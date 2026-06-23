"""
death_detector.py
─────────────────────────────────────────────────────────────────────────────
AI VOD Review — Death Detection Backend

Designed to run inside a QThread worker.  All progress is communicated via
the `ProcessingWorker` signals rather than print() calls, so the PySide6 GUI
can consume them without blocking the main thread.

Usage (from app.py):
    worker = ProcessingWorker(video_path)
    worker.log.connect(my_log_slot)
    worker.clip_ready.connect(my_clip_slot)
    worker.finished.connect(my_done_slot)
    worker.error.connect(my_error_slot)
    worker.start()
"""

import os
import re
import subprocess
import time

import cv2
import easyocr
import pytesseract
from PySide6.QtCore import QThread, Signal

# ================= TIME-OCR CONFIG =================
FALLBACK_SKIP_SECONDS = 45.0
BUY_ROUND_BUFFER_SECONDS = 30.0
MAX_PLAUSIBLE_SKIP_SECONDS = 300.0

# ================= DEATH CLIP CONFIG =================
CLIP_SECONDS_BEFORE = 15.0
CLIP_SECONDS_AFTER = 5.0
CLIP_TOTAL_DURATION = CLIP_SECONDS_BEFORE + CLIP_SECONDS_AFTER  # 20s
DEATH_CLIPS_DIR = r"D:\AI-Vod-Review\Death Clips"
FFMPEG_PATH = r"D:\AI-Vod-Review\FFmpeg\ffmpeg.exe"

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ================= RESOLUTION-AWARE CROP CONFIG =================
# Calibrated at 1280x720. The 1920x1080 coordinates supplied for this
# project (1600 / 250 / 1800 / 290) are this exact config scaled by 1.5x —
# which is also precisely the 1280->1920 / 720->1080 scale factor. That's
# not a coincidence: Valorant's HUD scales linearly with resolution on
# 16:9 displays. Rather than keep a two-entry lookup table (which would
# silently produce zero detections on anything else — 1440p, 4K, or a
# capture that's a few pixels off-spec), the same ratio derives crop
# coordinates for any 16:9 resolution from this one baseline. Non-16:9
# video is explicitly rejected rather than guessed at, since edge-anchored
# UI elements don't necessarily scale the same way as centered ones.
BASE_WIDTH, BASE_HEIGHT = 1280, 720
ASPECT_TOLERANCE = 0.02  # 2% slack for near-16:9 captures

BASE_KILL_CROP_CONFIG = {
    "LEFT": 1066,
    "START_UPPER": 166,
    "RIGHT": 1200,
    "START_LOWER": 193,
}

# Round-timer OCR crop (used for dynamic skip-ahead) — same HUD, same scaling.
BASE_TIME_CROP_CONFIG = {
    "LEFT": 615,
    "START_UPPER": 15,
    "RIGHT": 665,
    "START_LOWER": 45,
}

KNOWN_CALIBRATED_RESOLUTIONS = {(1280, 720), (1920, 1080)}


def resolve_crop_scale(width: int, height: int, log_fn) -> float | None:
    """
    Returns the scale factor to apply to the baseline (1280x720) crop
    coordinates for this video's resolution, or None if the aspect ratio
    is too far from 16:9 to trust the extrapolation.
    """
    if width <= 0 or height <= 0:
        return None

    base_aspect = BASE_WIDTH / BASE_HEIGHT
    actual_aspect = width / height
    if abs(actual_aspect - base_aspect) / base_aspect > ASPECT_TOLERANCE:
        return None

    scale = width / BASE_WIDTH

    if (width, height) in KNOWN_CALIBRATED_RESOLUTIONS:
        log_fn(f"Resolution {width}x{height} — using calibrated crop coordinates.")
    else:
        log_fn(
            f"Resolution {width}x{height} — no exact calibration on file; "
            f"extrapolating crop coordinates at {scale:.3f}x from the "
            f"1280x720 baseline. Double-check kill-banner OCR accuracy "
            f"if this is a non-standard capture resolution."
        )
    return scale


def _scale_crop_config(base_config: dict, scale: float) -> dict:
    return {k: round(v * scale) for k, v in base_config.items()}

# ─────────────────────────────────────────────────────────────────────────────
# Pure utility functions (no Qt dependency)
# ─────────────────────────────────────────────────────────────────────────────

def format_timestamp(seconds: float) -> str:
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{minutes:02d}:{secs:02d}"


def time_str_to_seconds(time_str: str | None) -> float | None:
    if not time_str:
        return None
    match = re.match(r"^(\d{1,2}):(\d{2})$", time_str)
    if not match:
        return None
    minutes, seconds = int(match.group(1)), int(match.group(2))
    if seconds >= 60:
        return None
    return minutes * 60 + seconds


def extract_time_from_frame(frame, time_crop: dict) -> str | None:
    crop = frame[
        time_crop["START_UPPER"]:time_crop["START_LOWER"],
        time_crop["LEFT"]:time_crop["RIGHT"],
    ]
    if crop.size == 0:
        return None

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=8, fy=8, interpolation=cv2.INTER_CUBIC)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    config = r"--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789:"
    text = pytesseract.image_to_string(thresh, config=config).strip()
    text = (
        text.replace("O", "0").replace("o", "0")
            .replace(" ", "").replace(".", ":")
    )

    match = re.search(r"(\d{1,2}):(\d{2})", text)
    if match:
        return f"{match.group(1)}:{match.group(2)}"
    return None


def levenshtein_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(curr[j - 1] + 1, prev[j] + 1,
                            prev[j - 1] + (0 if ca == cb else 1)))
        prev = curr
    return prev[-1]


TOKEN_SPLIT_PATTERN = re.compile(r"[^a-z0-9]+")
REFERENCE_KILL_WORD = "killed"
MAX_KILL_WORD_EDIT_DISTANCE = 2
MIN_TOKEN_LENGTH_TO_CONSIDER = 4


def is_genuine_kill_banner(cleaned_text: str) -> tuple[bool, str | None]:
    raw_tokens = [t for t in TOKEN_SPLIT_PATTERN.split(cleaned_text) if t]
    if not raw_tokens:
        return False, None

    for idx, token in enumerate(raw_tokens):
        if len(token) < MIN_TOKEN_LENGTH_TO_CONSIDER:
            continue

        prefix = token[:6] if len(token) > 6 else token
        distance = min(
            levenshtein_distance(token, REFERENCE_KILL_WORD),
            levenshtein_distance(prefix, REFERENCE_KILL_WORD),
        )

        if distance <= MAX_KILL_WORD_EDIT_DISTANCE:
            trailing = [t for t in raw_tokens[idx + 1:] if len(t) >= 2]
            if trailing:
                return True, f"kill-word '{token}' (edit-dist {distance}), trailing: {trailing}"
            else:
                return True, f"kill-word '{token}' (edit-dist {distance}) standalone"

    return False, None


# ================= PERFORMANCE / DEBUG SWITCHES =================
SLIDING_WINDOW_COUNT = 5
# Original calibration: a 20px step for a 27px-tall window at 720p. Scaled
# the same way the crop coordinates are (see resolve_crop_scale), so the 5
# sliding positions cover the same proportional vertical range at any
# resolution instead of under-shifting on 1080p+ video.
BASE_SLIDING_WINDOW_STEP = 20

# Off by default. The original code wrote a full-resolution PNG to disk on
# EVERY scanned frame (not just on a kill match) purely for debugging — that's
# a PNG-encode + disk write multiplied across every ~10s scan point in the
# whole video, which dominates runtime on long VODs with few kills. Flip to
# True only when you actually need to inspect what the scanner is looking at.
DEBUG_SAVE_FRAMES = False


# ─────────────────────────────────────────────────────────────────────────────
# Frame analysis (now log-callback based instead of print)
# ─────────────────────────────────────────────────────────────────────────────

def analyze_frame_with_sliding_window(
    frame,
    ocr_reader,
    start_upper: int,
    start_lower: int,
    left: int,
    right: int,
    debug_dir: str,
    timestamp: float,
    log_fn,
) -> bool:
    if DEBUG_SAVE_FRAMES:
        full_frame_path = os.path.join(debug_dir, f"time_{timestamp}s_FULL.png")
        cv2.imwrite(full_frame_path, frame)

    window_height = start_lower - start_upper
    step = max(1, round(window_height * BASE_SLIDING_WINDOW_STEP / 27))

    # Crop the small band covering all 5 sliding positions FIRST, then
    # color-convert only that sliver — instead of BGR2RGB-converting the
    # entire 720p/1080p/4K frame just to read a ~100px-tall strip out of it.
    band_upper = start_upper
    band_lower = start_lower + (SLIDING_WINDOW_COUNT - 1) * step
    band = frame[band_upper:band_lower, left:right]
    if band.size == 0:
        return False
    band_rgb = cv2.cvtColor(band, cv2.COLOR_BGR2RGB)

    for i in range(SLIDING_WINDOW_COUNT):
        rel_upper = i * step
        rel_lower = rel_upper + window_height
        crop_rgb = band_rgb[rel_upper:rel_lower, :]

        if crop_rgb.size == 0:
            continue

        # recognize() — NOT readtext() — skips EasyOCR's CRAFT text-detection
        # model entirely and runs only the (much cheaper) CRNN recognizer,
        # treating the whole crop as one text box. That's safe here because
        # we already know this crop IS the text region; readtext() would
        # re-run full detection on every single one of these 5 calls, which
        # is most of the per-scan cost on a CPU.
        result = ocr_reader.recognize(crop_rgb, detail=0)
        extracted = " ".join(result).strip()
        cleaned = extracted.lower()

        if extracted:
            log_fn(f"    [Crop {i+1}/{SLIDING_WINDOW_COUNT}]: \"{extracted}\"")

        is_match, debug_info = is_genuine_kill_banner(cleaned)
        if is_match:
            log_fn(f"    [Match!] {debug_info} in Crop {i+1}")
            return True

    return False


def extract_death_clip(
    video_path: str,
    death_time_sec: float,
    clip_number: int,
    output_dir: str,
    log_fn,
) -> str | None:
    """Returns the output path on success, None on failure."""
    start_time = max(0.0, death_time_sec - CLIP_SECONDS_BEFORE)
    output_path = os.path.join(output_dir, f"Death Clip #{clip_number}.mp4")

    command = [
        FFMPEG_PATH, "-y",
        "-ss", f"{start_time:.2f}",
        "-i", video_path,
        "-t", f"{CLIP_TOTAL_DURATION:.2f}",
        "-c", "copy",
        output_path,
    ]

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        log_fn("    [FAILED] ffmpeg not found — check FFMPEG_PATH")
        return None

    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-5:])
        log_fn(f"    [FAILED] ffmpeg exited {result.returncode}:\n{tail}")
        return None

    log_fn(f"    [Saved] {output_path}")
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# OCR engine setup
# ─────────────────────────────────────────────────────────────────────────────

def _create_ocr_reader(log_fn):
    """
    EasyOCR's own default is gpu=True — this codebase was explicitly
    overriding that to gpu=False. CPU-only inference for the CRAFT detector
    + CRNN recognizer is typically 5-10x slower than even a modest consumer
    GPU. Try GPU first; fall back to CPU on ANY failure (no CUDA-capable
    card, no CUDA build of torch installed, driver mismatch, OOM, etc.)
    rather than crashing the whole run over it.
    """
    gpu_available = False
    try:
        import torch
        gpu_available = torch.cuda.is_available()
    except Exception:
        gpu_available = False

    if gpu_available:
        try:
            reader = easyocr.Reader(["en"], gpu=True)
            log_fn("EasyOCR running on GPU.")
            return reader
        except Exception as exc:
            log_fn(f"GPU init failed ({exc}); falling back to CPU.")

    reader = easyocr.Reader(["en"], gpu=False)
    log_fn("EasyOCR running on CPU.")
    return reader


# ─────────────────────────────────────────────────────────────────────────────
# QThread Worker
# ─────────────────────────────────────────────────────────────────────────────

class ProcessingWorker(QThread):
    """
    Signals
    -------
    log(str)          — one line of human-readable progress text
    clip_ready(str)   — absolute path to a newly saved death clip
    progress(int,int) — (current_second, total_seconds) for a progress bar
    finished(int)     — total number of deaths found; emitted when done
    error(str)        — fatal error message; processing stopped
    """

    log = Signal(str)
    clip_ready = Signal(str)
    progress = Signal(int, int)
    finished = Signal(int)
    error = Signal(str)

    def __init__(self, video_path: str, crop_config: dict | None = None):
        super().__init__()
        self.video_path = video_path
        # None => auto-detect crop coordinates from the video's resolution.
        # Pass an explicit dict to bypass auto-detection entirely.
        self.crop_config_override = crop_config
        self._abort = False

    def abort(self) -> None:
        """Call from the main thread to request early termination."""
        self._abort = True

    # ── Internal helpers ──────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        self.log.emit(msg)

    # ── Main thread body ──────────────────────────────────────────────────

    def run(self) -> None:
        if not os.path.exists(self.video_path):
            self.error.emit(f"Video file not found: {self.video_path}")
            return

        # ── Setup dirs ────────────────────────────────────────────────────
        debug_dir = r"D:\AI-Vod-Review\debug_output"
        os.makedirs(debug_dir, exist_ok=True)
        os.makedirs(DEATH_CLIPS_DIR, exist_ok=True)

        # ── Init OCR ──────────────────────────────────────────────────────
        self._log("Initializing EasyOCR engine…")
        try:
            ocr_reader = _create_ocr_reader(self._log)
        except Exception as exc:
            self.error.emit(f"EasyOCR init failed: {exc}")
            return
        self._log("OCR engine ready.")

        # ── Open video ────────────────────────────────────────────────────
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            self.error.emit("OpenCV could not open the video file.")
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_sec = total_frames / fps if fps > 0 else 0.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        self._log(f"Video: {os.path.basename(self.video_path)}")
        self._log(f"Resolution: {width}×{height}")
        self._log(f"FPS: {fps:.2f}  |  Duration: {duration_sec:.1f}s")
        self._log("─" * 44)

        if self.crop_config_override is not None:
            cfg = self.crop_config_override
            time_cfg = BASE_TIME_CROP_CONFIG
            self._log("Using manually supplied crop configuration.")
        else:
            scale = resolve_crop_scale(width, height, self._log)
            if scale is None:
                cap.release()
                self.error.emit(
                    f"Unsupported resolution {width}x{height}: kill-banner "
                    f"OCR is calibrated for 16:9 video (e.g. 1280x720, "
                    f"1920x1080). Re-export/re-record at a 16:9 resolution, "
                    f"or pass an explicit crop_config to ProcessingWorker."
                )
                return
            cfg = _scale_crop_config(BASE_KILL_CROP_CONFIG, scale)
            time_cfg = _scale_crop_config(BASE_TIME_CROP_CONFIG, scale)

        kill_count = 0
        current_time_sec = 0.0
        t_start = time.time()

        while current_time_sec < duration_sec:
            if self._abort:
                self._log("⚠ Processing aborted by user.")
                break

            self.progress.emit(int(current_time_sec), int(duration_sec))

            frame_id = int(current_time_sec * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_id)
            ret, frame = cap.read()

            if not ret:
                break

            self._log(
                f"Scanning {format_timestamp(current_time_sec)} "
                f"(frame {frame_id})…"
            )

            found = analyze_frame_with_sliding_window(
                frame=frame,
                ocr_reader=ocr_reader,
                start_upper=cfg["START_UPPER"],
                start_lower=cfg["START_LOWER"],
                left=cfg["LEFT"],
                right=cfg["RIGHT"],
                debug_dir=debug_dir,
                timestamp=current_time_sec,
                log_fn=self._log,
            )

            if found:
                kill_count += 1
                ts = format_timestamp(current_time_sec)
                self._log(f"── DEATH #{kill_count} detected at {ts} ──")

                clip_path = extract_death_clip(
                    video_path=self.video_path,
                    death_time_sec=current_time_sec,
                    clip_number=kill_count,
                    output_dir=DEATH_CLIPS_DIR,
                    log_fn=self._log,
                )

                if clip_path:
                    self.clip_ready.emit(clip_path)

                # ── Dynamic skip ──────────────────────────────────────────
                time_str = extract_time_from_frame(frame, time_cfg)
                extracted_sec = time_str_to_seconds(time_str)

                if extracted_sec is not None and 0 < extracted_sec <= MAX_PLAUSIBLE_SKIP_SECONDS:
                    skip = extracted_sec + BUY_ROUND_BUFFER_SECONDS
                    src = f"OCR ({time_str}) + {BUY_ROUND_BUFFER_SECONDS:.0f}s buffer"
                else:
                    skip = FALLBACK_SKIP_SECONDS
                    src = f"fallback {FALLBACK_SKIP_SECONDS}s (OCR: {time_str!r})"

                self._log(f"    Time left: {time_str}  →  skip {skip:.1f}s [{src}]")
                self._log("─" * 44)
                current_time_sec += skip
            else:
                self._log("─" * 44)
                current_time_sec += 10.0

        cap.release()

        elapsed = time.time() - t_start
        em, es = int(elapsed) // 60, int(elapsed) % 60
        self._log("")
        self._log("═" * 44)
        self._log(f"DONE — {kill_count} death(s) found in {em}m {es}s")
        self._log("═" * 44)

        self.finished.emit(kill_count)