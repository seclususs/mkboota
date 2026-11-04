from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile

from PIL import Image
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gui.multimedia import silence_multimedia_logging
from gui.widgets.cropper import CropperCanvas
from gui.widgets.inspector import PartInspector
from gui.widgets.meter import VramMeter
from gui.widgets.timeline import TimelineWidget
from gui.workers.compiler import CompileWorker
from mkboota.compiler import AnimationCompiler
from mkboota.estimator import CompilationEstimator
from mkboota.exceptions import MkbootaError
from mkboota.models import (
    CanvasResolution,
    CropRect,
    SegmentSpec,
    VideoMetadata,
    align_mod16,
)
from mkboota.video import FFmpegExtractor, FFprobeInspector

_RESOLUTION_PRESETS: list[tuple[str, int, int]] = [
    ("1088x1920 (9:16 Phone)", 1088, 1920),
    ("1088x2400 (20:9 Phone)", 1088, 2400),
    ("720x1280 (HD Phone)", 720, 1280),
    ("1824x2176 (Foldable 5:6)", 1824, 2176),
    ("2560x1600 (Tablet 16:10)", 2560, 1600),
    ("464x464 (Watch 1:1)", 464, 464),
    ("1920x1080 (Landscape TV / Auto)", 1920, 1080),
    ("1920x720 (Ultrawide Auto 21:9)", 1920, 720),
    ("Custom", 0, 0),
]


class StudioTab(QWidget):
    """Studio editor tab combining viewport, timeline, inspector, and compiler"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._video_path: Path | None = None
        self._active_worker: CompileWorker | None = None
        self._current_canvas: CanvasResolution = CanvasResolution(width=1088, height=1920)
        self._fps: int = 30
        self._media_player: QMediaPlayer | None = None
        self._video_sink: QVideoSink | None = None
        self._playback_timer: QTimer = QTimer(self)
        self._is_updating_slider: bool = False
        self._is_updating_timeline: bool = False
        self._syncing_segments: bool = False
        self._last_seek_ms: int = -1

        self._init_multimedia()
        self._setup_ui()
        self._connect_signals()

    def _init_multimedia(self) -> None:
        silence_multimedia_logging()
        try:
            player = QMediaPlayer(self)
            sink = QVideoSink(self)
            player.setVideoSink(sink)
            self._media_player = player
            self._video_sink = sink
        except (RuntimeError, TypeError, ValueError, AttributeError, OSError):
            self._media_player = None
            self._video_sink = None

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        main_layout.addLayout(self._create_header_controls())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._create_viewport_panel())
        splitter.addWidget(self._create_sidebar_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        main_layout.addWidget(splitter, stretch=1)

        main_layout.addWidget(self._create_timeline_panel())
        main_layout.addLayout(self._create_compile_controls())

    def _create_header_controls(self) -> QHBoxLayout:
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        self.video_path_edit = QLineEdit()
        self.video_path_edit.setPlaceholderText("Select input video file")
        self.video_path_edit.setReadOnly(True)

        self.open_video_btn = QPushButton("Browse Video")
        header_layout.addWidget(self.video_path_edit, stretch=1)
        header_layout.addWidget(self.open_video_btn)

        header_layout.addWidget(QLabel("Preset:"))
        self.resolution_combo = QComboBox()
        self.preset_combo = self.resolution_combo
        self.resolution_combo.setMinimumWidth(140)
        for label, w, h in _RESOLUTION_PRESETS:
            self.resolution_combo.addItem(label, (w, h))
        header_layout.addWidget(self.resolution_combo)

        self.custom_w_spin = QSpinBox()
        self.custom_w_spin.setRange(16, 8192)
        self.custom_w_spin.setSingleStep(16)
        self.custom_w_spin.setValue(1088)

        self.custom_h_spin = QSpinBox()
        self.custom_h_spin.setRange(16, 8192)
        self.custom_h_spin.setSingleStep(2)
        self.custom_h_spin.setValue(1920)

        self.custom_res_container = QWidget()
        custom_layout = QHBoxLayout(self.custom_res_container)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setSpacing(4)
        custom_layout.addWidget(QLabel("W:"))
        custom_layout.addWidget(self.custom_w_spin)
        custom_layout.addWidget(QLabel("H:"))
        custom_layout.addWidget(self.custom_h_spin)
        self.custom_res_container.setVisible(False)
        header_layout.addWidget(self.custom_res_container)

        header_layout.addWidget(QLabel("FPS:"))
        self.fps_combo = QComboBox()
        self.fps_combo.setMinimumWidth(90)
        self.fps_combo.setEditable(True)
        for fps_val in (30, 60, 24, 15):
            self.fps_combo.addItem(str(fps_val), fps_val)
        header_layout.addWidget(self.fps_combo)

        return header_layout

    def _create_viewport_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.cropper = CropperCanvas()
        self.cropper.set_preset(1088, 1920)
        layout.addWidget(self.cropper, stretch=1)
        layout.addLayout(self._create_transport_bar())
        return panel

    def _create_transport_bar(self) -> QHBoxLayout:
        transport_layout = QHBoxLayout()
        transport_layout.setSpacing(8)

        self.play_btn = QPushButton("Play")
        self.play_btn.setFixedWidth(64)

        self.scrub_slider = QSlider(Qt.Orientation.Horizontal)
        self.scrub_slider.setRange(0, 0)

        self.timecode_label = QLabel("00:00.00 / 00:00.00")
        self.timecode_label.setStyleSheet("font-family: monospace;")

        transport_layout.addWidget(self.play_btn)
        transport_layout.addWidget(self.scrub_slider, stretch=1)
        transport_layout.addWidget(self.timecode_label)
        return transport_layout

    def _create_sidebar_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        vram_group = QGroupBox("VRAM Advisor")
        vram_layout = QVBoxLayout(vram_group)
        self.vram_meter = VramMeter()
        vram_layout.addWidget(self.vram_meter)
        layout.addWidget(vram_group)

        inspector_group = QGroupBox("Part Parameters")
        inspector_layout = QVBoxLayout(inspector_group)
        self.part_inspector = PartInspector()
        inspector_layout.addWidget(self.part_inspector)
        layout.addWidget(inspector_group, stretch=1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(panel)
        return scroll

    def _create_timeline_panel(self) -> QGroupBox:
        group = QGroupBox("Timeline")
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        actions_layout = QHBoxLayout()
        self.split_btn = QPushButton("Split at Playhead")
        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.setEnabled(False)
        self.reset_btn = QPushButton("Reset Segments")
        self.reset_btn.setEnabled(False)
        actions_layout.addWidget(self.split_btn)
        actions_layout.addWidget(self.delete_btn)
        actions_layout.addWidget(self.reset_btn)
        actions_layout.addStretch()
        layout.addLayout(actions_layout)

        self.timeline = TimelineWidget()
        layout.addWidget(self.timeline)
        return group

    def _create_compile_controls(self) -> QHBoxLayout:
        compile_layout = QHBoxLayout()
        compile_layout.setSpacing(8)

        self.compile_btn = QPushButton("Compile Bootanimation")
        self.compile_btn.setProperty("primary", "true")
        self.compile_btn.setEnabled(False)

        self.cancel_compile_btn = QPushButton("Cancel")
        self.cancel_compile_btn.setEnabled(False)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(16)

        self.status_label = QLabel("Ready")
        self.status_label.setProperty("muted", "true")

        compile_layout.addWidget(self.compile_btn)
        compile_layout.addWidget(self.cancel_compile_btn)
        compile_layout.addWidget(self.progress_bar, stretch=1)
        compile_layout.addWidget(self.status_label)

        return compile_layout

    def _connect_signals(self) -> None:
        self.open_video_btn.clicked.connect(self._on_browse_video)
        self.resolution_combo.currentIndexChanged.connect(self._on_preset_changed)
        self.custom_w_spin.valueChanged.connect(self._on_custom_resolution_changed)
        self.custom_h_spin.valueChanged.connect(self._on_custom_resolution_changed)
        self.fps_combo.currentTextChanged.connect(self._on_fps_changed)

        self.play_btn.clicked.connect(self._toggle_playback)
        self.scrub_slider.sliderMoved.connect(self._on_slider_moved)
        self.scrub_slider.valueChanged.connect(self._on_slider_value_changed)
        self.timeline.time_scrubbed.connect(self._on_timeline_scrubbed)
        self._playback_timer.timeout.connect(self._on_timer_tick)

        if self._media_player is not None and self._video_sink is not None:
            self._video_sink.videoFrameChanged.connect(self._on_video_frame_changed)
            self._media_player.positionChanged.connect(self._on_player_position_changed)
            self._media_player.durationChanged.connect(self._on_player_duration_changed)
            self._media_player.playbackStateChanged.connect(self._on_playback_state_changed)

        self.split_btn.clicked.connect(self._on_split_clicked)
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        self.reset_btn.clicked.connect(self._on_reset_segments)
        self.compile_btn.clicked.connect(self._on_compile_clicked)
        self.cancel_compile_btn.clicked.connect(self.cancel_compilation)

        self.timeline.segments_changed.connect(self._on_segments_changed)
        self.timeline.segment_selected.connect(self._on_segment_selected)
        self.timeline.segment_time_changed.connect(self._on_segment_time_changed)
        self.part_inspector.segment_updated.connect(self._on_part_updated)

    def set_canvas_resolution(self, width: int, height: int) -> None:
        aligned_w = align_mod16(width) if width > 0 else 16
        safe_h = max(1, height)
        self._current_canvas = CanvasResolution(width=aligned_w, height=safe_h)
        self.cropper.set_preset(aligned_w, safe_h)
        self.update_vram_estimation()

    def get_canvas_resolution(self) -> CanvasResolution:
        return self._current_canvas

    def set_fps(self, fps: int) -> None:
        self._fps = max(1, fps)
        if self.fps_combo.currentText() != str(self._fps):
            self.fps_combo.blockSignals(True)
            self.fps_combo.setCurrentText(str(self._fps))
            self.fps_combo.blockSignals(False)

        self.update_vram_estimation()

    def get_fps(self) -> int:
        return self.get_effective_fps()

    def get_effective_fps(self) -> int:
        text = self.fps_combo.currentText().strip()
        if not text:
            return 30

        try:
            val = int(text)
            return val if val > 0 else 30
        except ValueError:
            return 30

    @property
    def total_duration(self) -> float:
        return self.timeline.get_duration()

    @total_duration.setter
    def total_duration(self, seconds: float) -> None:
        self.timeline.set_duration(seconds)

    def _on_browse_video(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Video",
            "",
            "Video Files (*.mp4 *.mkv *.avi *.mov *.webm);;All Files (*)",
        )
        if file_path:
            self.load_video(Path(file_path))

    def browse_video(self) -> None:
        self._on_browse_video()

    def load_video(self, video_path: Path) -> None:
        if not video_path.exists():
            self.status_label.setText(f"Video file not found: {video_path}")
            return

        self._video_path = video_path
        self.video_path_edit.setText(str(video_path))

        try:
            inspector = FFprobeInspector()
            metadata = inspector.probe(video_path)
            extractor = FFmpegExtractor()
            frame = self._extract_preview_frame(video_path, extractor)
            self.load_video_from_metadata(video_path, metadata, frame)
        except (MkbootaError, OSError, ValueError, RuntimeError) as exc:
            self.status_label.setText(f"Probe failed: {exc}")

    def _extract_preview_frame(
        self, video_path: Path, extractor: FFmpegExtractor
    ) -> Image.Image | None:
        frame = self._try_extract_single_frame(video_path, extractor)
        if frame is not None:
            return frame
        return Image.new("RGBA", (1088, 1920), (20, 20, 25, 255))

    @staticmethod
    def _read_frame_rgba(frame_path: Path) -> Image.Image:
        with Image.open(frame_path) as img:
            return img.convert("RGBA")

    @staticmethod
    def _try_extract_single_frame(
        video_path: Path, extractor: FFmpegExtractor
    ) -> Image.Image | None:
        try:
            with tempfile.TemporaryDirectory() as tmp_dir_str:
                frames = extractor.extract_frames(
                    video_path=video_path,
                    output_dir=Path(tmp_dir_str),
                    target_fps=1,
                    max_duration=1.0,
                )
                return StudioTab._read_frame_rgba(frames[0]) if frames else None
        except (MkbootaError, OSError, ValueError, RuntimeError):
            return None

    def load_video_from_metadata(
        self,
        video_path: Path,
        metadata: VideoMetadata,
        representative_frame: Image.Image | None = None,
    ) -> None:
        self._pause_playback()
        self._video_path = video_path
        self.video_path_edit.setText(str(video_path))
        self.timeline.set_duration(metadata.duration)

        duration_ms = int(round(metadata.duration * 1000.0))
        self.scrub_slider.setRange(0, max(0, duration_ms))
        self._seek_to_seconds(0.0)

        if self._media_player is not None and video_path.exists():
            self._media_player.setSource(QUrl.fromLocalFile(str(video_path)))

        initial_segment = SegmentSpec(
            start_time=0.0,
            end_time=metadata.duration,
            type_code="p",
            count=0,
            pause=0,
            folder_name="part0",
        )
        self.timeline.set_segments([initial_segment])
        self.timeline.select_segment(0)

        if representative_frame is not None:
            self.cropper.set_image(representative_frame)

        self.reset_btn.setEnabled(True)
        self.compile_btn.setEnabled(True)
        self.update_vram_estimation()

    def _on_preset_changed(self) -> None:
        data = self.resolution_combo.currentData()
        if data is None:
            return

        w, h = data
        is_custom = w == 0 and h == 0
        self.custom_res_container.setVisible(is_custom)
        if is_custom:
            self.set_canvas_resolution(self.custom_w_spin.value(), self.custom_h_spin.value())
            return

        self.set_canvas_resolution(w, h)

    def _on_custom_resolution_changed(self) -> None:
        aligned_w = align_mod16(self.custom_w_spin.value())
        if self.custom_w_spin.value() != aligned_w:
            self.custom_w_spin.setValue(aligned_w)
            return
        self.set_canvas_resolution(aligned_w, self.custom_h_spin.value())

    def _on_fps_changed(self) -> None:
        self._fps = self.get_effective_fps()
        self.update_vram_estimation()

    def _on_split_clicked(self) -> None:
        current_time = self.timeline.get_current_time()
        self.timeline.split_at(current_time)

    def _on_delete_clicked(self) -> None:
        idx = self.timeline.get_selected_index()
        if idx >= 0:
            self.timeline.delete_segment(idx)
        self.delete_btn.setEnabled(len(self.timeline.get_segments()) > 1)

    def _on_reset_segments(self) -> None:
        if self.total_duration <= 0.0:
            return
        default_segment = SegmentSpec(
            start_time=0.0,
            end_time=self.total_duration,
            type_code="p",
            count=0,
            pause=0,
            folder_name="part0",
        )
        self.timeline.set_segments([default_segment])
        self.timeline.select_segment(0)
        self._seek_to_seconds(0.0)
        self._update_looping_vram()

    def _on_segments_changed(self, segments: list[SegmentSpec]) -> None:
        self.delete_btn.setEnabled(len(segments) > 1)
        self.update_vram_estimation()

    def _on_segment_selected(self, index: int) -> None:
        self.delete_btn.setEnabled(len(self.timeline.get_segments()) > 1)
        if self._syncing_segments:
            return

        segments = self.timeline.get_segments()
        if 0 <= index < len(segments):
            self.part_inspector.load_segment(segments[index])
            self._seek_to_seconds(segments[index].start_time)
        else:
            self.part_inspector.load_segment(None)

    def _on_segment_time_changed(self, index: int, start: float, end: float) -> None:
        if self._syncing_segments:
            return

        selected = self.timeline.get_selected_index()
        if selected != index:
            return

        segments = self.timeline.get_segments()
        if 0 <= selected < len(segments):
            self._syncing_segments = True
            try:
                self.part_inspector.load_segment(segments[selected])
            finally:
                self._syncing_segments = False

    def _on_part_updated(self, updated: SegmentSpec) -> None:
        if self._syncing_segments:
            return

        idx = self.timeline.get_selected_index()
        segments = self.timeline.get_segments()
        if idx < 0 or idx >= len(segments):
            return

        old_seg = segments[idx]
        self._syncing_segments = True
        try:
            self._sync_cut_point_or_update(idx, updated, old_seg, segments)
            current_segments = self.timeline.get_segments()
            if 0 <= idx < len(current_segments):
                self.part_inspector.load_segment(current_segments[idx])
        finally:
            self._syncing_segments = False

        if abs(updated.start_time - old_seg.start_time) > 1e-4:
            self._seek_to_seconds(updated.start_time)
        elif updated.end_time is not None and (
            old_seg.end_time is None or abs(updated.end_time - old_seg.end_time) > 1e-4
        ):
            self._seek_to_seconds(max(0.0, updated.end_time - 0.05))

    def _sync_cut_point_or_update(
        self, idx: int, updated: SegmentSpec, old_seg: SegmentSpec, segments: list[SegmentSpec]
    ) -> None:
        has_next = idx < len(segments) - 1
        end_changed = updated.end_time is not None and (
            old_seg.end_time is None or abs(updated.end_time - old_seg.end_time) > 1e-6
        )
        start_changed = abs(updated.start_time - old_seg.start_time) > 1e-6
        adjust_start_first = updated.start_time < old_seg.start_time

        if adjust_start_first:
            if idx > 0 and start_changed:
                self.timeline.adjust_cut_point(idx - 1, updated.start_time)

            if has_next and end_changed and updated.end_time is not None:
                self.timeline.adjust_cut_point(idx, updated.end_time)
        else:
            if has_next and end_changed and updated.end_time is not None:
                self.timeline.adjust_cut_point(idx, updated.end_time)

            if idx > 0 and start_changed:
                self.timeline.adjust_cut_point(idx - 1, updated.start_time)

        if end_changed or start_changed:
            if idx == 0 and start_changed:
                current_segs = self.timeline.get_segments()
                current_segs[idx] = replace(current_segs[idx], start_time=updated.start_time)
                self.timeline.set_segments(current_segs)

            if not has_next and end_changed and updated.end_time is not None:
                current_segs = self.timeline.get_segments()
                current_segs[idx] = replace(current_segs[idx], end_time=updated.end_time)
                self.timeline.set_segments(current_segs)

            if self._metadata_changed(updated, old_seg):
                self._apply_segment_metadata(idx, updated)

            return

        segments[idx] = updated
        self.timeline.set_segments(segments)

    @staticmethod
    def _metadata_changed(a: SegmentSpec, b: SegmentSpec) -> bool:
        return (
            a.type_code != b.type_code
            or a.count != b.count
            or a.pause != b.pause
            or a.folder_name != b.folder_name
            or a.bg_color != b.bg_color
            or a.clock_pos != b.clock_pos
        )

    def _apply_segment_metadata(self, idx: int, updated: SegmentSpec) -> None:
        segments = self.timeline.get_segments()
        segments[idx] = replace(
            updated,
            start_time=segments[idx].start_time,
            end_time=segments[idx].end_time,
        )
        self.timeline.set_segments(segments)

    def update_vram_estimation(self) -> None:
        estimate = CompilationEstimator.estimate_from_segments(
            segments=self.timeline.get_segments(),
            duration=self.timeline.get_duration(),
            canvas=self.get_canvas_resolution(),
            fps=self.get_fps(),
        )
        total_mb = sum(p.vram_mb for p in estimate.parts)
        self.vram_meter.update_metrics(total_mb, estimate.looping_vram_mb, list(estimate.warnings))

    def calculate_looping_vram_mb(self) -> float:
        estimate = CompilationEstimator.estimate_from_segments(
            segments=self.timeline.get_segments(),
            duration=self.timeline.get_duration(),
            canvas=self.get_canvas_resolution(),
            fps=self.get_fps(),
        )
        return estimate.looping_vram_mb

    def _update_looping_vram(self) -> None:
        self.update_vram_estimation()

    def _prompt_vram_warning(self, looping_mb: float) -> tuple[bool, bool] | None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("VRAM Warning")
        box.setText(f"Looping partition ({looping_mb:.1f} MB) exceeds 150 MB safety limit")
        box.setInformativeText("Excessive VRAM risks device bootloops; select compilation action")

        repair_btn = box.addButton("Auto Repair", QMessageBox.ButtonRole.AcceptRole)
        override_btn = box.addButton("Force Compile", QMessageBox.ButtonRole.ActionRole)
        cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setEscapeButton(cancel_btn)
        box.exec()

        clicked = box.clickedButton()

        if clicked == repair_btn:
            return True, False

        if clicked == override_btn:
            return False, True

        return None

    def _on_compile_clicked(self) -> None:
        if self._video_path is None:
            return

        auto_repair = False
        force_override = False
        looping_mb = self.calculate_looping_vram_mb()

        if looping_mb > 150.0:
            prompt_result = self._prompt_vram_warning(looping_mb)
            if prompt_result is None:
                return
            auto_repair, force_override = prompt_result

        default_out = self._video_path.parent / "bootanimation.zip"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Bootanimation",
            str(default_out),
            "Zip Archives (*.zip);;All Files (*)",
        )
        if not file_path:
            return

        self.start_compilation(
            video_path=self._video_path,
            output_zip=Path(file_path),
            auto_repair_vram=auto_repair,
            force_override_vram=force_override,
        )

    def start_compilation(
        self,
        video_path: Path,
        output_zip: Path,
        compiler: AnimationCompiler | None = None,
        crop: CropRect | None = None,
        auto_repair_vram: bool = False,
        force_override_vram: bool = False,
    ) -> CompileWorker:
        target_crop = crop if crop is not None else self.cropper.get_crop_rect()
        segments = self.timeline.get_segments()

        worker = CompileWorker(
            video_path=video_path,
            output_zip=output_zip,
            segments=segments,
            canvas=self.get_canvas_resolution(),
            fps=self.get_fps(),
            crop=target_crop,
            compiler=compiler,
            parent=self,
            auto_repair_vram=auto_repair_vram,
            force_override_vram=force_override_vram,
        )

        self._active_worker = worker
        self._set_compilation_ui_active(True)

        worker.signals.progress.connect(self._on_compile_progress)
        worker.signals.status_message.connect(self._on_compile_status)
        worker.signals.finished.connect(self._on_compile_finished)
        worker.signals.error.connect(self._on_compile_error)
        worker.signals.cancelled.connect(self._on_compile_cancelled)

        worker.start()
        return worker

    def cancel_compilation(self) -> None:
        if self._active_worker is not None:
            self._active_worker.cancel()

    def _set_compilation_ui_active(self, active: bool) -> None:
        self.compile_btn.setEnabled(not active)
        self.cancel_compile_btn.setEnabled(active)

    def _on_compile_progress(self, progress: float) -> None:
        self.progress_bar.setValue(int(round(progress * 100)))

    def _on_compile_status(self, message: str) -> None:
        self.status_label.setText(message)

    def _on_compile_finished(self, output_path: Path) -> None:
        self._set_compilation_ui_active(False)
        self.progress_bar.setValue(100)
        self.status_label.setText(f"Compiled: {output_path.name}")
        self._active_worker = None

    def _on_compile_error(self, error_message: str) -> None:
        self._set_compilation_ui_active(False)
        self.status_label.setText(f"Error: {error_message}")
        self._active_worker = None

    def _on_compile_cancelled(self) -> None:
        self._set_compilation_ui_active(False)
        self.status_label.setText("Compilation cancelled")
        self._active_worker = None

    def _toggle_playback(self) -> None:
        if self._is_playing():
            self._pause_playback()
            return
        self._start_playback()

    def _is_playing(self) -> bool:
        if (
            self._media_player is not None
            and self._media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        ):
            return True
        return self._playback_timer.isActive()

    def _pause_playback(self) -> None:
        if self._media_player is not None:
            self._media_player.pause()
        self._playback_timer.stop()
        self.play_btn.setText("Play")

    def _start_playback(self) -> None:
        duration = self.timeline.get_duration()
        if duration <= 0.0:
            return

        if self.scrub_slider.maximum() <= 0:
            self.scrub_slider.setRange(0, int(round(duration * 1000.0)))
            self._update_timecode_display(self.timeline.get_current_time())

        current = self.timeline.get_current_time()
        if current >= duration:
            self._seek_to_seconds(0.0)

        if self._can_use_media_player() and self._media_player is not None:
            self._playback_timer.stop()
            self._media_player.play()
            self.play_btn.setText("Pause")
            return

        fps = max(1, self.get_fps())
        self._playback_timer.setInterval(max(10, int(round(1000.0 / fps))))
        self._playback_timer.start()
        self.play_btn.setText("Pause")

    def _can_use_media_player(self) -> bool:
        if self._media_player is None or self._video_path is None:
            return False

        if not self._video_path.exists():
            return False

        return self._media_player.duration() > 0

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._playback_timer.stop()
            self.play_btn.setText("Pause")
        else:
            self.play_btn.setText("Play")

    def _on_timer_tick(self) -> None:
        duration = self.timeline.get_duration()
        if duration <= 0.0:
            self._pause_playback()
            return

        current = self.timeline.get_current_time()
        step = 1.0 / max(1, self.get_fps())
        next_time = current + step

        if next_time >= duration:
            self._seek_to_seconds(duration)
            self._pause_playback()
            return

        self._seek_to_seconds(next_time)

    def _seek_to_seconds(self, sec: float) -> None:
        duration = self.timeline.get_duration()
        if duration > 0.0 and self.scrub_slider.maximum() <= 0:
            self.scrub_slider.setRange(0, int(round(duration * 1000.0)))

        clamped_sec = max(0.0, min(sec, duration if duration > 0.0 else sec))

        self._is_updating_timeline = True
        try:
            self.timeline.set_current_time(clamped_sec)
        finally:
            self._is_updating_timeline = False

        pos_ms = int(round(clamped_sec * 1000.0))
        self._last_seek_ms = pos_ms
        self._is_updating_slider = True
        try:
            self.scrub_slider.setValue(pos_ms)
        finally:
            self._is_updating_slider = False

        self._update_timecode_display(clamped_sec)

        if self._can_use_media_player() and self._media_player is not None:
            self._media_player.setPosition(pos_ms)

    def _on_slider_moved(self, pos_ms: int) -> None:
        if pos_ms == self._last_seek_ms:
            return

        self._last_seek_ms = pos_ms
        sec = pos_ms / 1000.0
        self._is_updating_timeline = True
        try:
            self.timeline.set_current_time(sec)
        finally:
            self._is_updating_timeline = False

        self._update_timecode_display(sec)
        if self._can_use_media_player() and self._media_player is not None:
            self._media_player.setPosition(pos_ms)

    def _on_slider_value_changed(self, pos_ms: int) -> None:
        if self._is_updating_slider:
            return
        self._on_slider_moved(pos_ms)

    def _on_timeline_scrubbed(self, sec: float) -> None:
        if self._is_updating_timeline:
            return
        self._seek_to_seconds(sec)

    def _on_player_position_changed(self, pos_ms: int) -> None:
        if self.scrub_slider.isSliderDown():
            return

        sec = pos_ms / 1000.0
        self._is_updating_timeline = True
        try:
            self.timeline.set_current_time(sec)
        finally:
            self._is_updating_timeline = False

        self._is_updating_slider = True
        try:
            self.scrub_slider.setValue(pos_ms)
        finally:
            self._is_updating_slider = False

        self._update_timecode_display(sec)

    def _on_player_duration_changed(self, duration_ms: int) -> None:
        if duration_ms <= 0:
            return

        self.scrub_slider.setRange(0, duration_ms)
        if self.timeline.get_duration() <= 0.0:
            self.timeline.set_duration(duration_ms / 1000.0)

        self._update_timecode_display(self.timeline.get_current_time())

    def _on_video_frame_changed(self, frame: QVideoFrame) -> None:
        if not frame.isValid():
            return

        img = frame.toImage()
        if not img.isNull():
            self.cropper.set_frame_image(img)

    def _update_timecode_display(self, current_sec: float) -> None:
        duration_sec = self.timeline.get_duration()
        cur_str = self._format_timecode(current_sec)
        dur_str = self._format_timecode(duration_sec)
        self.timecode_label.setText(f"{cur_str} / {dur_str}")

    @staticmethod
    def _format_timecode(seconds: float) -> str:
        total_cs = int(round(max(0.0, seconds) * 100))
        mins = total_cs // 6000
        secs = (total_cs % 6000) / 100.0
        return f"{mins:02d}:{secs:05.2f}"
