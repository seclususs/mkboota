from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from PIL import Image

    from mkboota.models import (
        CanvasResolution,
        CropRect,
        FrameTrim,
        RepairBudgetOptions,
        RepairReport,
        SegmentSpec,
        VerificationReport,
        VideoMetadata,
    )

ProgressCallback = Callable[[str, int, int], None]


@runtime_checkable
class VideoInspectorProtocol(Protocol):
    def probe(self, video_path: Path) -> VideoMetadata: ...


@runtime_checkable
class VideoExtractorProtocol(Protocol):
    def extract_frames(
        self,
        video_path: Path,
        output_dir: Path,
        target_fps: int = 60,
        crop: CropRect | None = None,
        scale: CanvasResolution | tuple[int, int] | None = None,
        max_duration: float | None = None,
        start_time: float = 0.0,
        duration: float | None = None,
    ) -> list[Path]: ...


@runtime_checkable
class FrameTrimmerProtocol(Protocol):
    def calculate_trim(
        self,
        image: Image.Image,
        canvas: CanvasResolution = ...,
        threshold: int = 2,
    ) -> FrameTrim: ...

    def calculate_uniform_trim(
        self,
        images: list[Image.Image],
        canvas: CanvasResolution = ...,
        threshold: int = 2,
    ) -> tuple[CropRect | None, FrameTrim]: ...


@runtime_checkable
class FrameQuantizerProtocol(Protocol):
    def quantize_png8(
        self,
        image: Image.Image,
        output_path: Path,
        crop: CropRect | None = None,
        trim: FrameTrim | None = None,
    ) -> None: ...


@runtime_checkable
class ZipPackerProtocol(Protocol):
    def pack(self, build_dir: Path, output_zip: Path) -> Path: ...


@runtime_checkable
class VerifierProtocol(Protocol):
    def verify(self, zip_path: Path, max_vram_mb: float = 150.0) -> VerificationReport: ...


@runtime_checkable
class RepairerProtocol(Protocol):
    def repair(
        self,
        input_zip: Path,
        output_zip: Path | None = None,
        progress_callback: ProgressCallback | None = None,
        options: RepairBudgetOptions | None = None,
    ) -> RepairReport: ...


@runtime_checkable
class CropperUIProtocol(Protocol):
    def select_crop(
        self,
        preview_image: Image.Image,
        canvas: CanvasResolution = ...,
        video_duration: float = 0.0,
        fps: int = 30,
    ) -> CropRect: ...


@runtime_checkable
class CompilerProtocol(Protocol):
    def compile(
        self,
        video_path: Path,
        output_zip: Path,
        canvas: CanvasResolution = ...,
        fps: int | None = None,
        loop: bool = False,
        loop_start: float | None = None,
        loop_mode: str = "auto",
        parts: Sequence[SegmentSpec] | None = None,
        max_duration: float | None = None,
        bg_color: str = "#000000",
        progress_callback: Any | None = None,
        cropper_ui: CropperUIProtocol | None = None,
    ) -> Path: ...


__all__ = [
    "CompilerProtocol",
    "CropperUIProtocol",
    "FrameQuantizerProtocol",
    "FrameTrimmerProtocol",
    "ProgressCallback",
    "RepairerProtocol",
    "VerifierProtocol",
    "VideoExtractorProtocol",
    "VideoInspectorProtocol",
    "ZipPackerProtocol",
]
