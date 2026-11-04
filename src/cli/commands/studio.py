from __future__ import annotations

import importlib

from cli.parsers import build_studio_parser, has_graphical_display
from cli.terminal import print_structured_error


def run_studio(argv: list[str]) -> int:
    parser = build_studio_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 0
        return 0 if code == 0 else 1

    if not has_graphical_display():
        print_structured_error(
            "Display not available",
            why="Studio requires desktop session",
            fix="Use desktop session or CLI commands (compile, repair, inspect, verify)",
        )
        return 1

    try:
        gui_app = importlib.import_module("gui.app")
        studio_runner = gui_app.run_studio
        result = studio_runner(argv, initial_file=args.input_file)
        return int(result) if isinstance(result, int) else 0
    except (ImportError, AttributeError) as exc:
        print_structured_error(
            "Studio GUI module unavailable",
            why=f"Studio launch failed: {exc}",
            fix="Ensure PySide6 is installed",
        )
        return 1


__all__ = ["run_studio"]
