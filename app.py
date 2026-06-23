"""
app.py — AI VOD Review
Two views managed by a QStackedWidget:
  Page 0 — UPLOAD   : centered drag-drop zone
  Page 1 — RESULTS  : upload card (top, doubles as live scan-progress bar)
                       + master-detail clip viewer (bottom)

There is deliberately no separate "processing" page. The moment a file is
chosen, we jump straight to the results page and start the worker; clips
land in the master-detail list the instant ProcessingWorker emits
clip_ready, while a progress row at the top of the results page tracks the
scan. The processing page that used to gate this off has been removed.

Right-panel video player layout (this revision):
  header bar (clip title)
  video
  scrub bar (seek slider + time labels)
  control row (play/pause, volume+mute, speed dropdown, open-folder)
"""

import os
import sys

os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui     import QFont, QPalette, QColor, QDragEnterEvent, QDropEvent
from PySide6.QtMultimedia          import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets   import QVideoWidget
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QSlider,
    QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from death_detector import ProcessingWorker

VIDEO_FILTER = (
    "Video Files (*.mp4 *.avi *.mov *.mkv *.wmv *.flv "
    "*.webm *.m4v *.mpeg *.mpg *.3gp *.ts *.mts *.m2ts *.vob)"
    ";;All Files (*)"
)

# ── Colours ───────────────────────────────────────────────────────────────────
BG        = "#0e0e1a"
CARD      = "#16162a"
CARD2     = "#1c1c30"
BORDER    = "#252540"
BORDER2   = "#2e2e50"
PURPLE    = "#7c3aed"
PURPLE_H  = "#9333ea"
BLUE_BG   = "#1e3d6b"
BLUE_H    = "#2a5490"
BLUE_TXT  = "#7eb8f7"
GREEN     = "#22c55e"
GREEN_BG  = "#0d2b1a"
GREEN_BDR = "#1a4d28"
RED       = "#ef4444"
RED_BG    = "#2b0d0d"
RED_BDR   = "#4d1a1a"
VIOLET    = "#a78bfa"
VIOLET_BG = "#1a1230"
VIOLET_BDR= "#3d2080"
TXT       = "#e2e2f0"
DIM       = "#555570"
MUTED     = "#383855"
DIS_BG    = "#1e1e35"
DIS_TXT   = "#3d3d55"
DIS_BDR   = "#2a2a40"

SS = f"""
QMainWindow, QWidget {{
    background-color: {BG};
    color: {TXT};
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 13px;
}}

/* ── scrollbars ── */
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{
    background: {CARD}; width: 6px; margin: 0; border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER2}; border-radius: 3px; min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: {CARD}; height: 6px; margin: 0; border-radius: 3px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER2}; border-radius: 3px; min-width: 20px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ── frames ── */
QFrame#card     {{ background: {CARD};  border: 1px solid {BORDER};  border-radius: 14px; }}
QFrame#card2    {{ background: {CARD2}; border: 1px solid {BORDER};  border-radius: 10px; }}
QFrame#divider  {{ background: {BORDER}; max-height: 1px; border: none; }}
QFrame#drop_zone {{
    background: transparent;
    border: 2px dashed {BORDER2};
    border-radius: 16px;
}}
QFrame#drop_zone_hover {{
    background: {VIOLET_BG};
    border: 2px dashed {PURPLE};
    border-radius: 16px;
}}
QFrame#left_panel  {{ background: {CARD};  border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#right_panel {{ background: #0a0a14; border: 1px solid {BORDER}; border-radius: 12px; }}

/* ── labels ── */
QLabel#title       {{ font-size: 22px; font-weight: 700; color: #ffffff; }}
QLabel#subtitle    {{ font-size: 12px; color: {DIM}; }}
QLabel#drop_title  {{ font-size: 18px; font-weight: 600; color: {TXT}; }}
QLabel#drop_sub    {{ font-size: 13px; color: {DIM}; }}
QLabel#drop_icon   {{ font-size: 48px; color: {VIOLET}; }}
QLabel#file_name   {{ font-size: 13px; font-weight: 600; color: {TXT}; }}
QLabel#file_meta   {{ font-size: 11px; color: {DIM}; }}
QLabel#hint        {{ font-size: 10px; color: {MUTED}; font-style: italic; }}
QLabel#section_hdr {{ font-size: 10px; font-weight: 700; letter-spacing: 2px; color: {DIM}; }}
QLabel#proc_status {{ font-size: 11px; font-weight: 600; color: {VIOLET}; }}
QLabel#clip_item   {{
    font-size: 11px; color: {DIM};
    padding: 8px 12px;
    border-radius: 6px;
    border: 1px solid transparent;
}}
QLabel#clip_item_selected {{
    font-size: 11px; color: {TXT};
    padding: 8px 12px;
    border-radius: 6px;
    background: {VIOLET_BG};
    border: 1px solid {VIOLET_BDR};
}}
QLabel#no_clips    {{ font-size: 12px; color: {MUTED}; font-style: italic; }}
QLabel#no_select   {{ font-size: 13px; color: {MUTED}; font-style: italic; }}
QLabel#time_lbl    {{ font-size: 11px; color: {DIM}; font-family: "Consolas", monospace; }}
QLabel#detail_header {{ font-size: 12px; font-weight: 600; color: {TXT}; }}

/* ── badges ── */
QLabel#badge_idle        {{ background:{BG};         color:{DIM};    border:1px solid {BORDER};  border-radius:6px; padding:4px 12px; font-size:11px; font-weight:700; }}
QLabel#badge_ready       {{ background:{GREEN_BG};   color:{GREEN};  border:1px solid {GREEN_BDR};  border-radius:6px; padding:4px 12px; font-size:11px; font-weight:700; }}
QLabel#badge_processing  {{ background:{VIOLET_BG};  color:{VIOLET}; border:1px solid {VIOLET_BDR}; border-radius:6px; padding:4px 12px; font-size:11px; font-weight:700; }}
QLabel#badge_done        {{ background:{GREEN_BG};   color:{GREEN};  border:1px solid {GREEN_BDR};  border-radius:6px; padding:4px 12px; font-size:11px; font-weight:700; }}
QLabel#badge_error       {{ background:{RED_BG};     color:{RED};    border:1px solid {RED_BDR};    border-radius:6px; padding:4px 12px; font-size:11px; font-weight:700; }}

/* ── buttons ── */
QPushButton#btn_browse {{
    background:{BLUE_BG}; color:{BLUE_TXT};
    border:1px solid #2a5490; border-radius:8px;
    padding:0 28px; font-size:13px; font-weight:600;
}}
QPushButton#btn_browse:hover  {{ background:{BLUE_H}; color:#aad4ff; border-color:#5b9bd5; }}
QPushButton#btn_browse:pressed {{ background:#162d52; color:{BLUE_TXT}; }}

QPushButton#btn_cancel {{
    background:{RED_BG}; color:{RED};
    border:1px solid {RED_BDR}; border-radius:7px;
    padding:0 18px; font-size:12px; font-weight:600;
}}
QPushButton#btn_cancel:hover  {{ background:#3d1212; color:{RED}; border-color:{RED}; }}
QPushButton#btn_cancel:pressed {{ background:#1e0808; color:{RED}; }}
QPushButton#btn_cancel:disabled {{
    background:{DIS_BG}; color:{DIS_TXT}; border:1px solid {DIS_BDR};
}}

QPushButton#btn_new {{
    background:{BLUE_BG}; color:{BLUE_TXT};
    border:1px solid #2a5490; border-radius:7px;
    padding:0 18px; font-size:12px; font-weight:600;
}}
QPushButton#btn_new:hover  {{ background:{BLUE_H}; color:#aad4ff; }}
QPushButton#btn_new:pressed {{ background:#162d52; color:{BLUE_TXT}; }}

QPushButton#btn_play {{
    background:{BLUE_BG}; color:{BLUE_TXT};
    border:1px solid #2a5490; border-radius:6px;
    padding:0 18px; font-size:12px; font-weight:600;
}}
QPushButton#btn_play:hover   {{ background:{BLUE_H}; color:#aad4ff; }}
QPushButton#btn_play:pressed {{ background:#162d52; color:{BLUE_TXT}; }}
QPushButton#btn_play:checked {{
    background:#2563eb; color:#ffffff; border-color:#3b82f6;
}}

QPushButton#btn_mute {{
    background:transparent; color:{DIM};
    border:1px solid {BORDER2}; border-radius:6px;
    padding:0 10px; font-size:12px; font-weight:600;
}}
QPushButton#btn_mute:hover {{ background:{CARD2}; color:{TXT}; border-color:{DIM}; }}
QPushButton#btn_mute:checked {{
    background:{RED_BG}; color:{RED}; border-color:{RED_BDR};
}}

QPushButton#btn_open {{
    background:transparent; color:{DIM};
    border:1px solid {BORDER2}; border-radius:6px;
    padding:0 14px; font-size:11px; font-weight:600;
}}
QPushButton#btn_open:hover {{ background:{CARD2}; color:{TXT}; border-color:{DIM}; }}

/* ── progress bar ── */
QProgressBar {{
    background:#1a1a2e; border:1px solid {BORDER};
    border-radius:5px; max-height:6px;
}}
QProgressBar::chunk {{ background:{PURPLE}; border-radius:5px; }}

/* ── sliders (seek bar + volume) ── */
QSlider#seek_slider::groove:horizontal {{
    height: 4px; background: {BORDER2}; border-radius: 2px;
}}
QSlider#seek_slider::sub-page:horizontal {{
    background: {PURPLE}; border-radius: 2px;
}}
QSlider#seek_slider::handle:horizontal {{
    width: 12px; height: 12px; margin: -4px 0;
    background: #ffffff; border-radius: 6px;
}}
QSlider#seek_slider::handle:horizontal:hover {{ background: {VIOLET}; }}

QSlider#volume_slider::groove:horizontal {{
    height: 4px; background: {BORDER2}; border-radius: 2px;
}}
QSlider#volume_slider::sub-page:horizontal {{
    background: {BLUE_TXT}; border-radius: 2px;
}}
QSlider#volume_slider::handle:horizontal {{
    width: 10px; height: 10px; margin: -3px 0;
    background: #ffffff; border-radius: 5px;
}}

/* ── speed dropdown ── */
QComboBox#speed_combo {{
    background: transparent; color: {DIM};
    border: 1px solid {BORDER2}; border-radius: 6px;
    padding: 0 10px; font-size: 11px; font-weight: 600;
    min-height: 28px;
}}
QComboBox#speed_combo:hover {{ background: {CARD2}; color: {TXT}; border-color: {DIM}; }}
QComboBox#speed_combo::drop-down {{ border: none; width: 18px; }}
QComboBox#speed_combo QAbstractItemView {{
    background: {CARD2}; color: {TXT};
    border: 1px solid {BORDER2};
    selection-background-color: {VIOLET_BG};
    selection-color: {TXT};
}}
"""

BTN_H = 42
BTN_H_SM = 34


# ─────────────────────────────────────────────────────────────────────────────
# Aspect-ratio-locked video container
# ─────────────────────────────────────────────────────────────────────────────

# Death clips come from 1280x720 source footage via ffmpeg stream-copy
# (-c copy), which doesn't alter resolution -- so clips are 16:9. This is
# hardcoded rather than probed per-clip; if source footage at a different
# aspect ratio is ever introduced, this constant (and the math below) would
# need to change too, or letterboxing reappears along a different axis.


# ─────────────────────────────────────────────────────────────────────────────
# Drag-and-drop upload zone  (Page 0)
# ─────────────────────────────────────────────────────────────────────────────

class DropZone(QFrame):
    """Centered dashed-border drag-drop area."""

    def __init__(self, on_file_chosen, parent=None):
        super().__init__(parent)
        self.on_file_chosen = on_file_chosen
        self.setObjectName("drop_zone")
        self.setAcceptDrops(True)
        self.setMinimumSize(480, 280)
        self.setMaximumSize(600, 340)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(10)
        lay.setContentsMargins(40, 40, 40, 40)

        icon = QLabel("⬆")
        icon.setObjectName("drop_icon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Drag your video here")
        title.setObjectName("drop_title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub = QLabel("or")
        sub.setObjectName("drop_sub")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.browse_btn = QPushButton("Browse files")
        self.browse_btn.setObjectName("btn_browse")
        self.browse_btn.setMinimumHeight(BTN_H)
        self.browse_btn.setMinimumWidth(160)
        self.browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_btn.clicked.connect(self._browse)

        hint = QLabel("mp4 · avi · mov · mkv · wmv · flv · webm · m4v … and more")
        hint.setObjectName("hint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lay.addWidget(icon)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addWidget(self.browse_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        lay.addSpacing(8)
        lay.addWidget(hint)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select a Video File", "", VIDEO_FILTER)
        if path:
            self.on_file_chosen(path)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.setObjectName("drop_zone_hover")
            self.style().unpolish(self); self.style().polish(self)

    def dragLeaveEvent(self, e):
        self.setObjectName("drop_zone")
        self.style().unpolish(self); self.style().polish(self)

    def dropEvent(self, e: QDropEvent):
        self.setObjectName("drop_zone")
        self.style().unpolish(self); self.style().polish(self)
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isfile(path):
                self.on_file_chosen(path)


# ─────────────────────────────────────────────────────────────────────────────
# Clip list item  (left panel in Page 1)
# ─────────────────────────────────────────────────────────────────────────────

class ClipListItem(QWidget):
    def __init__(self, number: int, path: str, on_select, parent=None):
        super().__init__(parent)
        self.number = number
        self.path   = path
        self.on_select = on_select
        self._selected = False

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.lbl = QLabel(f"  #{number}   Death Clip #{number}.mp4")
        self.lbl.setObjectName("clip_item")
        self.lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.lbl)

    def set_selected(self, sel: bool):
        self._selected = sel
        self.lbl.setObjectName("clip_item_selected" if sel else "clip_item")
        self.lbl.style().unpolish(self.lbl)
        self.lbl.style().polish(self.lbl)

    def mousePressEvent(self, e):
        self.on_select(self)


# ─────────────────────────────────────────────────────────────────────────────
# Media player controls — seek bar, play/pause, volume, speed, open-folder
# ─────────────────────────────────────────────────────────────────────────────

SPEED_PRESETS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]


def _format_ms(ms: int) -> str:
    """Formats a millisecond duration as M:SS (or H:MM:SS for long clips)."""
    if ms <= 0:
        return "0:00"
    total_seconds = ms // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


class MediaPlayerControls(QWidget):
    """
    Self-contained playback control block: seek bar with time labels,
    play/pause, volume + mute, speed dropdown, and open-folder.

    Wired to an externally-owned QMediaPlayer/QAudioOutput pair (passed in,
    not created here) so MainWindow keeps a single shared player instance
    across clip selections, same as before this change.

    Seek-drag handling: QSlider.valueChanged fires both from user drags AND
    from our own setValue() calls driven by positionChanged. Without a guard,
    those two paths fight -- the player keeps snapping the handle back to the
    real playback position while the user is mid-drag. sliderPressed sets a
    flag that suppresses positionChanged-driven updates; sliderReleased clears
    it and commits the seek via setPosition().
    """

    def __init__(self, player: QMediaPlayer, audio: QAudioOutput, on_open_folder, parent=None):
        super().__init__(parent)
        self._player = player
        self._audio = audio
        self._on_open_folder = on_open_folder
        self._user_is_seeking = False
        self._pre_mute_volume = audio.volume()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 10, 16, 10)
        outer.setSpacing(8)

        # ── Seek row: current time / slider / total time ───────────────
        seek_row = QHBoxLayout()
        seek_row.setSpacing(10)

        self.time_current_lbl = QLabel("0:00")
        self.time_current_lbl.setObjectName("time_lbl")
        self.time_current_lbl.setFixedWidth(40)
        self.time_current_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setObjectName("seek_slider")
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderPressed.connect(self._on_seek_pressed)
        self.seek_slider.sliderReleased.connect(self._on_seek_released)
        self.seek_slider.valueChanged.connect(self._on_seek_value_changed)

        self.time_total_lbl = QLabel("0:00")
        self.time_total_lbl.setObjectName("time_lbl")
        self.time_total_lbl.setFixedWidth(40)

        seek_row.addWidget(self.time_current_lbl)
        seek_row.addWidget(self.seek_slider, stretch=1)
        seek_row.addWidget(self.time_total_lbl)
        outer.addLayout(seek_row)

        # ── Button row: play / volume+mute / speed / open-folder ───────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.btn_play = QPushButton("▶  Play")
        self.btn_play.setObjectName("btn_play")
        self.btn_play.setCheckable(True)
        self.btn_play.setMinimumHeight(BTN_H_SM)
        self.btn_play.setMinimumWidth(100)
        self.btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_play.setEnabled(False)

        self.btn_mute = QPushButton("🔊")
        self.btn_mute.setObjectName("btn_mute")
        self.btn_mute.setCheckable(True)
        self.btn_mute.setMinimumHeight(BTN_H_SM)
        self.btn_mute.setFixedWidth(36)
        self.btn_mute.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mute.clicked.connect(self._toggle_mute)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setObjectName("volume_slider")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(int(audio.volume() * 100))
        self.volume_slider.setFixedWidth(80)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)

        self.speed_combo = QComboBox()
        self.speed_combo.setObjectName("speed_combo")
        for rate in SPEED_PRESETS:
            label = f"{rate:g}x"
            self.speed_combo.addItem(label, rate)
        self.speed_combo.setCurrentIndex(SPEED_PRESETS.index(1.0))
        self.speed_combo.currentIndexChanged.connect(self._on_speed_changed)

        self.btn_open_folder = QPushButton("Open folder")
        self.btn_open_folder.setObjectName("btn_open")
        self.btn_open_folder.setMinimumHeight(BTN_H_SM)
        self.btn_open_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_folder.clicked.connect(self._on_open_folder)
        self.btn_open_folder.setEnabled(False)

        btn_row.addWidget(self.btn_play)
        btn_row.addSpacing(4)
        btn_row.addWidget(self.btn_mute)
        btn_row.addWidget(self.volume_slider)
        btn_row.addStretch(1)
        btn_row.addWidget(self.speed_combo)
        btn_row.addWidget(self.btn_open_folder)
        outer.addLayout(btn_row)

        # ── Wire to the player ───────────────────────────────────────────
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_play_state)

    # ── Seek bar ─────────────────────────────────────────────────────────

    def _on_seek_pressed(self):
        self._user_is_seeking = True

    def _on_seek_released(self):
        self._user_is_seeking = False
        self._player.setPosition(self.seek_slider.value())

    def _on_seek_value_changed(self, value: int):
        # Update the current-time label live while dragging, even before
        # release, so the user sees where they're about to land.
        self.time_current_lbl.setText(_format_ms(value))

    def _on_position_changed(self, position_ms: int):
        if self._user_is_seeking:
            return  # don't fight the user's drag
        self.seek_slider.setValue(position_ms)
        self.time_current_lbl.setText(_format_ms(position_ms))

    def _on_duration_changed(self, duration_ms: int):
        self.seek_slider.setRange(0, duration_ms)
        self.time_total_lbl.setText(_format_ms(duration_ms))

    # ── Play / pause ─────────────────────────────────────────────────────

    def _toggle_play(self, checked: bool):
        if checked:
            self._player.play()
        else:
            self._player.pause()

    def _on_play_state(self, state: QMediaPlayer.PlaybackState):
        playing = (state == QMediaPlayer.PlaybackState.PlayingState)
        self.btn_play.setChecked(playing)
        self.btn_play.setText("⏸  Pause" if playing else "▶  Play")

    # ── Volume / mute ────────────────────────────────────────────────────

    def _on_volume_changed(self, value: int):
        self._audio.setVolume(value / 100)
        if value > 0 and self.btn_mute.isChecked():
            self.btn_mute.setChecked(False)
            self.btn_mute.setText("🔊")

    def _toggle_mute(self, checked: bool):
        if checked:
            self._pre_mute_volume = self._audio.volume()
            self._audio.setVolume(0)
            self.btn_mute.setText("🔇")
        else:
            restored = self._pre_mute_volume if self._pre_mute_volume > 0 else 0.5
            self._audio.setVolume(restored)
            self.volume_slider.setValue(int(restored * 100))
            self.btn_mute.setText("🔊")

    # ── Speed ────────────────────────────────────────────────────────────

    def _on_speed_changed(self, index: int):
        rate = self.speed_combo.itemData(index)
        self._player.setPlaybackRate(rate)

    # ── External API used by MainWindow ─────────────────────────────────

    def on_clip_loaded(self):
        """Call right after a new clip is set as the player source."""
        self.btn_play.setEnabled(True)
        self.btn_open_folder.setEnabled(True)
        # Re-apply the current speed selection to the freshly-loaded source --
        # QMediaPlayer resets playbackRate to 1.0 on setSource for some backends.
        rate = self.speed_combo.itemData(self.speed_combo.currentIndex())
        self._player.setPlaybackRate(rate)

    def reset(self):
        """Call when clearing clips / returning to the upload page."""
        self.seek_slider.setRange(0, 0)
        self.time_current_lbl.setText("0:00")
        self.time_total_lbl.setText("0:00")
        self.btn_play.setChecked(False)
        self.btn_play.setText("▶  Play")
        self.btn_play.setEnabled(False)
        self.btn_open_folder.setEnabled(False)


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):

    # pages — processing no longer has its own page; it happens on PAGE_RESULTS
    PAGE_UPLOAD  = 0
    PAGE_RESULTS = 1

    def __init__(self):
        super().__init__()
        self.selected_path: str | None = None
        self.worker:  ProcessingWorker | None = None
        self._workers: list[ProcessingWorker] = []
        self._clip_paths: list[str] = []
        self._reviewed_items = []
        self._clip_items: list[ClipListItem] = []
        self._selected_item: ClipListItem | None = None
        self._is_scanning = False

        # single shared player for the right-hand detail panel
        self._audio  = QAudioOutput()
        self._audio.setVolume(1.0)
        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._audio)

        self.setWindowTitle("AI VOD Review")
        self.setMinimumSize(960, 640)
        self.resize(1200, 780)
        self._build_ui()
        self.setStyleSheet(SS)

    # ─────────────────────────────────────────────────────────────────────────
    # UI construction
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root_w = QWidget()
        self.setCentralWidget(root_w)
        root = QVBoxLayout(root_w)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header bar ────────────────────────────────────────────────────
        hdr_w = QWidget()
        hdr_w.setFixedHeight(64)
        hdr_w.setStyleSheet(f"background:{CARD}; border-bottom: 1px solid {BORDER};")
        hdr = QHBoxLayout(hdr_w)
        hdr.setContentsMargins(36, 0, 36, 0)


        self.badge = QLabel("● Idle"); self.badge.setObjectName("badge_idle")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


        # ── Stacked pages ─────────────────────────────────────────────────
        self.stack = QStackedWidget()
        root.addWidget(self.stack, stretch=1)

        self.stack.addWidget(self._build_upload_page())   # 0
        self.stack.addWidget(self._build_results_page())  # 1

        self.stack.setCurrentIndex(self.PAGE_UPLOAD)

    # ── Page 0 : Upload ───────────────────────────────────────────────────

    def _build_upload_page(self) -> QWidget:
        page = QWidget()
        lay  = QVBoxLayout(page)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setContentsMargins(40, 40, 40, 40)

        self.drop_zone = DropZone(self._file_chosen)
        lay.addWidget(self.drop_zone, alignment=Qt.AlignmentFlag.AlignCenter)
        return page

    # ── Page 1 : Results (also hosts the live scan-progress row) ──────────

    def _build_results_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)

        # ── Top card: file info + action button (Cancel ⟷ New video) ────
        # plus a collapsible scan-progress row, visible only while a
        # worker is actively running.
        top_card = QFrame(); top_card.setObjectName("card2")
        top_card.setMaximumHeight(78)
        top_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed
        )
        tc = QVBoxLayout(top_card)
        tc.setContentsMargins(16, 10, 16, 10)
        tc.setSpacing(6)

        row1 = QHBoxLayout(); row1.setSpacing(16)

        self.res_file_lbl  = QLabel("—"); self.res_file_lbl.setObjectName("file_name")
        self.res_meta_lbl  = QLabel(""); self.res_meta_lbl.setObjectName("file_meta")
        self.res_file_lbl.setStyleSheet("""
        font-size: 13px;
        font-weight: 600;
        """)

        self.res_meta_lbl.setStyleSheet("""
            font-size: 11px;
            color: #555570;
        """)
        file_col = QVBoxLayout(); file_col.setSpacing(0)
        file_col.addWidget(self.res_file_lbl)
        file_col.addWidget(self.res_meta_lbl)

        self.btn_new = QPushButton("+ New video")
        self.btn_new.setObjectName("btn_new")
        self.btn_new.setFixedHeight(30)
        self.btn_new.setFixedWidth(130)
        self.btn_new.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_new.clicked.connect(self._go_to_upload)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("btn_cancel")
        self.btn_cancel.setFixedHeight(30)
        self.btn_cancel.setFixedWidth(100)
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_cancel.setVisible(False)   # only shown while scanning

        row1.addLayout(file_col, stretch=1)
        row1.addWidget(self.btn_new)
        row1.addWidget(self.btn_cancel)
        tc.addLayout(row1)

        # scan row — status text, progress bar, live death count
        self.scan_row = QWidget()
        scan_lay = QHBoxLayout(self.scan_row)
        scan_lay.setContentsMargins(0, 0, 0, 0)
        scan_lay.setSpacing(12)

        self.proc_status_lbl = QLabel("Scanning… 0%")
        self.proc_status_lbl.setObjectName("proc_status")
        self.proc_status_lbl.setFixedWidth(110)

        self.proc_progress = QProgressBar()
        self.proc_progress.setRange(0, 100)
        self.proc_progress.setValue(0)
        self.proc_progress.setTextVisible(False)

        self.proc_clips_lbl = QLabel("Deaths found: 0")
        self.proc_clips_lbl.setObjectName("file_meta")

        scan_lay.addWidget(self.proc_status_lbl)
        scan_lay.addWidget(self.proc_progress, stretch=1)
        scan_lay.addWidget(self.proc_clips_lbl)

        self.scan_row.setVisible(False)
        tc.addWidget(self.scan_row)

        outer.addWidget(top_card, stretch=0)

        # ── Master-detail ──────────────────────────────────────────────
        md = QHBoxLayout(); md.setSpacing(16)

        # Left panel — clip list (this IS the live view; clips appear here
        # the instant clip_ready fires, while scanning is still in progress)
        left = QFrame(); left.setObjectName("left_panel")
        left.setFixedWidth(260)
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(0)

        list_hdr = QLabel("  DEATH CLIPS")
        list_hdr.setObjectName("section_hdr")
        list_hdr.setFixedHeight(36)
        list_hdr.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        list_hdr.setStyleSheet(f"padding-left:14px; border-bottom:1px solid {BORDER}; color:{DIM};")
        left_lay.addWidget(list_hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(8, 8, 8, 8)
        self.list_layout.setSpacing(4)
        self.list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.no_clips_lbl = QLabel("No clips yet")
        self.no_clips_lbl.setObjectName("no_clips")
        self.no_clips_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.list_layout.addWidget(self.no_clips_lbl)

        scroll.setWidget(self.list_container)
        left_lay.addWidget(scroll, stretch=1)
        md.addWidget(left)

        # Right panel — slim header / video / scrub bar / controls
        # (standard media-player layout, replacing the old single packed
        # control row that lived directly under the video)
        right = QFrame(); right.setObjectName("right_panel")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)


        # Video output — must be native for WMF on Windows.
        # Wrapped in AspectRatioVideoContainer so the player area shrinks to
        # the clip's real proportions instead of letterboxing with black bars.
# Video output — fill available space with no side gaps.

        self.video_widget = QVideoWidget()

        self.video_widget.setAttribute(
            Qt.WidgetAttribute.WA_NativeWindow,
            True
        )

        self.video_widget.setStyleSheet("""
            background: #000000;
            border: none;
        """)

        self.video_widget.setAspectRatioMode(
            Qt.AspectRatioMode.KeepAspectRatioByExpanding
        )

        self._player.setVideoOutput(self.video_widget)

        right_lay.addWidget(self.video_widget, stretch=1)

        # Scrub bar + play/volume/speed/open-folder, all in one control block
        controls_bar = QWidget()
        controls_bar.setStyleSheet(f"background:{CARD}; border-top:1px solid {BORDER};")
        self.player_controls = MediaPlayerControls(
            self._player, self._audio, self._open_folder
        )
        cb_lay = QVBoxLayout(controls_bar)
        cb_lay.setContentsMargins(0, 0, 0, 0)
        cb_lay.addWidget(self.player_controls)
        right_lay.addWidget(controls_bar)

        md.addWidget(right, stretch=1)
        outer.addLayout(md, stretch=10)
        return page

    # ─────────────────────────────────────────────────────────────────────────
    # Badge helper
    # ─────────────────────────────────────────────────────────────────────────

    def _set_badge(self, obj: str, txt: str):
        self.badge.setObjectName(obj)
        self.badge.setText(txt)
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)

    # ─────────────────────────────────────────────────────────────────────────
    # Upload page logic
    # ─────────────────────────────────────────────────────────────────────────

    def _file_chosen(self, path: str):
        self.selected_path = path
        name   = os.path.basename(path)
        raw_mb = os.path.getsize(path) / (1024 * 1024)
        size   = f"{raw_mb:.2f} MB" if raw_mb < 1024 else f"{raw_mb / 1024:.2f} GB"

        self.res_file_lbl.setText(name)
        self.res_meta_lbl.setText(f"Size: {size}")

        self._start_processing()

    def _go_to_upload(self):
        """Return to upload page (from results). Only reachable when idle —
        btn_new is hidden for the whole duration of an active scan."""
        self._player.stop()
        self._clear_clips()
        self._set_badge("badge_idle", "● Idle")
        self.stack.setCurrentIndex(self.PAGE_UPLOAD)

    # ─────────────────────────────────────────────────────────────────────────
    # Processing — runs ON the results page, no dedicated page for it
    # ─────────────────────────────────────────────────────────────────────────

    def _start_processing(self):
        self._clear_clips()

        # jump straight to results — clips will populate live as they're found
        self.stack.setCurrentIndex(self.PAGE_RESULTS)
        self._set_badge("badge_processing", "● Processing…")

        self._is_scanning = True
        self.scan_row.setVisible(True)
        self.proc_progress.setValue(0)
        self.proc_status_lbl.setText("Scanning… 0%")
        self.proc_clips_lbl.setText("Deaths found: 0")
        self.btn_cancel.setVisible(True)
        self.btn_cancel.setEnabled(True)
        self.btn_new.setVisible(False)

        self.worker = ProcessingWorker(self.selected_path)
        self._workers.append(self.worker)

        self.worker.clip_ready.connect(self._on_clip_ready)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.finished.connect(lambda _: self._retire(self.worker))
        self.worker.error.connect(lambda _: self._retire(self.worker))
        self.worker.start()

    def _on_cancel(self):
        """First press = confirmation dialog; second press handled by dialog."""
        reply = QMessageBox.question(
            self,
            "Cancel processing?",
            "Are you sure you want to stop processing?\n"
            "Any clips extracted so far will be kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.btn_cancel.setEnabled(False)
            if self.worker and self.worker.isRunning():
                self.worker.abort()
            self._finalise("badge_done",
                            f"● Stopped — {len(self._clip_paths)} clip(s)")

    def _on_clip_ready(self, path: str):
        self._clip_paths.append(path)
        n = len(self._clip_paths)
        self.proc_clips_lbl.setText(f"Deaths found: {n}")
        self._add_clip_to_list(path, n)

        # Auto-load the very first clip into the player so the user isn't
        # staring at an empty panel while the rest of the video is scanned.
        # Later clips never steal focus — only fires when nothing is selected.
        if self._selected_item is None:
            self._select_item(self._clip_items[-1])

    def _on_progress(self, cur: int, total: int):
        if total > 0:
            pct = int(cur / total * 100)
            self.proc_progress.setValue(pct)
            self.proc_status_lbl.setText(f"Scanning… {pct}%")

    def _on_finished(self, count: int):
        self._finalise("badge_done", f"● Done — {count} death(s)")

    def _on_error(self, msg: str):
        self._finalise("badge_error", "● Error")

    def _retire(self, w):
        if w in self._workers:
            w.wait()
            self._workers.remove(w)
        if self.worker is w:
            self.worker = None

    def _finalise(self, badge: str, text: str):
        self._is_scanning = False
        self._set_badge(badge, text)
        self.scan_row.setVisible(False)
        self.btn_cancel.setVisible(False)
        self.btn_new.setVisible(True)
        # Fallback: if nothing ever got auto-selected (e.g. zero clips found
        # until right at the end — shouldn't happen given the auto-select in
        # _on_clip_ready, but cheap to guard) pick the first one now.
        if self._selected_item is None and self._clip_items:
            self._select_item(self._clip_items[0])

    # ─────────────────────────────────────────────────────────────────────────
    # Clip list (left panel, Page 1)
    # ─────────────────────────────────────────────────────────────────────────

    def _add_clip_to_list(self, path: str, n: int):
        if self.no_clips_lbl:
            self.list_layout.removeWidget(self.no_clips_lbl)
            self.no_clips_lbl.deleteLater()
            self.no_clips_lbl = None

        item = ClipListItem(n, path, self._select_item)
        self._clip_items.append(item)
        self.list_layout.addWidget(item)

    def _select_item(self, item: ClipListItem):
        if self._selected_item:
            self._selected_item.set_selected(False)
        self._selected_item = item
        item.set_selected(True)

        # Load into shared player
        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(item.path))
        self._player.play()
        self.player_controls.on_clip_loaded()


    def _clear_clips(self):
        self._player.stop()
        self._clip_paths.clear()
        self._clip_items.clear()
        self._selected_item = None

        while self.list_layout.count():
            it = self.list_layout.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()

        self.no_clips_lbl = QLabel("No clips yet")
        self.no_clips_lbl.setObjectName("no_clips")
        self.no_clips_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.list_layout.addWidget(self.no_clips_lbl)


        self.player_controls.reset()

    # ─────────────────────────────────────────────────────────────────────────
    # Open folder
    # ─────────────────────────────────────────────────────────────────────────

    def _open_folder(self):
        if not self._selected_item:
            return
        folder = os.path.dirname(self._selected_item.path)
        if sys.platform == "win32":
            os.startfile(folder)
        elif sys.platform == "darwin":
            import subprocess; subprocess.Popen(["open", folder])
        else:
            import subprocess; subprocess.Popen(["xdg-open", folder])

    # ─────────────────────────────────────────────────────────────────────────
    # Close
    # ─────────────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._player.stop()
        for w in list(self._workers):
            if w.isRunning():
                w.abort()
                w.wait(5000)
        super().closeEvent(event)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 11))

    pal = app.palette()
    pal.setColor(QPalette.ColorRole.ButtonText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.WindowText, QColor("#e2e2f0"))
    pal.setColor(QPalette.ColorRole.Text,       QColor("#e2e2f0"))
    pal.setColor(QPalette.ColorRole.Window,     QColor("#0e0e1a"))
    pal.setColor(QPalette.ColorRole.Button,     QColor("#1e1e35"))
    app.setPalette(pal)

    w = MainWindow()
    w.show()
    sys.exit(app.exec())
