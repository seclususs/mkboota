_MINIMALIST_THEME = """
QWidget {
    background-color: #1e1e24;
    color: #e0e0e0;
    font-family: "Segoe UI", -apple-system, sans-serif;
    font-size: 10pt;
    selection-background-color: #4a90e2;
    selection-color: #ffffff;
}

QMainWindow, QDialog {
    background-color: #1e1e24;
}

QGroupBox {
    background-color: #282830;
    border: 1px solid #383842;
    border-radius: 4px;
    margin-top: 6px;
    padding: 8px;
}

QLabel {
    background-color: transparent;
    border: none;
    padding: 0px;
    margin: 0px;
    color: #e0e0e0;
}

QScrollArea {
    border: none;
    background-color: transparent;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 4px;
    color: #8a8a96;
}

QPushButton {
    background-color: #282830;
    color: #e0e0e0;
    border: 1px solid #383842;
    border-radius: 4px;
    padding: 6px 14px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #383842;
    border-color: #4a90e2;
}

QPushButton:pressed {
    background-color: #4a90e2;
    color: #ffffff;
}

QPushButton:disabled {
    background-color: #1e1e24;
    color: #8a8a96;
    border-color: #282830;
}

QPushButton[primary="true"] {
    background-color: #4a90e2;
    color: #ffffff;
    border: 1px solid #4a90e2;
}

QPushButton[primary="true"]:hover {
    background-color: #357abd;
}

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #1e1e24;
    color: #e0e0e0;
    border: 1px solid #383842;
    border-radius: 4px;
    padding: 6px;
}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {
    border-color: #4a90e2;
}

QProgressBar {
    background-color: #282830;
    border: 1px solid #383842;
    border-radius: 4px;
    text-align: center;
    color: #e0e0e0;
}

QProgressBar::chunk {
    background-color: #4a90e2;
    border-radius: 3px;
}

QLabel {
    color: #e0e0e0;
}

QLabel[muted="true"] {
    color: #8a8a96;
}

QTabBar::tab {
    background-color: #1e1e24;
    color: #8a8a96;
    border: 1px solid #383842;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    padding: 8px 16px;
    margin-right: 2px;
}

QTabBar::tab:selected {
    background-color: #282830;
    color: #e0e0e0;
    border-color: #383842;
}

QTabWidget::pane {
    border: 1px solid #383842;
    background-color: #282830;
    border-radius: 4px;
}

QScrollBar:vertical {
    background-color: #1e1e24;
    width: 10px;
    margin: 0px;
}

QScrollBar::handle:vertical {
    background-color: #383842;
    min-height: 20px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background-color: #4a90e2;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QMenuBar {
    background-color: #1e1e24;
    color: #e0e0e0;
    spacing: 4px;
    border-bottom: 1px solid #383842;
}

QMenuBar::item {
    background-color: transparent;
    padding: 4px 8px;
    border-radius: 4px;
}

QMenuBar::item:selected {
    background-color: #282830;
    color: #ffffff;
}

QMenuBar::item:pressed {
    background-color: #383842;
}

QMenu {
    background-color: #282830;
    color: #e0e0e0;
    border: 1px solid #383842;
    border-radius: 4px;
    padding: 4px 0px;
    min-width: 180px;
}

QMenu::item {
    padding: 6px 24px;
    background-color: transparent;
}

QMenu::item:selected {
    background-color: #4a90e2;
    color: #ffffff;
}

QMenu::item:disabled {
    color: #8a8a96;
    background-color: transparent;
}

QMenu::separator {
    height: 1px;
    background-color: #383842;
    margin: 4px 8px;
}
"""


def get_minimalist_theme() -> str:
    return _MINIMALIST_THEME
