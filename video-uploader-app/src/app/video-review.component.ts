import {
  Component, Input, ViewChild, ElementRef, AfterViewInit,
  OnDestroy, ChangeDetectorRef, HostListener
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { SafeResourceUrl } from '@angular/platform-browser';

type DrawTool = 'pen' | 'circle' | 'arrow' | 'square';
interface DrawPoint { x: number; y: number; }
interface Stroke { tool: DrawTool; points: DrawPoint[]; }
interface Note { id: string; timestamp: number; text: string; }
interface Flag { id: string; timestamp: number; }

@Component({
  selector: 'app-video-review',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './video-review.component.html',
  styleUrl: './video-review.component.css'
})
export class VideoReviewComponent implements AfterViewInit, OnDestroy {
  @Input({ required: true }) videoUrl!: SafeResourceUrl;

  @ViewChild('videoStage') videoStageRef!: ElementRef<HTMLDivElement>;
  @ViewChild('videoWrapper') videoWrapperRef!: ElementRef<HTMLDivElement>;
  @ViewChild('videoEl') videoElRef!: ElementRef<HTMLVideoElement>;
  @ViewChild('canvasEl') canvasElRef!: ElementRef<HTMLCanvasElement>;
  @ViewChild('controlsBar') controlsBarRef!: ElementRef<HTMLDivElement>;

  isPlaying = false;
  currentTime = 0;
  duration = 0;
  playbackRate = 1;
  isDrawMode = false;
  isFullscreen = false;
  isToolbarOpen = false;
  isSymbolPopupOpen = false;
  showVolumeSlider = false;
  isZoomMode = false;
  isZoomed = false;
  isFullscreenNotesOpen = false;
  showZoomPresetMenu = false;
  zoomSelectionActive = false;
  zoomSelectionCenter: DrawPoint | null = null;
  activeZoomScale: number | null = null;
  activeZoomBoxSize = 0;
  zoomScale = 1;
  zoomOffsetX = 0;
  zoomOffsetY = 0;
  volume = 1;
  isMuted = false;
  currentTool: DrawTool = 'pen';

  strokes: Stroke[] = [];
  redoStack: Stroke[] = [];
  notes: Note[] = [];
  flags: Flag[] = [];
  annotationItems: Array<{ id: string; timestamp: number; text: string }> = [];
  newNoteText = '';
  lastAnnotationMessage = '';

  private resizeObserver: ResizeObserver | null = null;
  private controlsResizeObserver: ResizeObserver | null = null;
  private isDrawing = false;
  private strokeStart: DrawPoint | null = null;
  private currentStroke: Stroke | null = null;
  private ctx!: CanvasRenderingContext2D;

  constructor(private cdr: ChangeDetectorRef) {}

  ngAfterViewInit(): void {
    const canvas = this.canvasElRef.nativeElement;
    this.ctx = canvas.getContext('2d')!;
    this.resizeObserver = new ResizeObserver(() => this.syncCanvasSize());
    this.resizeObserver.observe(this.videoElRef.nativeElement);
    this.syncCanvasSize();
    this.syncAnnotations();
    document.addEventListener('fullscreenchange', this.onFullscreenChange);
    // Observe controls bar size to dynamically compute panel bottom offset
    try {
      this.controlsResizeObserver = new ResizeObserver(() => this.updateControlsHeight());
      if (this.controlsBarRef && this.controlsBarRef.nativeElement) this.controlsResizeObserver.observe(this.controlsBarRef.nativeElement);
    } catch (e) {
      // ResizeObserver not supported - fallback to window resize listener
      window.addEventListener('resize', this.updateControlsHeight);
    }
    // Initial update
    setTimeout(() => this.updateControlsHeight(), 50);
  }

  ngOnDestroy(): void {
    this.resizeObserver?.disconnect();
    document.removeEventListener('fullscreenchange', this.onFullscreenChange);
    this.controlsResizeObserver?.disconnect();
    try { window.removeEventListener('resize', this.updateControlsHeight); } catch {}
  }

  updateControlsHeight = () => {
    try {
      const el = this.controlsBarRef?.nativeElement;
      const h = el ? el.getBoundingClientRect().height : 72;
      // set CSS variable on the stage element so CSS can use it for panel bottom/height
      if (this.videoStageRef && this.videoStageRef.nativeElement) {
        this.videoStageRef.nativeElement.style.setProperty('--controls-height', `${h}px`);
      }
    } catch (e) {
      // ignore
    }
  };

  private syncCanvasSize() {
    const video = this.videoElRef.nativeElement;
    const canvas = this.canvasElRef.nativeElement;
    canvas.width = video.clientWidth;
    canvas.height = video.clientHeight;
    this.redrawStrokes();
  }

  // ---- Playback ----
  togglePlay() {
    const video = this.videoElRef.nativeElement;
    video.paused ? video.play() : video.pause();
  }

  get videoTransform() {
    return `translate(${this.zoomOffsetX}px, ${this.zoomOffsetY}px) scale(${this.zoomScale})`;
  }

  get zoomSelectionLeft(): number {
    if (!this.zoomSelectionCenter) return 0;
    return this.zoomSelectionCenter.x - this.activeZoomBoxSize / 2;
  }

  get zoomSelectionTop(): number {
    if (!this.zoomSelectionCenter) return 0;
    return this.zoomSelectionCenter.y - this.activeZoomBoxSize / 2;
  }

  getZoomPresetSquareSize(zoomScale: number): number {
    if (zoomScale >= 3.5) return 200;
    if (zoomScale >= 3) return 300;
    return 350;
  }

  toggleZoomMode(event?: MouseEvent) {
    if (event) event.stopPropagation();
    this.showZoomPresetMenu = !this.showZoomPresetMenu;
    if (!this.showZoomPresetMenu) {
      this.isZoomMode = false;
      this.zoomSelectionActive = false;
      this.zoomSelectionCenter = null;
    }
    this.isDrawMode = false;
    this.isToolbarOpen = false;
    this.isSymbolPopupOpen = false;
    this.cdr.markForCheck();
  }

  selectZoomPreset(zoomScale: number, event?: MouseEvent) {
    if (event) event.stopPropagation();
    this.activeZoomScale = zoomScale;
    this.activeZoomBoxSize = this.getZoomPresetSquareSize(zoomScale);
    this.showZoomPresetMenu = false;
    this.isZoomMode = true;
    this.zoomSelectionActive = true;
    this.zoomSelectionCenter = null;
    this.cdr.markForCheck();
  }

  resetZoom(event?: MouseEvent) {
    if (event) event.stopPropagation();
    this.zoomScale = 1;
    this.zoomOffsetX = 0;
    this.zoomOffsetY = 0;
    this.isZoomMode = false;
    this.isZoomed = false;
    this.showZoomPresetMenu = false;
    this.zoomSelectionActive = false;
    this.zoomSelectionCenter = null;
    this.activeZoomScale = null;
    this.activeZoomBoxSize = 0;
    this.cdr.markForCheck();
  }
  onPlay() { this.isPlaying = true; this.cdr.markForCheck(); }
  onPause() { this.isPlaying = false; this.cdr.markForCheck(); }
  onTimeUpdate() {
    this.currentTime = this.videoElRef.nativeElement.currentTime;
    this.cdr.markForCheck();
  }
  onLoadedMetadata() {
    this.duration = this.videoElRef.nativeElement.duration;
    this.cdr.markForCheck();
  }
  seekTo(seconds: number) { this.videoElRef.nativeElement.currentTime = seconds; }
  onSeekBarInput(event: Event) { this.seekTo(+(event.target as HTMLInputElement).value); }
  setPlaybackRate(rate: number) {
    this.playbackRate = rate;
    this.videoElRef.nativeElement.playbackRate = rate;
  }
  setVolume(value: number) {
    const video = this.videoElRef.nativeElement;
    this.volume = Math.max(0, Math.min(1, value));
    video.volume = this.volume;
    this.isMuted = this.volume === 0 || video.muted;
    video.muted = this.volume === 0;
    this.cdr.markForCheck();
  }
  toggleMute(event?: MouseEvent) {
    if (event) event.stopPropagation();
    const video = this.videoElRef.nativeElement;
    if (video.muted || this.volume === 0) {
      this.volume = Math.max(this.volume, 0.25);
      video.volume = this.volume;
      video.muted = false;
      this.isMuted = false;
    } else {
      video.muted = true;
      this.isMuted = true;
    }
    this.showVolumeSlider = true;
    this.cdr.markForCheck();
  }
  toggleVolumeSlider(event?: MouseEvent) {
    if (event) event.stopPropagation();
    this.showVolumeSlider = !this.showVolumeSlider;
    this.cdr.markForCheck();
  }
  stepFrame(direction: 1 | -1) {
    const video = this.videoElRef.nativeElement;
    video.pause();
    const FRAME_DURATION = 1 / 30;
    video.currentTime = Math.min(Math.max(video.currentTime + direction * FRAME_DURATION, 0), this.duration);
  }

  // ---- Draw controls (compact overlay above the Draw button) ----
  toggleDrawMenu(event?: MouseEvent) {
    if (event) event.stopPropagation();
    this.isToolbarOpen = !this.isToolbarOpen;
    this.isDrawMode = this.isToolbarOpen;
    if (!this.isToolbarOpen) {
      this.isSymbolPopupOpen = false;
    }
    if (this.isDrawMode) this.videoElRef.nativeElement.pause();
    this.cdr.markForCheck();
  }

  toggleSymbolsMenu(event?: MouseEvent) {
    if (!this.isToolbarOpen) return;
    if (event) event.stopPropagation();
    this.isSymbolPopupOpen = !this.isSymbolPopupOpen;
    this.cdr.markForCheck();
  }

  selectTool(tool: DrawTool, event?: MouseEvent) {
    if (event) event.stopPropagation();
    this.currentTool = tool;
    this.cdr.markForCheck();
  }

  // ---- Drawing (pen = freehand path, shapes = two-point bounding box) ----
  onPointerDown(event: PointerEvent) {
    if (!this.isDrawMode) return;
    this.isDrawing = true;
    const point = this.toNormalizedPoint(event);
    this.strokeStart = point;
    this.currentStroke = { tool: this.currentTool, points: [point] };
  }

  onZoomPointerDown(event: PointerEvent) {
    if (!this.isZoomMode || !this.activeZoomBoxSize) return;
    event.preventDefault();
    event.stopPropagation();
    this.zoomSelectionCenter = this.toVideoPoint(event);
    this.applyZoomSelection(this.zoomSelectionCenter);
    this.zoomSelectionActive = false;
    this.isZoomMode = false;
    this.isZoomed = true;
    this.showZoomPresetMenu = false;
    this.cdr.markForCheck();
  }

  onZoomPointerMove(event: PointerEvent) {
    if (!this.isZoomMode || !this.zoomSelectionActive) return;
    this.zoomSelectionCenter = this.toVideoPoint(event);
    this.cdr.markForCheck();
  }

  onZoomPointerLeave() {
    if (!this.isZoomMode || !this.zoomSelectionActive) return;
    this.zoomSelectionActive = false;
    this.zoomSelectionCenter = null;
    this.cdr.markForCheck();
  }

  private getDisplayedVideoFrame() {
    const video = this.videoElRef.nativeElement;
    const wrapper = this.videoWrapperRef?.nativeElement;
    const rect = wrapper ? wrapper.getBoundingClientRect() : video.getBoundingClientRect();
    const naturalWidth = video.videoWidth || rect.width;
    const naturalHeight = video.videoHeight || rect.height;
    const objectFit = window.getComputedStyle(video).objectFit || 'cover';
    const scale = objectFit === 'contain'
      ? Math.min(rect.width / naturalWidth, rect.height / naturalHeight)
      : Math.max(rect.width / naturalWidth, rect.height / naturalHeight);
    const width = naturalWidth * scale;
    const height = naturalHeight * scale;
    return {
      width,
      height,
      left: rect.left + (rect.width - width) / 2,
      top: rect.top + (rect.height - height) / 2
    };
  }

  private toVideoPoint(event: PointerEvent): DrawPoint {
    const frame = this.getDisplayedVideoFrame();
    return {
      x: Math.min(Math.max(event.clientX - frame.left, 0), frame.width),
      y: Math.min(Math.max(event.clientY - frame.top, 0), frame.height)
    };
  }

  private applyZoomSelection(center: DrawPoint) {
    const video = this.videoElRef.nativeElement;
    const frame = this.getDisplayedVideoFrame();
    const squareSize = Math.max(this.activeZoomBoxSize, 20);
    const scale = Math.min(frame.width / squareSize, frame.height / squareSize);
    const tx = (frame.width / 2 - center.x) * (scale - 1);
    const ty = (frame.height / 2 - center.y) * (scale - 1);
    this.zoomScale = parseFloat(scale.toFixed(4));
    this.zoomOffsetX = parseFloat(tx.toFixed(2));
    this.zoomOffsetY = parseFloat(ty.toFixed(2));
    if (video) {
      video.style.setProperty('transform-origin', 'center center');
    }
  }

  onPointerMove(event: PointerEvent) {
    if (!this.isDrawing || !this.currentStroke || !this.strokeStart) return;
    const point = this.toNormalizedPoint(event);
    if (this.currentTool === 'pen') {
      this.currentStroke.points.push(point);
    } else {
      // Shapes only need start + live end point for a bounding-box preview
      this.currentStroke.points = [this.strokeStart, point];
    }
    this.redrawStrokes();
    this.drawStroke(this.currentStroke);
  }

  onPointerUp() {
    if (this.currentStroke && this.currentStroke.points.length >= 2) {
      this.strokes.push(this.currentStroke);
      this.redoStack = [];
    }
    this.currentStroke = null;
    this.strokeStart = null;
    this.isDrawing = false;
  }

  undo(event?: MouseEvent) {
    if (event) event.stopPropagation();
    const last = this.strokes.pop();
    if (last) { this.redoStack.push(last); this.redrawStrokes(); this.cdr.markForCheck(); }
  }
  redo(event?: MouseEvent) {
    if (event) event.stopPropagation();
    const restored = this.redoStack.pop();
    if (restored) { this.strokes.push(restored); this.redrawStrokes(); this.cdr.markForCheck(); }
  }
  clearDrawing(event?: MouseEvent) {
    if (event) event.stopPropagation();
    this.strokes = [];
    this.redoStack = [];
    this.redrawStrokes();
    this.cdr.markForCheck();
  }

  private toNormalizedPoint(event: PointerEvent): DrawPoint {
    const canvas = this.canvasElRef.nativeElement;
    const rect = canvas.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) / rect.width,
      y: (event.clientY - rect.top) / rect.height
    };
  }

  private redrawStrokes() {
    const canvas = this.canvasElRef.nativeElement;
    this.ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (const stroke of this.strokes) this.drawStroke(stroke);
  }

  private drawStroke(stroke: Stroke) {
    const canvas = this.canvasElRef.nativeElement;
    const toPx = (p: DrawPoint) => ({ x: p.x * canvas.width, y: p.y * canvas.height });

    this.ctx.strokeStyle = '#f5f5f5';
    this.ctx.lineWidth = 3;
    this.ctx.lineJoin = 'round';
    this.ctx.lineCap = 'round';

    if (stroke.tool === 'pen') {
      if (stroke.points.length < 2) return;
      const start = toPx(stroke.points[0]);
      this.ctx.beginPath();
      this.ctx.moveTo(start.x, start.y);
      for (const p of stroke.points.slice(1)) {
        const px = toPx(p);
        this.ctx.lineTo(px.x, px.y);
      }
      this.ctx.stroke();
      return;
    }

    if (stroke.points.length < 2) return;
    const a = toPx(stroke.points[0]);
    const b = toPx(stroke.points[1]);

    if (stroke.tool === 'square') {
      this.ctx.strokeRect(
        Math.min(a.x, b.x), Math.min(a.y, b.y),
        Math.abs(b.x - a.x), Math.abs(b.y - a.y)
      );
    } else if (stroke.tool === 'circle') {
      const cx = (a.x + b.x) / 2;
      const cy = (a.y + b.y) / 2;
      const rx = Math.abs(b.x - a.x) / 2;
      const ry = Math.abs(b.y - a.y) / 2;
      this.ctx.beginPath();
      this.ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
      this.ctx.stroke();
    } else if (stroke.tool === 'arrow') {
      this.ctx.beginPath();
      this.ctx.moveTo(a.x, a.y);
      this.ctx.lineTo(b.x, b.y);
      this.ctx.stroke();

      const angle = Math.atan2(b.y - a.y, b.x - a.x);
      const headLength = 14;
      this.ctx.beginPath();
      this.ctx.moveTo(b.x, b.y);
      this.ctx.lineTo(
        b.x - headLength * Math.cos(angle - Math.PI / 6),
        b.y - headLength * Math.sin(angle - Math.PI / 6)
      );
      this.ctx.moveTo(b.x, b.y);
      this.ctx.lineTo(
        b.x - headLength * Math.cos(angle + Math.PI / 6),
        b.y - headLength * Math.sin(angle + Math.PI / 6)
      );
      this.ctx.stroke();
    }
  }

  private genId(): string {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  }

  // ---- Notes ----
  addNote(event?: Event) {
    event?.preventDefault();
    const trimmed = this.newNoteText.trim();
    if (!trimmed) {
      console.debug('addNote skipped: empty input');
      return;
    }
    const newNote: Note = { id: this.genId(), timestamp: this.currentTime, text: trimmed };
    this.notes = [...this.notes, newNote].sort((a, b) => a.timestamp - b.timestamp);
    this.syncAnnotations();
    this.newNoteText = '';
    this.lastAnnotationMessage = `Added note at ${this.formatTime(newNote.timestamp)}`;
    this.cdr.markForCheck();
    console.log('addNote fired', { note: newNote, notes: this.notes, annotationItems: this.annotationItems });
  }
  removeNote(id: string) {
    this.notes = this.notes.filter(n => n.id !== id);
    this.syncAnnotations();
    this.cdr.markForCheck();
  }

  // ---- Flags ----
  addFlag(event?: Event) {
    event?.preventDefault();
    const newFlag: Flag = { id: this.genId(), timestamp: this.currentTime };
    this.flags = [...this.flags, newFlag].sort((a, b) => a.timestamp - b.timestamp);
    this.syncAnnotations();
    this.lastAnnotationMessage = `Added flag at ${this.formatTime(newFlag.timestamp)}`;
    this.isFullscreenNotesOpen = this.isFullscreen ? true : this.isFullscreenNotesOpen;
    this.cdr.markForCheck();
    console.log('addFlag fired', { flag: newFlag, flags: this.flags, annotationItems: this.annotationItems });
  }
  removeFlag(id: string) {
    this.flags = this.flags.filter(f => f.id !== id);
    this.syncAnnotations();
    this.cdr.markForCheck();
  }
  flagPosition(timestamp: number): number {
    return this.duration > 0 ? (timestamp / this.duration) * 100 : 0;
  }

  formatTime(seconds: number): string {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
  }

  // ---- Fullscreen ----
  toggleFullscreen() {
    if (!document.fullscreenElement) {
      this.videoStageRef.nativeElement.requestFullscreen().catch((err) => console.error('Fullscreen request failed:', err));
    } else {
      document.exitFullscreen();
    }
  }
  private onFullscreenChange = () => {
    this.isFullscreen = !!document.fullscreenElement;
    // Auto-open the notes panel whenever entering fullscreen; close on exit
    this.isFullscreenNotesOpen = this.isFullscreen ? true : false;
    this.cdr.markForCheck();
  };

  toggleFullscreenNotes(event?: MouseEvent) {
    if (event) event.stopPropagation();
    // If not in fullscreen, ignore
    if (!this.isFullscreen) return;
    this.isFullscreenNotesOpen = !this.isFullscreenNotesOpen;
    this.cdr.markForCheck();
  }

  private syncAnnotations() {
    const noteItems = this.notes.map(n => ({ id: n.id, timestamp: n.timestamp, text: n.text || '' }));
    const flagItems = this.flags.map(f => ({ id: f.id, timestamp: f.timestamp, text: '' }));
    this.annotationItems = [...noteItems, ...flagItems].sort((a, b) => a.timestamp - b.timestamp);
  }

  // ---- Keyboard shortcuts ----
  @HostListener('window:keydown', ['$event'])
  handleKeydown(event: KeyboardEvent) {
    const target = event.target as HTMLElement;
    if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') return;

    switch (event.key) {
      case ' ': event.preventDefault(); this.togglePlay(); break;
      case 'ArrowLeft': event.preventDefault(); this.stepFrame(-1); break;
      case 'ArrowRight': event.preventDefault(); this.stepFrame(1); break;
      case 'd': case 'D': this.toggleDrawMenu(); break;
      case 'z': case 'Z':
        if (event.ctrlKey || event.metaKey) {
          event.preventDefault();
          event.shiftKey ? this.redo() : this.undo();
        }
        break;
      case 'y': case 'Y':
        if (event.ctrlKey || event.metaKey) { event.preventDefault(); this.redo(); }
        break;
      case 'f': case 'F': this.toggleFullscreen(); break;
    }
  }
}
