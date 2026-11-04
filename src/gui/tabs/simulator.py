from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt, QTimer
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QImage,
    QPainter,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui.workers.loader import SimulatorLoadWorker, SimulatorPart
from mkboota.models import FrameTrim


class SimulatorTab(QWidget):
    """AOSP bootanimation playback simulator tab"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._state: str = "stopped"
        self._boot_complete: bool = False
        self._fps: int = 30
        self._canvas_width: int = 0
        self._canvas_height: int = 0
        self._parts: list[SimulatorPart[QPixmap]] = []
        self._trims: dict[str, FrameTrim] = {}
        self._current_part_idx: int = 0
        self._current_frame_idx: int = 0
        self._current_loop_iteration: int = 0
        self._pause_counter: int = 0
        self._loader_worker: SimulatorLoadWorker | None = None
        self._active_path: Path | None = None

        self._timer: QTimer = QTimer(self)
        self._timer.timeout.connect(self._advance_playback_frame)

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        main_layout.addLayout(self._create_header_controls())

        canvas_group = QGroupBox("Playback")
        canvas_layout = QVBoxLayout(canvas_group)
        self.display_label = QLabel()
        self.display_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display_label.setStyleSheet("background-color: #000000; border-radius: 4px;")
        self.display_label.setMinimumSize(240, 320)
        self.display_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        canvas_layout.addWidget(self.display_label)
        main_layout.addWidget(canvas_group, stretch=1)

        main_layout.addLayout(self._create_load_progress_layout())
        main_layout.addLayout(self._create_controls())
        main_layout.addLayout(self._create_status_bar())

    def _create_header_controls(self) -> QHBoxLayout:
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        self.load_btn = QPushButton("Load Animation")
        self.archive_info_label = QLabel("No archive loaded")
        self.archive_info_label.setProperty("muted", "true")

        header_layout.addWidget(self.load_btn)
        header_layout.addWidget(self.archive_info_label, stretch=1)
        return header_layout

    def _create_load_progress_layout(self) -> QVBoxLayout:
        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(4)

        self.load_progress_bar = QProgressBar()
        self.load_progress_bar.setRange(0, 100)
        self.load_progress_bar.setValue(0)
        self.load_progress_bar.setTextVisible(True)
        self.load_progress_bar.setVisible(False)

        self.load_status_label = QLabel("")
        self.load_status_label.setProperty("muted", "true")
        self.load_status_label.setVisible(False)

        progress_layout.addWidget(self.load_progress_bar)
        progress_layout.addWidget(self.load_status_label)
        return progress_layout

    def _create_controls(self) -> QHBoxLayout:
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(8)

        self.step_back_btn = QPushButton("◀ Step")
        self.step_back_btn.setEnabled(False)

        self.play_btn = QPushButton("Play")
        self.play_btn.setProperty("primary", "true")
        self.play_btn.setEnabled(False)

        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setEnabled(False)

        self.step_forward_btn = QPushButton("Step ▶")
        self.step_forward_btn.setEnabled(False)

        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setEnabled(False)

        self.boot_complete_btn = QPushButton("Simulate Boot Complete")
        self.boot_complete_btn.setEnabled(False)

        controls_layout.addWidget(self.step_back_btn)
        controls_layout.addWidget(self.play_btn)
        controls_layout.addWidget(self.pause_btn)
        controls_layout.addWidget(self.step_forward_btn)
        controls_layout.addWidget(self.reset_btn)
        controls_layout.addWidget(self.boot_complete_btn)
        controls_layout.addStretch()

        return controls_layout

    def _create_status_bar(self) -> QHBoxLayout:
        status_layout = QHBoxLayout()
        status_layout.setSpacing(16)

        self.state_label = QLabel("State: STOPPED")
        self.state_label.setStyleSheet("font-weight: 600; color: #8a8a96;")

        self.frame_info_label = QLabel("Part: - | Frame: - | Loop: -")
        self.frame_info_label.setProperty("muted", "true")

        status_layout.addWidget(self.state_label)
        status_layout.addWidget(self.frame_info_label)
        status_layout.addStretch()

        return status_layout

    def _connect_signals(self) -> None:
        self.load_btn.clicked.connect(self._on_browse_clicked)
        self.step_back_btn.clicked.connect(lambda: self.step_frame(-1))
        self.play_btn.clicked.connect(self.play)
        self.pause_btn.clicked.connect(self.pause)
        self.step_forward_btn.clicked.connect(lambda: self.step_frame(1))
        self.reset_btn.clicked.connect(self.reset)
        self.boot_complete_btn.clicked.connect(self.simulate_boot_complete)

    def _on_browse_clicked(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Bootanimation",
            "",
            "Zip Archives (*.zip);;All Files (*)",
        )
        if file_path:
            self.load_archive(Path(file_path))

    def browse_animation(self) -> None:
        self._on_browse_clicked()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if not event.mimeData().hasUrls():
            return
        for url in event.mimeData().urls():
            if url.toLocalFile().lower().endswith(".zip"):
                event.acceptProposedAction()
                return

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            path_str = url.toLocalFile()
            if path_str.lower().endswith(".zip"):
                self.load_archive(Path(path_str))
                event.acceptProposedAction()
                return

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._render_current_frame()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.cancel_loading()
        super().closeEvent(event)

    def cancel_loading(self) -> None:
        if self._loader_worker is None:
            return

        try:
            self._loader_worker.progress.disconnect()
            self._loader_worker.loaded.disconnect()
            self._loader_worker.error.disconnect()
        except RuntimeError:
            pass

        if self._loader_worker.isRunning():
            self._loader_worker.cancel()
            self._loader_worker.wait()

    def wait_for_load(self, timeout_ms: int = 5000) -> bool:
        if self._loader_worker is None:
            return True
        finished = self._loader_worker.wait(timeout_ms)
        QCoreApplication.processEvents()
        return finished

    def load_archive(self, archive_path: Path | str, wait: bool = False) -> None:
        path = Path(archive_path)
        if not path.exists():
            self.archive_info_label.setText(f"File not found: {path.name}")
            return

        self.cancel_loading()

        self.play_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.step_back_btn.setEnabled(False)
        self.step_forward_btn.setEnabled(False)
        self.reset_btn.setEnabled(False)
        self.boot_complete_btn.setEnabled(False)

        self.load_progress_bar.setValue(0)
        self.load_progress_bar.setVisible(True)
        self.load_status_label.setText(f"Loading {path.name}")
        self.load_status_label.setVisible(True)

        self._active_path = path
        worker = SimulatorLoadWorker(path, self)
        worker.progress.connect(self._on_load_progress)
        worker.loaded.connect(self._on_load_finished)
        worker.error.connect(self._on_load_error)
        self._loader_worker = worker
        worker.start()
        if wait:
            self.wait_for_load()

    def _on_load_progress(self, current: int, total: int, message: str) -> None:
        pct = int((current / total) * 100) if total > 0 else 0
        self.load_progress_bar.setValue(pct)
        self.load_status_label.setText(message)

    def _on_load_finished(
        self,
        payload: tuple[list[SimulatorPart[QImage]], dict[str, FrameTrim], int, int],
    ) -> None:
        parts, trims, canvas_w, canvas_h = payload
        fps = self._loader_worker.fps if self._loader_worker is not None else 30

        gui_parts: list[SimulatorPart[QPixmap]] = []
        for part in parts:
            pixmaps = [
                QPixmap.fromImage(frame) if isinstance(frame, QImage) else frame
                for frame in part.frames
            ]
            gui_parts.append(
                SimulatorPart(
                    folder_name=part.folder_name,
                    type_code=part.type_code,
                    count=part.count,
                    pause=part.pause,
                    frames=pixmaps,
                    bg_color=part.bg_color,
                    clock_pos=part.clock_pos,
                    frame_names=part.frame_names,
                )
            )

        self._canvas_width = canvas_w
        self._canvas_height = canvas_h
        self._fps = max(1, fps)
        self._parts = gui_parts
        self._trims = dict(trims)

        self.load_progress_bar.setVisible(False)
        self.load_status_label.setVisible(False)
        name = self._active_path.name if self._active_path is not None else "Archive"
        self.archive_info_label.setText(
            f"{name} ({canvas_w}x{canvas_h} @ {fps} fps, {len(gui_parts)} parts)"
        )
        self.reset()

    def _on_load_error(self, message: str) -> None:
        self.load_progress_bar.setVisible(False)
        self.load_status_label.setVisible(False)
        self.archive_info_label.setText(f"Load failed: {message}")
        QMessageBox.warning(self, "Load Error", message)

    def load_parts_from_data(self, parts: list[SimulatorPart[QPixmap]], fps: int = 30) -> None:
        self.cancel_loading()
        self._trims.clear()
        self._parts = list(parts)
        self._fps = max(1, fps)

        if parts and parts[0].frames:
            self._canvas_width = parts[0].frames[0].width()
            self._canvas_height = parts[0].frames[0].height()

        self.archive_info_label.setText(f"Synthetic ({len(parts)} parts @ {fps} fps)")
        self.reset()

    def play(self) -> None:
        if not self._parts:
            return

        if self._state == "completed":
            self.reset()

        self._state = "playing"
        self.play_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.step_back_btn.setEnabled(True)
        self.step_forward_btn.setEnabled(True)
        self.reset_btn.setEnabled(True)
        self.boot_complete_btn.setEnabled(True)

        interval = max(1, int(round(1000.0 / self._fps)))
        self._timer.start(interval)
        self._update_status_display()

    def pause(self) -> None:
        self._state = "paused"
        self._timer.stop()
        self.play_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.step_back_btn.setEnabled(True)
        self.step_forward_btn.setEnabled(True)
        self._update_status_display()

    def reset(self) -> None:
        self._timer.stop()
        self._state = "stopped"
        self._boot_complete = False
        self._current_part_idx = 0
        self._current_frame_idx = 0
        self._current_loop_iteration = 0
        self._pause_counter = 0

        has_data = bool(self._parts)
        self.play_btn.setEnabled(has_data)
        self.pause_btn.setEnabled(False)
        self.step_back_btn.setEnabled(has_data)
        self.step_forward_btn.setEnabled(has_data)
        self.reset_btn.setEnabled(has_data)
        self.boot_complete_btn.setEnabled(has_data)

        self._render_current_frame()
        self._update_status_display()

    def simulate_boot_complete(self) -> None:
        self._boot_complete = True
        self._update_status_display()

    def is_boot_complete(self) -> bool:
        return self._boot_complete

    def get_state(self) -> str:
        return self._state

    def step_frame(self, delta: int | None = None) -> None:
        if delta is None:
            self._advance_playback_frame()
            return

        if self._state == "playing":
            self.pause()

        if not self._parts:
            return

        if self._current_part_idx >= len(self._parts):
            self._current_part_idx = len(self._parts) - 1
            last_part = self._parts[self._current_part_idx]
            self._current_frame_idx = len(last_part.frames) - 1 if delta < 0 else 0
            self._state = "paused"
            self._render_current_frame()
            self._update_status_display()
            return

        part = self._parts[self._current_part_idx]
        if not part.frames:
            return

        num_frames = len(part.frames)
        self._current_frame_idx = (self._current_frame_idx + delta) % num_frames
        self._render_current_frame()
        self._update_status_display()

    def _advance_playback_frame(self) -> None:
        if not self._parts or self._current_part_idx >= len(self._parts):
            self._complete_playback()
            return

        part = self._parts[self._current_part_idx]

        if self._pause_counter > 0:
            self._decrement_pause_counter(part)
            return

        self._render_current_frame()
        self._current_frame_idx += 1

        if self._current_frame_idx >= len(part.frames):
            self._handle_frame_boundary(part)

        self._update_status_display()

    def _decrement_pause_counter(self, part: SimulatorPart[QPixmap]) -> None:
        self._pause_counter -= 1
        if self._pause_counter == 0:
            self._handle_iteration_finished(part)

        self._update_status_display()

    def _handle_frame_boundary(self, part: SimulatorPart[QPixmap]) -> None:
        self._current_loop_iteration += 1
        if part.pause > 0:
            self._pause_counter = part.pause
            return

        self._handle_iteration_finished(part)

    def _handle_iteration_finished(self, part: SimulatorPart[QPixmap]) -> None:
        if self._boot_complete and part.type_code == "p":
            self._complete_playback()
            return

        if part.count > 0 and self._current_loop_iteration >= part.count:
            self._advance_to_next_part()
            return

        self._current_frame_idx = 0

    def _advance_to_next_part(self) -> None:
        self._current_part_idx += 1
        self._current_frame_idx = 0
        self._current_loop_iteration = 0
        self._pause_counter = 0

        if self._current_part_idx >= len(self._parts):
            self._complete_playback()

    def _complete_playback(self) -> None:
        self._state = "completed"
        self._timer.stop()
        self.play_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.step_back_btn.setEnabled(True)
        self.step_forward_btn.setEnabled(True)
        self._update_status_display()

    def _create_composite_frame(
        self, part: SimulatorPart[QPixmap], frame_name: str, frame_pixmap: QPixmap
    ) -> QPixmap:
        canvas_w = self._canvas_width if self._canvas_width > 0 else frame_pixmap.width()
        canvas_h = self._canvas_height if self._canvas_height > 0 else frame_pixmap.height()
        if canvas_w <= 0 or canvas_h <= 0:
            return frame_pixmap

        base = QPixmap(canvas_w, canvas_h)
        col = QColor(part.bg_color)
        base.fill(col if col.isValid() else QColor("#000000"))

        trim = self._trims.get(f"{part.folder_name}/{frame_name}")
        if trim is None:
            trim = self._trims.get(frame_name)

        painter = QPainter(base)

        if trim is not None:
            painter.drawPixmap(trim.offset_x, trim.offset_y, frame_pixmap)
        elif frame_pixmap.width() == canvas_w and frame_pixmap.height() == canvas_h:
            painter.drawPixmap(0, 0, frame_pixmap)
        else:
            offset_x = (canvas_w - frame_pixmap.width()) // 2
            offset_y = (canvas_h - frame_pixmap.height()) // 2
            painter.drawPixmap(offset_x, offset_y, frame_pixmap)

        painter.end()
        return base

    def _render_current_frame(self) -> None:
        if not self._parts or self._current_part_idx >= len(self._parts):
            return

        part = self._parts[self._current_part_idx]
        if not part.frames:
            return

        idx = min(self._current_frame_idx, len(part.frames) - 1)
        frame_pixmap = part.frames[idx]
        frame_name = part.frame_names[idx] if idx < len(part.frame_names) else f"{idx:04d}.png"
        composite = self._create_composite_frame(part, frame_name, frame_pixmap)

        canvas_size = self.display_label.size()
        scaled = composite.scaled(
            canvas_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.display_label.setPixmap(scaled)

    def _update_status_display(self) -> None:
        state_color = {
            "stopped": "#8a8a96",
            "playing": "#2ecc71",
            "paused": "#f1c40f",
            "completed": "#4a90e2",
        }.get(self._state, "#8a8a96")

        boot_text = " [BOOT COMPLETED]" if self._boot_complete else ""
        self.state_label.setText(f"State: {self._state.upper()}{boot_text}")
        self.state_label.setStyleSheet(f"font-weight: 600; color: {state_color};")

        if self._parts and self._current_part_idx < len(self._parts):
            part = self._parts[self._current_part_idx]
            frame_num = min(self._current_frame_idx + 1, max(1, len(part.frames)))
            pause_info = f" (pause {self._pause_counter})" if self._pause_counter > 0 else ""
            self.frame_info_label.setText(
                f"Part: {part.folder_name} ({self._current_part_idx + 1}/{len(self._parts)}) | "
                f"Frame: {frame_num}/{max(1, len(part.frames))}{pause_info} | "
                f"Loop: {self._current_loop_iteration}"
            )
        else:
            self.frame_info_label.setText("Part: - | Frame: - | Loop: -")
