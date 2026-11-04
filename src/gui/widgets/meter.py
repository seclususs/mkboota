from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from mkboota.models import DeviceProfile

_COLOR_SAFE = "#2ecc71"
_COLOR_CAUTION = "#f1c40f"
_COLOR_HAZARD = "#e74c3c"


class VramMeter(QWidget):
    """VRAM usage monitor and bootloop hazard gauge"""

    hazard_level_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._device_profile: DeviceProfile = DeviceProfile.STANDARD
        self._max_vram_mb: float = DeviceProfile.STANDARD.max_vram_mb
        self._caution_vram_mb: float = self._max_vram_mb * (2.0 / 3.0)

        self._total_vram_mb: float = 0.0
        self._looping_vram_mb: float = 0.0
        self._hazard_level: str = "safe"
        self._status_text: str = "Safe (<100 MB)"
        self._advice: str = "Safe: VRAM within limits"
        self._warnings: tuple[str, ...] = ()

        self._setup_ui()
        self._update_display()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        profile_layout = QHBoxLayout()
        profile_layout.setContentsMargins(0, 0, 0, 0)
        profile_layout.setSpacing(6)
        profile_layout.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        self.profile_combo.addItem("Standard (150 MB)", DeviceProfile.STANDARD)
        self.profile_combo.addItem("Android Go (45 MB)", DeviceProfile.ANDROID_GO)
        self.profile_combo.addItem("Flagship (300 MB)", DeviceProfile.FLAGSHIP)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_combo_changed)
        profile_layout.addWidget(self.profile_combo, stretch=1)
        layout.addLayout(profile_layout)

        self.status_label = QLabel(self._status_text)
        self.metrics_label = QLabel("Looping: 0.0 MB | Total: 0.0 MB")
        self.metrics_label.setProperty("muted", "true")

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 200)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(12)

        self.advice_label = QLabel(self._advice)
        self.advice_label.setWordWrap(True)

        self.warnings_label = QLabel("")
        self.warnings_label.setWordWrap(True)
        self.warnings_label.setStyleSheet("color: #e74c3c; font-size: 11px;")
        self.warnings_label.setVisible(False)

        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.metrics_label)
        layout.addWidget(self.advice_label)
        layout.addWidget(self.warnings_label)

    def _on_profile_combo_changed(self) -> None:
        data = self.profile_combo.currentData()
        if isinstance(data, DeviceProfile):
            self.set_device_profile(data)

    def set_device_profile(self, profile: DeviceProfile) -> None:
        self._device_profile = profile
        self._max_vram_mb = profile.max_vram_mb
        self._caution_vram_mb = profile.max_vram_mb * (2.0 / 3.0)

        idx = self.profile_combo.findData(profile)
        if idx >= 0 and self.profile_combo.currentIndex() != idx:
            self.profile_combo.blockSignals(True)
            self.profile_combo.setCurrentIndex(idx)
            self.profile_combo.blockSignals(False)

        self._evaluate_metrics()
        self._update_display()
        self.hazard_level_changed.emit(self._hazard_level)

    def get_device_profile(self) -> DeviceProfile:
        return self._device_profile

    def update_metrics(
        self,
        total_vram_mb: float,
        looping_vram_mb: float,
        warnings: Sequence[str] = (),
    ) -> None:
        self._total_vram_mb = max(0.0, float(total_vram_mb))
        self._looping_vram_mb = max(0.0, float(looping_vram_mb))
        self._warnings = tuple(warnings)

        self._evaluate_metrics()
        self._update_display()
        self.hazard_level_changed.emit(self._hazard_level)

    def _evaluate_metrics(self) -> None:
        caution_int = int(round(self._caution_vram_mb))
        max_int = int(round(self._max_vram_mb))

        if self._looping_vram_mb < self._caution_vram_mb:
            self._hazard_level = "safe"
            self._status_text = f"Safe (<{caution_int} MB)"
            self._advice = "Safe: VRAM within limits"
        elif self._looping_vram_mb <= self._max_vram_mb:
            self._hazard_level = "caution"
            self._status_text = f"Caution ({caution_int}-{max_int} MB)"
            self._advice = f"Caution: Looping approaches {max_int} MB limit"
        else:
            self._hazard_level = "hazard"
            self._status_text = f"Bootloop Hazard (>{max_int} MB)"
            self._advice = f"Hazard: Looping exceeds {max_int} MB safety limit"

    def _update_display(self) -> None:
        chunk_color = self._get_level_color()
        max_val = max(
            int(round(self._max_vram_mb * 1.2)),
            int(round(self._total_vram_mb * 1.1)),
        )
        self.progress_bar.setRange(0, max_val)
        self.progress_bar.setValue(int(round(self._looping_vram_mb)))
        self.progress_bar.setStyleSheet(
            f"QProgressBar::chunk {{ background-color: {chunk_color}; border-radius: 3px; }}"
        )

        self.status_label.setText(self._status_text)
        self.status_label.setStyleSheet(f"font-weight: 600; color: {chunk_color};")

        self.metrics_label.setText(
            f"Looping: {self._looping_vram_mb:.1f} MB | Total: {self._total_vram_mb:.1f} MB"
        )
        self.advice_label.setText(self._advice)

        warn_text = "\n".join(self._warnings)
        self.warnings_label.setText(warn_text)
        self.warnings_label.setVisible(bool(self._warnings))

    def _get_level_color(self) -> str:
        if self._hazard_level == "safe":
            return _COLOR_SAFE

        if self._hazard_level == "caution":
            return _COLOR_CAUTION

        return _COLOR_HAZARD

    def get_hazard_level(self) -> str:
        return self._hazard_level

    def get_status_text(self) -> str:
        return self._status_text

    def get_advice(self) -> str:
        return self._advice

    def get_looping_vram(self) -> float:
        return self._looping_vram_mb

    def get_total_vram(self) -> float:
        return self._total_vram_mb

    def get_warnings_text(self) -> str:
        return "\n".join(self._warnings)
