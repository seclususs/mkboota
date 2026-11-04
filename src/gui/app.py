from __future__ import annotations

import os
from pathlib import Path
import sys

from collections.abc import Sequence

from PySide6.QtWidgets import QApplication

from gui.multimedia import silence_multimedia_logging
from gui.window import StudioMainWindow
from gui.theme import get_minimalist_theme


def has_graphical_display() -> bool:
    if os.environ.get("QT_QPA_PLATFORM") in ("offscreen", "minimal"):
        return False

    if sys.platform in ("win32", "darwin"):
        return True

    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def run_studio(
    argv: Sequence[str] | None = None,
    initial_file: Path | str | None = None,
) -> int:
    if not has_graphical_display():
        sys.stderr.write("Display environment not available\n")
        return 1

    silence_multimedia_logging()
    app = QApplication.instance()

    if app is None:
        q_args = sys.argv if argv is None else [sys.argv[0], *list(argv)]
        app = QApplication(q_args)

    if isinstance(app, QApplication):
        app.setStyleSheet(get_minimalist_theme())

    if initial_file is None and argv:
        for arg in argv:
            if not arg.startswith("-"):
                initial_file = Path(arg)
                break

    window = StudioMainWindow(initial_file=initial_file)
    window.show()

    return int(app.exec())


def main() -> int:
    return run_studio(sys.argv[1:])


__all__ = [
    "has_graphical_display",
    "main",
    "run_studio",
    "silence_multimedia_logging",
]
