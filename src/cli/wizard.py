from __future__ import annotations

import argparse
import shlex

from cli.parsers import RawResolution, parse_resolution_arg
from cli.terminal import print_info, print_structured_error
from mkboota.exceptions import CropCancelledError
from mkboota.models import (
    CanvasResolution,
    bytes_to_mb,
    calculate_partition_vram_bytes,
)
from mkboota.segments import parse_parts_arg

STANDARD_RESOLUTIONS: tuple[tuple[str, int, int], ...] = (
    ("960x2142 (Default 21:9)", 960, 2142),
    ("1088x1920 (9:16 Phone)", 1088, 1920),
    ("1088x2400 (20:9 Phone)", 1088, 2400),
    ("720x1280 (HD 9:16)", 720, 1280),
    ("1824x2176 (Foldable 5:6)", 1824, 2176),
    ("2560x1600 (Tablet 16:10)", 2560, 1600),
    ("464x464 (Watch 1:1)", 464, 464),
    ("1920x1080 (Landscape TV / Auto)", 1920, 1080),
)


def parse_wizard_resolution_input(raw: str) -> CanvasResolution | RawResolution | None:
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(STANDARD_RESOLUTIONS):
            _, w, h = STANDARD_RESOLUTIONS[idx - 1]
            return CanvasResolution(width=w, height=h)

        print_structured_error(
            f"Preset index out of range: {raw}",
            why="Must be between 1 and 8",
            fix="Enter a number 1-8 or custom WIDTHxHEIGHT (e.g. 1088x1920)",
        )
        return None

    try:
        return parse_resolution_arg(raw)
    except argparse.ArgumentTypeError as exc:
        print_structured_error(str(exc))
        return None


def prompt_wizard_resolution(
    current_resolution: CanvasResolution | RawResolution,
) -> CanvasResolution | RawResolution:
    default_str = f"{current_resolution.width}x{current_resolution.height}"
    while True:
        raw = input(f"Resolution preset [1-8] or WIDTHxHEIGHT [{default_str}]: ").strip()
        if not raw:
            return current_resolution

        resolved = parse_wizard_resolution_input(raw)
        if resolved is not None:
            return resolved


def parse_wizard_fps(raw: str) -> int | None:
    try:
        val = int(raw)
    except ValueError:
        print_structured_error(f"Invalid integer framerate: '{raw}'")
        return None

    if val <= 0:
        print_structured_error(f"Framerate must be positive: {val}")
        return None

    return val


def prompt_wizard_fps(current_fps: int | None) -> int | None:
    default_str = str(current_fps) if current_fps is not None else "30"
    while True:
        raw = input(f"Target framerate FPS [{default_str}]: ").strip()
        if not raw:
            return current_fps

        fps = parse_wizard_fps(raw)
        if fps is not None:
            return fps


def prompt_wizard_parts() -> list[str] | None:
    while True:
        raw = input(
            "Loop mode [1=loop, 2=intro+loop, 3=single] or segments (Enter for default): "
        ).strip()
        if not raw or raw == "1":
            return None

        if raw == "2":
            return ["0-2s:c:1:0", "2-6s:p:0:0"]

        if raw == "3":
            return ["0-5s:p:1:0"]

        tokens = shlex.split(raw)
        try:
            parse_parts_arg(tokens)
            return tokens
        except ValueError as exc:
            print_structured_error(str(exc), fix="Format: <start>-<end>[s]:<type>:<count>:<pause>")


def estimate_looping_frames(parts: list[str] | None, fps: int) -> int:
    if not parts:
        return 5 * fps

    try:
        specs = parse_parts_arg(parts)
        durations = [
            (spec.end_time - spec.start_time)
            for spec in specs
            if spec.count != 1 and spec.end_time is not None
        ]
        looping_duration = sum(durations)
        return max(1, round(looping_duration * fps)) if looping_duration > 0 else 5 * fps
    except (ValueError, TypeError):
        return 5 * fps


def display_wizard_vram_and_confirm(
    resolution: CanvasResolution | RawResolution,
    fps: int | None,
    parts: list[str] | None,
) -> None:
    canvas = resolution if isinstance(resolution, CanvasResolution) else resolution.to_canvas()
    effective_fps = fps or 30
    looping_frames = estimate_looping_frames(parts, effective_fps)
    vram_bytes = calculate_partition_vram_bytes(looping_frames, canvas)
    vram_mb = bytes_to_mb(vram_bytes)
    print_info(
        f"Projected looping VRAM: ~{vram_mb:.1f} MB ({looping_frames} frames @ {canvas.width}x{canvas.height})"
    )

    try:
        confirm = input("Confirm compilation? [Y/n]: ").strip().lower()
        if confirm in ("n", "no"):
            raise CropCancelledError("Compilation cancelled")
    except (EOFError, StopIteration):
        pass


def prompt_wizard_inputs(
    current_resolution: CanvasResolution | RawResolution,
    current_fps: int | None,
) -> tuple[CanvasResolution | RawResolution, int | None, list[str] | None]:
    chosen_resolution = prompt_wizard_resolution(current_resolution)
    chosen_fps = prompt_wizard_fps(current_fps)
    chosen_parts = prompt_wizard_parts()
    display_wizard_vram_and_confirm(chosen_resolution, chosen_fps, chosen_parts)
    return chosen_resolution, chosen_fps, chosen_parts


__all__ = [
    "STANDARD_RESOLUTIONS",
    "display_wizard_vram_and_confirm",
    "estimate_looping_frames",
    "parse_wizard_fps",
    "parse_wizard_resolution_input",
    "prompt_wizard_fps",
    "prompt_wizard_inputs",
    "prompt_wizard_parts",
    "prompt_wizard_resolution",
]
