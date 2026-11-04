from __future__ import annotations

from cli.terminal import print_banner, print_info, print_structured_error
from mkboota.exceptions import (
    DescFormatError,
    InvalidCanvasError,
    InvalidCropError,
    OutputRequiredError,
    VideoNotFoundError,
    VideoProbeError,
)


def banner(quiet: bool) -> None:
    if not quiet:
        print_banner()


def info(message: str, quiet: bool) -> None:
    if not quiet:
        print_info(message)


def cancel_exit(message: str, quiet: bool, code: int = 130) -> int:
    info(message, quiet=quiet)
    return code


def handle_error(exc: Exception) -> int:
    if isinstance(exc, OutputRequiredError):
        print_structured_error(
            str(exc),
            why="Output path required in non-interactive mode",
            fix="Specify -o / --output <path>",
        )
        return 1

    if isinstance(exc, InvalidCanvasError):
        print_structured_error(
            str(exc),
            why="Canvas width must be divisible by 16",
            fix="Use width divisible by 16 (e.g. 1088x1920 or 960x2142)",
        )
        return 1

    if isinstance(exc, VideoNotFoundError):
        print_structured_error(
            str(exc),
            why="Video source file not found",
            fix="Verify file path and access permissions",
        )
        return 1

    if isinstance(exc, DescFormatError):
        print_structured_error(
            str(exc),
            why="Invalid desc.txt syntax",
            fix="Check desc.txt header format (WIDTH HEIGHT FPS) and parts",
        )
        return 1

    if isinstance(exc, InvalidCropError):
        print_structured_error(
            str(exc),
            why="Invalid crop coordinates",
            fix="Ensure crop coordinates lie within frame boundaries",
        )
        return 1

    if isinstance(exc, VideoProbeError):
        print_structured_error(
            str(exc),
            why="FFprobe failed to read video metadata",
            fix="Ensure file is a valid supported video format",
        )
        return 1

    if isinstance(exc, (FileNotFoundError, ValueError)):
        print_structured_error(str(exc))
        return 1

    print_structured_error(str(exc))
    return 2


__all__ = ["banner", "cancel_exit", "handle_error", "info"]
