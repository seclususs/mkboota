from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import sys

from mkboota import __version__
from mkboota.exceptions import InvalidCanvasError, OutputRequiredError
from mkboota.models import (
    CanvasResolution,
    align_mod16,
    is_valid_hex_color,
    normalize_hex_color,
)


def has_graphical_display() -> bool:
    if os.environ.get("QT_QPA_PLATFORM") in ("offscreen", "minimal"):
        return False

    if sys.platform in ("win32", "darwin"):
        return True

    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def resolve_output_path(
    output_arg: Path | None,
    default_filename: str,
    title: str = "Save Bootanimation",
    filter_pattern: str = "Zip Archives (*.zip);;All Files (*)",
    allow_gui: bool = True,
    quiet: bool = False,
) -> Path | None:
    if output_arg is not None:
        return output_arg

    if not allow_gui or not has_graphical_display():
        raise OutputRequiredError(
            "Output path required in non-interactive mode: -o / --output <path>"
        )

    from PySide6.QtWidgets import QFileDialog

    selected, _ = QFileDialog.getSaveFileName(None, title, default_filename, filter_pattern)
    if not selected:
        if not quiet:
            print("Operation cancelled")
        return None

    return Path(selected)


@dataclass(frozen=True, slots=True)
class RawResolution:
    width: int
    height: int

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (CanvasResolution, RawResolution)):
            return self.width == other.width and self.height == other.height
        return False

    def to_canvas(self, strict: bool = False, quiet: bool = False) -> CanvasResolution:
        if self.width % 16 != 0:
            if strict:
                raise InvalidCanvasError(f"Canvas width {self.width} must be divisible by 16")

            new_w = align_mod16(self.width)
            if not quiet:
                from cli.terminal import print_info

                print_info(f"Canvas width aligned from {self.width} to {new_w}")
            return CanvasResolution(width=new_w, height=self.height)

        return CanvasResolution(width=self.width, height=self.height)


def parse_resolution_arg(res_str: str) -> CanvasResolution | RawResolution:
    parts = res_str.lower().split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"Invalid resolution '{res_str}'; expected WIDTHxHEIGHT (e.g. 960x2142)"
        )

    try:
        width = int(parts[0])
        height = int(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid resolution '{res_str}'; width and height must be integers"
        ) from exc

    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError(f"Canvas dimensions must be positive: {width}x{height}")

    if width % 16 == 0:
        return CanvasResolution(width=width, height=height)

    return RawResolution(width=width, height=height)


def parse_resolution(res_str: str) -> CanvasResolution:
    raw = parse_resolution_arg(res_str)

    if isinstance(raw, CanvasResolution):
        return raw

    raise argparse.ArgumentTypeError(f"Canvas width {raw.width} must be divisible by 16")


def validate_hex_color_arg(color_str: str) -> str:
    if not is_valid_hex_color(color_str):
        raise argparse.ArgumentTypeError(
            f"Invalid background color '{color_str}'; expected #RRGGBB (e.g. #000000)"
        )
    return normalize_hex_color(color_str)


def resolve_canvas(
    raw_res: CanvasResolution | RawResolution | None,
    strict: bool,
    quiet: bool,
) -> CanvasResolution | None:
    if raw_res is None:
        return None

    if isinstance(raw_res, CanvasResolution):
        return raw_res

    return raw_res.to_canvas(strict=strict, quiet=quiet)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mkboota",
        description="AOSP bootanimation tool",
        epilog=(
            "Subcommands:\n"
            "  compile Compile video to bootanimation.zip (default)\n"
            "  repair  Repair bootanimation.zip\n"
            "  verify  Verify AOSP compliance\n"
            "  inspect Inspect archive and VRAM metrics\n"
            "  studio  Launch desktop studio GUI\n\n"
            "Run 'mkboota repair --help' for repair options"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "video",
        type=Path,
        help="Input video path",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output zip path",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Quiet mode (print only output path)",
    )

    loop_group = parser.add_mutually_exclusive_group()
    loop_group.add_argument(
        "--loop",
        action="store_true",
        help="Loop full video continuously",
    )
    loop_group.add_argument(
        "--loop-start",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Loop start offset in seconds",
    )
    parser.add_argument(
        "--loop-mode",
        choices=["loop", "hold-last"],
        default=None,
        help="Loop mode (auto, loop, hold-last)",
    )

    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Disable GUI cropper (use center crop)",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=None,
        help="Target FPS (default: auto up to 30)",
    )
    parser.add_argument(
        "-r",
        "--resolution",
        type=parse_resolution_arg,
        default=CanvasResolution(width=960, height=2142),
        help="Resolution WIDTHxHEIGHT (default: 960x2142)",
    )
    parser.add_argument(
        "--max-duration",
        type=float,
        default=None,
        help="Max extraction duration in seconds",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on alignment or validation warnings",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run without writing output",
    )

    parser.add_argument(
        "--parts",
        nargs="+",
        default=None,
        help="Segment specs: <start>-<end>[s]:<type>:<count>:<pause>[:folder][:color][:clock]",
    )
    parser.add_argument(
        "--wizard",
        action="store_true",
        help="Interactive setup wizard",
    )
    parser.add_argument(
        "--bg-color",
        type=validate_hex_color_arg,
        default="#000000",
        help="Background color #RRGGBB (default: #000000)",
    )

    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show version and exit",
    )
    return parser


def build_repair_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mkboota repair",
        description="AOSP bootanimation repair tool",
    )
    parser.add_argument(
        "input_zip",
        type=Path,
        help="Input archive path",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output archive path",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Quiet mode (print only output path)",
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on alignment or validation warnings",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run preview without writing output",
    )

    parser.add_argument(
        "--max-vram",
        type=float,
        default=150.0,
        help="Max looping VRAM budget in MB (default: 150.0)",
    )
    parser.add_argument(
        "--loop-fps",
        type=int,
        default=None,
        help="Loop partition target FPS",
    )
    parser.add_argument(
        "--decimate-loop",
        action="store_true",
        help="Decimate looping frames by 2x",
    )
    parser.add_argument(
        "--convert-hold-last",
        action="store_true",
        help="Convert looping parts to 1-frame hold",
    )
    parser.add_argument(
        "-r",
        "--resolution",
        "--target-resolution",
        dest="target_resolution",
        type=parse_resolution_arg,
        default=None,
        help="Target resolution WIDTHxHEIGHT",
    )
    return parser


def build_verify_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mkboota verify",
        description="AOSP bootanimation verification tool",
    )
    parser.add_argument(
        "zip_path",
        type=Path,
        help="Archive path",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Quiet mode (suppress output on success)",
    )
    return parser


def build_inspect_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mkboota inspect",
        description="AOSP bootanimation inspection tool",
    )
    parser.add_argument(
        "zip_path",
        type=Path,
        help="Archive path",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Quiet mode",
    )
    return parser


def build_studio_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mkboota studio",
        description="Desktop graphical studio",
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        default=None,
        type=Path,
        help="Initial video or bootanimation.zip to load",
    )
    return parser


__all__ = [
    "RawResolution",
    "build_inspect_parser",
    "build_parser",
    "build_repair_parser",
    "build_studio_parser",
    "build_verify_parser",
    "has_graphical_display",
    "parse_resolution",
    "parse_resolution_arg",
    "resolve_canvas",
    "resolve_output_path",
    "validate_hex_color_arg",
]
