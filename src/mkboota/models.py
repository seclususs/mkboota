from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import re

from mkboota.desc import DescBuilder, DescParser
from mkboota.exceptions import DescFormatError, InvalidCanvasError, InvalidCropError
from mkboota.memory import (
    align_mod16,
    bytes_to_mb,
    calculate_frame_vram_bytes,
    calculate_looping_vram_bytes,
    calculate_partition_vram_bytes,
)


def alphanumeric_sort_key(s: str) -> list[int | str]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


@dataclass(frozen=True, slots=True)
class CanvasResolution:
    width: int = 960
    height: int = 2142

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise InvalidCanvasError(
                f"Canvas dimensions must be positive, got {self.width}x{self.height}"
            )
        if self.width % 16 != 0:
            raise InvalidCanvasError(f"Canvas width {self.width} must be divisible by 16")

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height

    @classmethod
    def from_dimensions(cls, width: int, height: int, auto_align: bool = False) -> CanvasResolution:
        if auto_align and width > 0:
            width = align_mod16(width)
        return cls(width=width, height=height)


@dataclass(frozen=True, slots=True)
class CropRect:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.x < 0 or self.y < 0:
            raise InvalidCropError(f"Crop offsets must be non-negative: x={self.x}, y={self.y}")
        if self.width <= 0 or self.height <= 0:
            raise InvalidCropError(
                f"Crop dimensions must be positive, got {self.width}x{self.height}"
            )


_TRIM_ENTRY_PATTERN = re.compile(r"^(\d+)x(\d+)\+(\d+)\+(\d+)$")


@dataclass(frozen=True, slots=True)
class FrameTrim:
    width: int
    height: int
    offset_x: int
    offset_y: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise InvalidCropError(
                f"Trim dimensions must be positive, got {self.width}x{self.height}"
            )
        if self.offset_x < 0 or self.offset_y < 0:
            raise InvalidCropError(
                f"Trim offsets must be non-negative: x={self.offset_x}, y={self.offset_y}"
            )

    @classmethod
    def from_trim_entry(cls, entry: str) -> FrameTrim:
        match = _TRIM_ENTRY_PATTERN.match(entry)
        if match is None:
            raise ValueError(f"Invalid trim entry format: {entry!r}")

        w, h, ox, oy = (int(group) for group in match.groups())
        return cls(width=w, height=h, offset_x=ox, offset_y=oy)

    def to_trim_entry(self) -> str:
        return f"{self.width}x{self.height}+{self.offset_x}+{self.offset_y}"


class LoopMode(StrEnum):
    LOOP = "loop"
    HOLD_LAST = "hold-last"
    SEGMENT = "segment"


_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def is_valid_hex_color(c: str) -> bool:
    if not isinstance(c, str) or not c.startswith("#"):
        return False

    if len(c) not in (4, 7):
        return False

    return all(ch in _HEX_DIGITS for ch in c[1:])


def normalize_hex_color(c: str) -> str:
    if not is_valid_hex_color(c):
        raise ValueError(f"Invalid hex color format: {c!r}")

    if len(c) == 4:
        return f"#{c[1] * 2}{c[2] * 2}{c[3] * 2}"

    return c


@dataclass(frozen=True, slots=True)
class PartConfig:
    type_code: str
    count: int
    pause: int
    folder_name: str
    bg_color: str = "#000000"
    clock_pos: int = -1

    def __post_init__(self) -> None:
        if self.type_code not in ("c", "p"):
            raise DescFormatError(f"Part type_code must be 'c' or 'p', got {self.type_code!r}")

        # In AOSP desc.txt, count=1 denotes play-once; count=0 or count>1 denotes repeating playback
        if self.count < 0:
            raise DescFormatError(f"Part count must be non-negative, got {self.count}")

        if self.pause < 0:
            raise DescFormatError(f"Part pause must be non-negative, got {self.pause}")

        if not is_valid_hex_color(self.bg_color):
            raise DescFormatError(f"bg_color must be hex like '#RRGGBB', got {self.bg_color!r}")
        object.__setattr__(self, "bg_color", normalize_hex_color(self.bg_color))

    def to_desc_line(self) -> str:
        if self.clock_pos >= 0:
            return (
                f"{self.type_code} {self.count} {self.pause} "
                f"{self.folder_name} {self.bg_color} {self.clock_pos}"
            )
        return f"{self.type_code} {self.count} {self.pause} {self.folder_name} {self.bg_color}"


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    duration: float
    width: int
    height: int
    fps: float
    frame_count: int


@dataclass(frozen=True, slots=True)
class VerificationReport:
    is_valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RepairReport:
    input_path: Path
    output_path: Path
    is_success: bool
    fixes_applied: tuple[str, ...]
    original_vram_mb: float
    repaired_vram_mb: float
    original_size_bytes: int
    repaired_size_bytes: int
    verification: VerificationReport


@dataclass(frozen=True, slots=True)
class SegmentSpec:
    start_time: float
    end_time: float | None = None
    type_code: str = "p"
    count: int = 0
    pause: int = 0
    folder_name: str | None = None
    bg_color: str = "#000000"
    clock_pos: int = -1

    def __post_init__(self) -> None:
        if self.start_time < 0.0:
            raise ValueError(f"start_time must be non-negative, got {self.start_time}")

        if self.end_time is not None and self.end_time <= self.start_time:
            raise ValueError(
                f"end_time ({self.end_time}) must exceed start_time ({self.start_time})"
            )

        if self.type_code not in ("c", "p"):
            raise DescFormatError(f"type_code must be 'c' or 'p', got {self.type_code!r}")

        if self.count < 0:
            raise DescFormatError(f"count must be non-negative, got {self.count}")

        if self.pause < 0:
            raise DescFormatError(f"pause must be non-negative, got {self.pause}")

        if not is_valid_hex_color(self.bg_color):
            raise DescFormatError(f"bg_color must be hex like '#RRGGBB', got {self.bg_color!r}")
        object.__setattr__(self, "bg_color", normalize_hex_color(self.bg_color))

    @property
    def is_looping(self) -> bool:
        # In AOSP desc.txt, count=1 denotes play-once; count=0 or count>1 denotes repeating playback
        return self.count != 1


@dataclass(frozen=True, slots=True)
class RepairBudgetOptions:
    max_vram_mb: float = 150.0
    convert_hold_last: bool = False
    decimate_loop: bool = False
    loop_fps: int | None = None
    target_resolution: CanvasResolution | None = None

    def __post_init__(self) -> None:
        if self.max_vram_mb <= 0.0:
            raise ValueError(f"max_vram_mb must be positive, got {self.max_vram_mb}")
        if self.loop_fps is not None and self.loop_fps <= 0:
            raise ValueError(f"loop_fps must be positive, got {self.loop_fps}")


@dataclass(frozen=True, slots=True)
class PartEstimate:
    folder_name: str
    type_code: str
    count: int
    pause: int
    frame_count: int
    vram_mb: float
    is_looping: bool


@dataclass(frozen=True, slots=True)
class CompilationEstimate:
    canvas: CanvasResolution
    fps: int
    parts: tuple[PartEstimate, ...]
    total_frames: int
    looping_vram_mb: float
    estimated_zip_size_bytes: int
    warnings: tuple[str, ...] = ()


class DeviceProfile(StrEnum):
    STANDARD = "standard"
    ANDROID_GO = "android-go"
    FLAGSHIP = "flagship"

    @property
    def max_vram_mb(self) -> float:
        if self == DeviceProfile.ANDROID_GO:
            return 45.0
        if self == DeviceProfile.FLAGSHIP:
            return 300.0
        return 150.0

    @property
    def recommended_max_resolution(self) -> tuple[int, int]:
        if self == DeviceProfile.ANDROID_GO:
            return (720, 1280)
        if self == DeviceProfile.FLAGSHIP:
            return (1440, 3200)
        return (1088, 2400)


__all__ = [
    "CanvasResolution",
    "CompilationEstimate",
    "CropRect",
    "DescBuilder",
    "DescParser",
    "DeviceProfile",
    "FrameTrim",
    "LoopMode",
    "PartConfig",
    "PartEstimate",
    "RepairBudgetOptions",
    "RepairReport",
    "SegmentSpec",
    "VerificationReport",
    "VideoMetadata",
    "align_mod16",
    "alphanumeric_sort_key",
    "bytes_to_mb",
    "calculate_frame_vram_bytes",
    "calculate_looping_vram_bytes",
    "calculate_partition_vram_bytes",
    "is_valid_hex_color",
    "normalize_hex_color",
]
