from __future__ import annotations

from pathlib import Path
import zipfile

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.workers.repairer import RepairWorker
from mkboota.inspector import ArchiveInspectInfo, inspect_archive
from mkboota.models import (
    RepairBudgetOptions,
    RepairReport,
    VerificationReport,
    bytes_to_mb,
)
from mkboota.repairer import AnimationRepairer
from mkboota.verifier import BootAnimationVerifier


class InspectorTab(QWidget):
    """Archive verification, inspection, and budget repair interface"""

    simulate_requested = Signal(Path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._archive_path: Path | None = None
        self._repaired_path: Path | None = None
        self._active_worker: RepairWorker | None = None

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        main_layout.addLayout(self._create_header_controls())

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._create_overview_panel())
        splitter.addWidget(self._create_details_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        main_layout.addWidget(splitter, stretch=1)

        main_layout.addWidget(self._create_repair_panel())

    def _create_header_controls(self) -> QHBoxLayout:
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        self.archive_path_edit = QLineEdit()
        self.archive_path_edit.setPlaceholderText("Select or drop bootanimation.zip archive")
        self.archive_path_edit.setReadOnly(True)

        self.open_archive_btn = QPushButton("Browse Archive")
        header_layout.addWidget(self.archive_path_edit, stretch=1)
        header_layout.addWidget(self.open_archive_btn)

        return header_layout

    def _create_overview_panel(self) -> QGroupBox:
        group = QGroupBox("Overview")
        layout = QHBoxLayout(group)
        layout.setSpacing(16)

        self.dimensions_label = QLabel("Dimensions: -")
        self.fps_label = QLabel("Framerate: -")
        self.archive_size_label = QLabel("File Size: -")
        self.looping_vram_label = QLabel("Looping VRAM: -")
        self.compliance_label = QLabel("AOSP Compliance: -")

        layout.addWidget(self.dimensions_label)
        layout.addWidget(self.fps_label)
        layout.addWidget(self.archive_size_label)
        layout.addWidget(self.looping_vram_label)
        layout.addWidget(self.compliance_label)
        layout.addStretch()

        return group

    def _create_details_panel(self) -> QWidget:
        panel = QWidget()
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        table_group = QGroupBox("Partitions")
        table_layout = QVBoxLayout(table_group)
        self.parts_table = QTableWidget(0, 7)
        self.parts_table.setHorizontalHeaderLabels(
            ["Folder", "Type", "Count", "Pause", "Frames", "Resolution", "Trim"]
        )
        header = self.parts_table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table_layout.addWidget(self.parts_table)
        layout.addWidget(table_group, stretch=3)

        desc_group = QGroupBox("desc.txt Preview")
        desc_layout = QVBoxLayout(desc_group)
        self.desc_preview = QPlainTextEdit()
        self.desc_preview.setReadOnly(True)
        desc_layout.addWidget(self.desc_preview)
        layout.addWidget(desc_group, stretch=2)

        return panel

    def _create_repair_panel(self) -> QGroupBox:
        group = QGroupBox("Auto-Repair")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        options_layout = QHBoxLayout()
        options_layout.setSpacing(12)

        self.vram_slider_label = QLabel("Target Max VRAM: 150 MB")
        self.vram_slider = QSlider(Qt.Orientation.Horizontal)
        self.vram_slider.setRange(50, 300)
        self.vram_slider.setValue(150)
        self.vram_slider.setFixedWidth(160)

        self.decimate_checkbox = QCheckBox("Decimate looping frames")
        self.hold_last_checkbox = QCheckBox("Convert looping parts to hold-last")

        options_layout.addWidget(self.vram_slider_label)
        options_layout.addWidget(self.vram_slider)
        options_layout.addWidget(self.decimate_checkbox)
        options_layout.addWidget(self.hold_last_checkbox)
        options_layout.addStretch()
        layout.addLayout(options_layout)

        action_layout = QHBoxLayout()
        action_layout.setSpacing(8)

        self.repair_btn = QPushButton("Safe Auto-Repair")
        self.repair_btn.setProperty("primary", "true")
        self.repair_btn.setEnabled(False)

        self.cancel_repair_btn = QPushButton("Cancel")
        self.cancel_repair_btn.setEnabled(False)

        self.open_sim_btn = QPushButton("Open in Simulator")
        self.open_sim_btn.setEnabled(False)

        self.repair_progress = QProgressBar()
        self.repair_progress.setRange(0, 100)
        self.repair_progress.setValue(0)
        self.repair_progress.setFixedHeight(16)

        action_layout.addWidget(self.repair_btn)
        action_layout.addWidget(self.cancel_repair_btn)
        action_layout.addWidget(self.open_sim_btn)
        action_layout.addWidget(self.repair_progress, stretch=1)
        layout.addLayout(action_layout)

        self.repair_summary_label = QLabel("")
        self.repair_summary_label.setWordWrap(True)
        self.repair_summary_label.setVisible(False)
        layout.addWidget(self.repair_summary_label)

        return group

    def _connect_signals(self) -> None:
        self.open_archive_btn.clicked.connect(self._on_browse_archive)
        self.vram_slider.valueChanged.connect(self._on_vram_slider_changed)
        self.repair_btn.clicked.connect(self._on_repair_clicked)
        self.cancel_repair_btn.clicked.connect(self.cancel_repair)
        self.open_sim_btn.clicked.connect(self._on_open_simulator_clicked)

    def _on_browse_archive(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Bootanimation",
            "",
            "Zip Archives (*.zip);;All Files (*)",
        )
        if file_path:
            self.load_archive(Path(file_path))

    def browse_archive(self) -> None:
        self._on_browse_archive()

    def _on_vram_slider_changed(self, value: int) -> None:
        self.vram_slider_label.setText(f"Target Max VRAM: {value} MB")

    def _on_open_simulator_clicked(self) -> None:
        target = self._repaired_path if self._repaired_path is not None else self._archive_path
        if target is not None:
            self.simulate_requested.emit(target)

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

    def load_archive(self, zip_path: Path) -> None:
        self._archive_path = zip_path
        self._repaired_path = None
        self.archive_path_edit.setText(str(zip_path))
        self.repair_summary_label.setVisible(False)
        self.open_sim_btn.setText("Open in Simulator")

        if not zip_path.exists() or not zipfile.is_zipfile(zip_path):
            self._handle_missing_archive("Invalid zip archive")
            return

        verifier = BootAnimationVerifier()
        report = verifier.verify(zip_path)

        size_bytes = zip_path.stat().st_size
        self.archive_size_label.setText(f"File Size: {bytes_to_mb(size_bytes):.2f} MB")
        self.open_sim_btn.setEnabled(True)
        self.repair_btn.setEnabled(True)

        self._read_and_display_archive(zip_path, report)

    def _handle_missing_archive(self, message: str) -> None:
        self.dimensions_label.setText("Dimensions: -")
        self.fps_label.setText("Framerate: -")
        self.archive_size_label.setText("File Size: -")
        self.looping_vram_label.setText("Looping VRAM: -")
        self.compliance_label.setText(message)
        self.compliance_label.setStyleSheet("color: #e74c3c; font-weight: 600;")
        self.parts_table.setRowCount(0)
        self.desc_preview.setPlainText(message)
        self.repair_btn.setEnabled(False)
        self.open_sim_btn.setEnabled(False)

    def _read_and_display_archive(self, zip_path: Path, report: VerificationReport) -> None:
        try:
            inspect_info = inspect_archive(zip_path)

            with zipfile.ZipFile(zip_path, "r") as zf:
                desc_bytes = zf.read("desc.txt")
                desc_text = desc_bytes.decode("utf-8", errors="replace").lstrip("\ufeff")
                self._update_compliance_status(report, desc_text)
                self._display_inspected_parts(zf, inspect_info)
        except (zipfile.BadZipFile, OSError, ValueError, KeyError) as exc:
            self._handle_missing_archive(f"Archive read error: {exc}")

    def _update_compliance_status(self, report: VerificationReport, desc_text: str) -> None:
        if report.is_valid:
            if report.warnings:
                self.compliance_label.setText(f"Valid ({len(report.warnings)} warnings)")
                self.compliance_label.setStyleSheet("color: #f1c40f; font-weight: 600;")
            else:
                self.compliance_label.setText("Valid (0 errors)")
                self.compliance_label.setStyleSheet("color: #2ecc71; font-weight: 600;")

            self.desc_preview.setPlainText(desc_text)
            return

        err_count = len(report.errors) if report.errors else 1
        self.compliance_label.setText(f"Invalid ({err_count} errors)")
        self.compliance_label.setStyleSheet("color: #e74c3c; font-weight: 600;")
        err_header = "--- VERIFICATION ERRORS ---\n" + "\n".join(report.errors)
        self.desc_preview.setPlainText(f"{err_header}\n\n--- desc.txt ---\n{desc_text}")

    def _display_inspected_parts(self, zf: zipfile.ZipFile, info: ArchiveInspectInfo) -> None:
        self.dimensions_label.setText(f"Dimensions: {info.canvas.width} x {info.canvas.height}")
        self.fps_label.setText(f"Framerate: {info.fps} fps")
        self.parts_table.setRowCount(len(info.parts))
        names = zf.namelist()

        for row_idx, part in enumerate(info.parts):
            trim_file = f"{part.folder_name}/trim.txt"
            trim_info = "None"
            if trim_file in names:
                trim_content = zf.read(trim_file).decode("utf-8", errors="ignore").strip()
                first_trim = trim_content.splitlines()[0] if trim_content else ""
                trim_info = first_trim or "Present"

            self._set_table_row(
                row_idx,
                part.folder_name,
                part.type_code,
                str(part.count),
                str(part.pause),
                str(part.frame_count),
                f"{info.canvas.width}x{info.canvas.height}",
                trim_info,
            )

        self.looping_vram_label.setText(f"Looping VRAM: {info.total_looping_vram_mb:.1f} MB")

    def _set_table_row(
        self,
        row: int,
        folder: str,
        type_code: str,
        count: str,
        pause: str,
        frames: str,
        resolution: str,
        trim: str,
    ) -> None:
        vals = [folder, type_code, count, pause, frames, resolution, trim]
        for col, val in enumerate(vals):
            item = QTableWidgetItem(val)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.parts_table.setItem(row, col, item)

    def _on_repair_clicked(self) -> None:
        if self._archive_path is None:
            return

        default_out = self._archive_path.parent / f"{self._archive_path.stem}_repaired.zip"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Repaired Bootanimation",
            str(default_out),
            "Zip Archives (*.zip);;All Files (*)",
        )
        if not file_path:
            return

        self.start_repair(output_zip=Path(file_path))

    def start_repair(
        self,
        output_zip: Path | None = None,
        repairer: AnimationRepairer | None = None,
    ) -> RepairWorker | None:
        if self._archive_path is None:
            return None

        options = RepairBudgetOptions(
            max_vram_mb=float(self.vram_slider.value()),
            decimate_loop=self.decimate_checkbox.isChecked(),
            convert_hold_last=self.hold_last_checkbox.isChecked(),
        )

        worker = RepairWorker(
            input_zip=self._archive_path,
            output_zip=output_zip,
            options=options,
            repairer=repairer,
            parent=self,
        )

        self._active_worker = worker
        self._set_repair_ui_active(True)

        worker.signals.progress.connect(self._on_repair_progress)
        worker.signals.finished.connect(self._on_repair_finished)
        worker.signals.error.connect(self._on_repair_error)
        worker.signals.cancelled.connect(self._on_repair_cancelled)

        worker.start()
        return worker

    def cancel_repair(self) -> None:
        if self._active_worker is not None:
            self._active_worker.cancel()

    def _set_repair_ui_active(self, active: bool) -> None:
        self.repair_btn.setEnabled(not active)
        self.cancel_repair_btn.setEnabled(active)

    def _on_repair_progress(self, progress: float) -> None:
        self.repair_progress.setValue(int(round(progress * 100)))

    def _on_repair_finished(self, report: RepairReport) -> None:
        self._set_repair_ui_active(False)
        self.repair_progress.setValue(100)
        self._active_worker = None

        if not report.is_success:
            err = (
                "; ".join(report.verification.errors)
                if report.verification.errors
                else "Failed to meet budget constraints"
            )
            self.repair_summary_label.setText(f"Repair incomplete: {err}")
            self.repair_summary_label.setStyleSheet("color: #e74c3c; font-size: 12px;")
            self.repair_summary_label.setVisible(True)
            return

        fixes = (
            "\n".join(f"- {f}" for f in report.fixes_applied) if report.fixes_applied else "None"
        )
        target_name = report.output_path.name
        summary = (
            f"Repair completed ({target_name})\n"
            f"VRAM: {report.original_vram_mb:.1f} MB -> {report.repaired_vram_mb:.1f} MB\n"
            f"Size: {bytes_to_mb(report.original_size_bytes):.2f} MB -> "
            f"{bytes_to_mb(report.repaired_size_bytes):.2f} MB\n"
            f"Fixes applied:\n{fixes}"
        )
        self.repair_summary_label.setText(summary)
        self.repair_summary_label.setStyleSheet("color: #2ecc71; font-size: 12px;")
        self.repair_summary_label.setVisible(True)

        if report.output_path.exists():
            self._repaired_path = report.output_path
            self.open_sim_btn.setText("Simulate Repaired Animation")
            self.open_sim_btn.setEnabled(True)

    def _on_repair_error(self, error_message: str) -> None:
        self._set_repair_ui_active(False)
        self._active_worker = None
        self.repair_summary_label.setText(f"Repair failed: {error_message}")
        self.repair_summary_label.setStyleSheet("color: #e74c3c; font-size: 12px;")
        self.repair_summary_label.setVisible(True)

    def _on_repair_cancelled(self) -> None:
        self._set_repair_ui_active(False)
        self._active_worker = None
        self.repair_summary_label.setText("Repair cancelled")
        self.repair_summary_label.setStyleSheet("color: #f1c40f; font-size: 12px;")
        self.repair_summary_label.setVisible(True)
