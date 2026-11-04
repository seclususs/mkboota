from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mkboota.models import (
    PartConfig,
    SegmentSpec,
    is_valid_hex_color,
    normalize_hex_color,
)


class PartInspector(QWidget):
    """Parameter editor for AOSP bootanimation segment specifications"""

    segment_updated = Signal(SegmentSpec)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_segment: SegmentSpec | None = None
        self._updating: bool = False
        self._fps: int = 30

        self._setup_ui()
        self._connect_signals()
        self.load_segment(None)

    @property
    def fps(self) -> int:
        return self._fps

    def set_fps(self, fps: int) -> None:
        self._fps = max(1, fps)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        form = QFormLayout()
        form.setSpacing(8)

        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("part0")

        self.start_spin = QDoubleSpinBox()
        self.start_spin.setRange(0.0, 3600.0)
        self.start_spin.setSingleStep(0.1)
        self.start_spin.setDecimals(2)
        self.start_spin.setSuffix(" s")

        self.end_spin = QDoubleSpinBox()
        self.end_spin.setRange(0.0, 3600.0)
        self.end_spin.setSingleStep(0.1)
        self.end_spin.setDecimals(2)
        self.end_spin.setSuffix(" s")

        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.1, 3600.0)
        self.duration_spin.setSingleStep(0.1)
        self.duration_spin.setDecimals(2)
        self.duration_spin.setSuffix(" s")

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Standard", "p")
        self.mode_combo.addItem("Loop", "p")
        self.mode_combo.addItem("Repeat", "p")
        self.mode_combo.addItem("Complete", "c")

        self.count_label = QLabel("Repeat Count:")
        self.count_spin = QSpinBox()
        self.count_spin.setRange(0, 999999)

        self.pause_spin = QSpinBox()
        self.pause_spin.setRange(0, 999999)

        bg_layout = QHBoxLayout()
        bg_layout.setContentsMargins(0, 0, 0, 0)
        bg_layout.setSpacing(6)
        self.bg_edit = QLineEdit()
        self.bg_edit.setPlaceholderText("#000000")
        self.color_btn = QPushButton("Pick Color")
        bg_layout.addWidget(self.bg_edit, stretch=1)
        bg_layout.addWidget(self.color_btn)

        form.addRow(QLabel("Folder Name:"), self.folder_edit)
        form.addRow(QLabel("Start Time:"), self.start_spin)
        form.addRow(QLabel("End Time:"), self.end_spin)
        form.addRow(QLabel("Duration:"), self.duration_spin)
        form.addRow(QLabel("Playback Mode:"), self.mode_combo)
        form.addRow(self.count_label, self.count_spin)
        form.addRow(QLabel("Pause Frames:"), self.pause_spin)
        form.addRow(QLabel("Background:"), bg_layout)

        layout.addLayout(form)
        layout.addStretch()

    def _connect_signals(self) -> None:
        self.folder_edit.textChanged.connect(self._on_field_changed)
        self.start_spin.valueChanged.connect(self._on_start_time_changed)
        self.start_spin.editingFinished.connect(self._on_start_time_committed)
        self.end_spin.valueChanged.connect(self._on_end_time_changed)
        self.end_spin.editingFinished.connect(self._on_end_time_committed)
        self.duration_spin.editingFinished.connect(self._on_duration_committed)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.count_spin.valueChanged.connect(self._on_count_changed)
        self.pause_spin.valueChanged.connect(self._on_field_changed)
        self.bg_edit.textChanged.connect(self._on_field_changed)
        self.color_btn.clicked.connect(self._on_pick_color)

    def _on_start_time_changed(self, value: float) -> None:
        if self._updating:
            return

        if self.end_spin.value() - value >= 0.1 - 1e-6:
            self._updating = True
            self.duration_spin.setValue(self.end_spin.value() - value)
            self._updating = False
            self._on_field_changed()

    def _on_start_time_committed(self) -> None:
        if self._updating or self._current_segment is None:
            return

        start_val = self.start_spin.value()
        min_duration = 0.1
        self._updating = True

        if self.end_spin.value() - start_val < min_duration - 1e-6:
            new_end = min(3600.0, start_val + min_duration)
            self.end_spin.setValue(new_end)
            self.start_spin.setValue(max(0.0, new_end - min_duration))

        new_dur = max(min_duration, self.end_spin.value() - self.start_spin.value())
        self.duration_spin.setValue(new_dur)
        self._updating = False
        self._on_field_changed()

    def _on_end_time_changed(self, value: float) -> None:
        if self._updating:
            return

        if value - self.start_spin.value() >= 0.1 - 1e-6:
            self._updating = True
            self.duration_spin.setValue(value - self.start_spin.value())
            self._updating = False
            self._on_field_changed()

    def _on_end_time_committed(self) -> None:
        if self._updating or self._current_segment is None:
            return

        end_val = self.end_spin.value()
        min_duration = 0.1
        self._updating = True

        if end_val - self.start_spin.value() < min_duration - 1e-6:
            new_start = max(0.0, end_val - min_duration)
            self.start_spin.setValue(new_start)
            self.end_spin.setValue(min(3600.0, new_start + min_duration))

        new_dur = max(min_duration, self.end_spin.value() - self.start_spin.value())
        self.duration_spin.setValue(new_dur)
        self._updating = False
        self._on_field_changed()

    def _on_duration_committed(self) -> None:
        if self._updating or self._current_segment is None:
            return

        start_val = self.start_spin.value()
        min_duration = 0.1
        new_end = min(3600.0, start_val + self.duration_spin.value())

        self._updating = True
        if new_end - start_val < min_duration - 1e-6:
            self.start_spin.setValue(max(0.0, new_end - min_duration))

        self.end_spin.setValue(new_end)
        self.duration_spin.setValue(max(min_duration, new_end - self.start_spin.value()))
        self._updating = False
        self._on_field_changed()

    def _on_mode_changed(self) -> None:
        mode = self.mode_combo.currentText()
        is_repeat = mode == "Repeat"
        self.count_label.setVisible(is_repeat)
        self.count_spin.setVisible(is_repeat)
        self.count_spin.setEnabled(is_repeat)

        if not self._updating:
            self._updating = True

            if mode == "Standard":
                self.count_spin.setValue(1)
            elif mode in ("Loop", "Complete"):
                self.count_spin.setValue(0)
            elif is_repeat and self.count_spin.value() < 2:
                self.count_spin.setValue(2)

            self._updating = False
            self._on_field_changed()

    def _on_count_changed(self, value: int) -> None:
        if self._updating or self.mode_combo.currentText() == "Complete":
            return

        self._updating = True

        if value == 0:
            self.mode_combo.setCurrentText("Loop")
        elif value == 1:
            self.mode_combo.setCurrentText("Standard")
        else:
            self.mode_combo.setCurrentText("Repeat")

        is_repeat = self.mode_combo.currentText() == "Repeat"
        self.count_label.setVisible(is_repeat)
        self.count_spin.setVisible(is_repeat)
        self.count_spin.setEnabled(is_repeat)
        self._updating = False
        self._on_field_changed()

    def _get_mode_parameters(self) -> tuple[str, int]:
        mode = self.mode_combo.currentText()

        if mode == "Standard":
            return "p", 1

        if mode == "Loop":
            return "p", 0

        if mode == "Complete":
            return "c", 0

        return "p", self.count_spin.value()

    def load_segment(self, spec: SegmentSpec | None) -> None:
        self._updating = True
        self._current_segment = spec

        if spec is None:
            self._set_fields_enabled(False)
            self._reset_fields()
            self._updating = False
            return

        self._set_fields_enabled(True)
        self.folder_edit.setText(spec.folder_name or "")

        if spec.type_code == "c":
            self.mode_combo.setCurrentText("Complete")
        elif spec.count == 0:
            self.mode_combo.setCurrentText("Loop")
        elif spec.count == 1:
            self.mode_combo.setCurrentText("Standard")
        else:
            self.mode_combo.setCurrentText("Repeat")

        is_repeat = self.mode_combo.currentText() == "Repeat"
        self.count_label.setVisible(is_repeat)
        self.count_spin.setVisible(is_repeat)
        self.count_spin.setEnabled(is_repeat)
        self.count_spin.setValue(spec.count)

        self.start_spin.setValue(spec.start_time)
        end_time = spec.end_time if spec.end_time is not None else spec.start_time + 1.0
        clamped_end = max(spec.start_time + 0.1, end_time)
        self.end_spin.setValue(clamped_end)
        self.duration_spin.setValue(max(0.1, clamped_end - spec.start_time))

        self.pause_spin.setValue(spec.pause)
        self.bg_edit.setText(spec.bg_color)
        self.bg_edit.setStyleSheet("")

        self._updating = False

    def set_part(self, part: PartConfig) -> None:
        spec = SegmentSpec(
            start_time=0.0,
            end_time=None,
            type_code=part.type_code,
            count=part.count,
            pause=part.pause,
            folder_name=part.folder_name,
            bg_color=part.bg_color,
            clock_pos=part.clock_pos,
        )
        self.load_segment(spec)

    def get_part_config(self) -> PartConfig:
        type_code, count = self._get_mode_parameters()
        bg_text = self.bg_edit.text().strip()
        bg_color = normalize_hex_color(bg_text) if is_valid_hex_color(bg_text) else "#000000"
        folder = self.folder_edit.text().strip() or "part0"
        current_clock = self._current_segment.clock_pos if self._current_segment else -1
        return PartConfig(
            type_code=type_code,
            count=count,
            pause=self.pause_spin.value(),
            folder_name=folder,
            bg_color=bg_color,
            clock_pos=current_clock,
        )

    def _on_pick_color(self) -> None:
        current_hex = self.bg_edit.text().strip()
        initial_color = (
            QColor(normalize_hex_color(current_hex))
            if is_valid_hex_color(current_hex)
            else QColor("#000000")
        )
        color = QColorDialog.getColor(initial_color, self)
        if color.isValid():
            self.bg_edit.setText(color.name().upper())

    def _set_fields_enabled(self, enabled: bool) -> None:
        self.folder_edit.setEnabled(enabled)
        self.start_spin.setEnabled(enabled)
        self.end_spin.setEnabled(enabled)
        self.duration_spin.setEnabled(enabled)
        self.mode_combo.setEnabled(enabled)
        is_repeat = enabled and self.mode_combo.currentText() == "Repeat"
        self.count_spin.setEnabled(is_repeat)
        self.count_spin.setVisible(is_repeat)
        self.count_label.setVisible(is_repeat)
        self.pause_spin.setEnabled(enabled)
        self.bg_edit.setEnabled(enabled)
        self.color_btn.setEnabled(enabled)

    def _reset_fields(self) -> None:
        self.folder_edit.setText("")
        self.start_spin.setValue(0.0)
        self.end_spin.setValue(0.0)
        self.duration_spin.setValue(0.1)
        self.mode_combo.setCurrentIndex(0)
        self.count_spin.setValue(0)
        self.pause_spin.setValue(0)
        self.bg_edit.setText("")
        self.bg_edit.setStyleSheet("")

    def get_current_segment(self) -> SegmentSpec | None:
        return self._current_segment

    def _on_field_changed(self) -> None:
        if self._updating or self._current_segment is None:
            return

        bg_text = self.bg_edit.text().strip()
        if not is_valid_hex_color(bg_text):
            self.bg_edit.setStyleSheet("border: 1px solid #e74c3c;")
            return

        self.bg_edit.setStyleSheet("")
        type_code, count = self._get_mode_parameters()
        folder = self.folder_edit.text().strip() or self._current_segment.folder_name

        updated = SegmentSpec(
            start_time=self.start_spin.value(),
            end_time=self.end_spin.value(),
            type_code=type_code,
            count=count,
            pause=self.pause_spin.value(),
            folder_name=folder,
            bg_color=normalize_hex_color(bg_text),
            clock_pos=self._current_segment.clock_pos,
        )
        self._current_segment = updated
        self.segment_updated.emit(updated)
