from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mkboota.models import CanvasResolution, FrameTrim


def align_mod16(dim: int) -> int:
    """Align dimension to a multiple of 16 for GPU scanline buffer stride requirements"""
    if dim <= 0:
        raise ValueError(f"Dimension must be positive: {dim}")
    return ((dim + 15) // 16) * 16


def calculate_frame_vram_bytes(w: int, h: int) -> int:
    """Calculate uncompressed SurfaceFlinger RGBA_8888 texture memory (4 bytes per pixel)"""
    if w <= 0 or h <= 0:
        raise ValueError(f"Frame dimensions must be positive: {w}x{h}")
    return w * h * 4


def calculate_looping_vram_bytes(frames: int, w: int, h: int) -> int:
    """Calculate texture cache memory for looping parts pre-loaded into GPU RAM by AOSP"""
    if frames < 0:
        raise ValueError(f"Frame count must be non-negative: {frames}")

    if frames == 0:
        return 0

    return frames * calculate_frame_vram_bytes(w, h)


def bytes_to_mb(bytes_count: int) -> float:
    if bytes_count < 0:
        raise ValueError(f"Byte count must be non-negative: {bytes_count}")

    return bytes_count / (1024 * 1024)


def _resolve_trim_bytes(entry: FrameTrim | str | Any) -> int:
    if isinstance(entry, str):
        from mkboota.models import FrameTrim

        trim = FrameTrim.from_trim_entry(entry)
    else:
        trim = entry
    return trim.width * trim.height * 4


def calculate_partition_vram_bytes(
    frame_count: int,
    canvas: CanvasResolution | Any,
    trim_entries: Sequence[FrameTrim | str] | None = None,
) -> int:
    if frame_count < 0:
        raise ValueError(f"Frame count must be non-negative: {frame_count}")

    if trim_entries is not None:
        return sum(_resolve_trim_bytes(entry) for entry in trim_entries)

    return frame_count * canvas.width * canvas.height * 4


__all__ = [
    "align_mod16",
    "bytes_to_mb",
    "calculate_frame_vram_bytes",
    "calculate_looping_vram_bytes",
    "calculate_partition_vram_bytes",
]
