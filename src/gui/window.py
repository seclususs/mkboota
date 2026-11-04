from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QWidget,
)

from gui.tabs.inspector import InspectorTab
from gui.tabs.simulator import SimulatorTab
from gui.tabs.studio import StudioTab


class StudioMainWindow(QMainWindow):
    def __init__(
        self,
        parent: QWidget | None = None,
        initial_file: Path | str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("mkboota Studio")
        self.setMinimumSize(800, 500)
        self._about_dialog: QMessageBox | None = None

        self._setup_ui()
        self._create_menu_bar()
        self._initial_geometry()
        self.set_status("Ready")

        if initial_file is not None:
            self.open_file(initial_file)

    def _setup_ui(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        self.studio_tab = StudioTab()
        self.inspector_tab = InspectorTab()
        self.simulator_tab = SimulatorTab()

        self.tabs.addTab(self.studio_tab, "Studio")
        self.tabs.addTab(self.inspector_tab, "Inspector")
        self.tabs.addTab(self.simulator_tab, "Simulator")

        self.inspector_tab.simulate_requested.connect(self.simulate_archive)
        self.setCentralWidget(self.tabs)

    def _create_menu_bar(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("File")
        open_video_act = QAction("Open Video", self)
        open_video_act.setShortcut(QKeySequence("Ctrl+O"))
        open_video_act.triggered.connect(self.open_video)
        file_menu.addAction(open_video_act)

        open_archive_act = QAction("Open Archive", self)
        open_archive_act.setShortcut(QKeySequence("Ctrl+Shift+O"))
        open_archive_act.triggered.connect(self.open_archive)
        file_menu.addAction(open_archive_act)

        file_menu.addSeparator()

        exit_act = QAction("Exit", self)
        exit_act.setShortcut(QKeySequence("Ctrl+Q"))
        exit_act.triggered.connect(self.close)
        file_menu.addAction(exit_act)

        help_menu = bar.addMenu("Help")
        about_act = QAction("About mkboota", self)
        about_act.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_act)

    _setup_menus = _create_menu_bar

    def _initial_geometry(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None and not screen.availableGeometry().isEmpty():
            avail = screen.availableGeometry()

            target_w = min(1100, int(avail.width() * 0.80))
            target_w = max(self.minimumWidth(), min(target_w, avail.width()))

            target_h = min(750, int(avail.height() * 0.80))
            target_h = max(self.minimumHeight(), min(target_h, avail.height()))

            self.resize(target_w, target_h)

            frame_geo = self.frameGeometry()
            frame_geo.moveCenter(avail.center())
            self.move(frame_geo.topLeft())
        else:
            self.resize(1000, 700)

    def set_status(self, message: str) -> None:
        self.statusBar().showMessage(message)

    def open_file(self, file_path: Path | str) -> None:
        path = Path(file_path)
        if not path.exists():
            self.set_status(f"File not found: {path}")
            return

        if path.suffix.lower() == ".zip":
            self.tabs.setCurrentIndex(1)
            self.inspector_tab.load_archive(path)
            self.set_status(f"Loaded archive: {path.name}")
        else:
            self.tabs.setCurrentIndex(0)
            self.studio_tab.load_video(path)
            self.set_status(f"Loaded video: {path.name}")

    def open_video(self) -> None:
        self.tabs.setCurrentIndex(0)
        self.studio_tab.browse_video()

    def open_archive(self) -> None:
        self.tabs.setCurrentIndex(1)
        self.inspector_tab.browse_archive()

    def simulate_archive(self, archive_path: Path) -> None:
        self.tabs.setCurrentIndex(2)
        self.simulator_tab.load_archive(archive_path)

    def _show_about_dialog(self) -> QMessageBox:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("About mkboota")
        dialog.setTextFormat(Qt.TextFormat.RichText)

        guide_text = (
            "<h3>mkboota Studio</h3>"
            "<p>AOSP boot animation compiler, inspector, and simulator</p>"
            "<h4>Overview</h4>"
            "<p>Import video, configure canvas and framerate, define segments, "
            "inspect VRAM, and compile bootanimation.zip</p>"
            "<h4>AOSP Constraints</h4>"
            "<p><b>Resolution:</b> Canvas width must be divisible by 16 for GPU stride</p>"
            "<p><b>Memory Budget:</b> 150 MB looping VRAM safety ceiling</p>"
            "<p><b>Playback Modes:</b> Type 'p' (repeating / single) and 'c' (complete)</p>"
            "<p><b>Pause Frames:</b> Frame count held before looping or advancing</p>"
            '<p><a href="https://github.com/seclususs/mkboota">mkboota GitHub Repository</a></p>'
        )
        dialog.setText(guide_text)
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok)

        self._about_dialog = dialog
        dialog.show()

        return dialog

    show_about = _show_about_dialog


MainWindow = StudioMainWindow

__all__ = ["MainWindow", "StudioMainWindow"]
