from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from mkboota.exceptions import VideoNotFoundError
from mkboota.models import (
    CanvasResolution,
    CompilationEstimate,
    PartEstimate,
    SegmentSpec,
    VideoMetadata,
    bytes_to_mb,
    calculate_looping_vram_bytes,
)
from mkboota.protocols import VideoInspectorProtocol
from mkboota.segments import resolve_canonical_segments
from mkboota.video import FFprobeInspector

DEFAULT_CANVAS = CanvasResolution()


class CompilationEstimator:
    def __init__(self, video_inspector: VideoInspectorProtocol | None = None) -> None:
        self.video_inspector = video_inspector or FFprobeInspector()

    def estimate(
        self,
        video_path: Path,
        segments: Sequence[SegmentSpec] | None = None,
        canvas: CanvasResolution | None = None,
        fps: int | None = None,
        loop: bool = False,
        loop_start: float | None = None,
        loop_mode: str = "infinite",
    ) -> CompilationEstimate:
        if not video_path.exists():
            raise VideoNotFoundError(f"Video file not found: {video_path}")

        meta = self.video_inspector.probe(video_path)
        target_canvas = canvas if canvas is not None else DEFAULT_CANVAS
        effective_fps = self.determine_fps(meta, fps)

        canonical_segments = resolve_canonical_segments(
            video_duration=meta.duration,
            segments=segments,
            loop=loop,
            loop_start=loop_start,
            loop_mode=loop_mode,
            fps=effective_fps,
        )

        return self.estimate_from_segments(
            segments=canonical_segments,
            duration=meta.duration,
            canvas=target_canvas,
            fps=effective_fps,
        )

    @classmethod
    def estimate_from_segments(
        cls,
        segments: Sequence[SegmentSpec],
        duration: float,
        canvas: CanvasResolution = DEFAULT_CANVAS,
        fps: int = 30,
    ) -> CompilationEstimate:
        effective_fps = max(1, fps)
        parts: list[PartEstimate] = []

        for idx, seg in enumerate(segments):
            seg_end = seg.end_time if seg.end_time is not None else duration
            seg_dur = max(0.0, seg_end - seg.start_time)
            frame_count = max(1, round(seg_dur * effective_fps))

            part_vram_bytes = calculate_looping_vram_bytes(frame_count, canvas.width, canvas.height)
            part_vram_mb = bytes_to_mb(part_vram_bytes)
            parts.append(
                PartEstimate(
                    folder_name=seg.folder_name or f"part{idx}",
                    type_code=seg.type_code,
                    count=seg.count,
                    pause=seg.pause,
                    frame_count=frame_count,
                    vram_mb=round(part_vram_mb, 2),
                    is_looping=seg.is_looping,
                )
            )

        total_frames = sum(p.frame_count for p in parts)
        looping_vram_mb = round(sum(p.vram_mb for p in parts if p.is_looping), 2)
        # Estimated uncompressed PNG-8 indexed palette size plus header overhead
        frame_bytes = (canvas.width * canvas.height) // 4
        estimated_zip_size = total_frames * frame_bytes + 1024

        warnings = cls.collect_warnings(canvas, looping_vram_mb, parts)

        return CompilationEstimate(
            canvas=canvas,
            fps=effective_fps,
            parts=tuple(parts),
            total_frames=total_frames,
            looping_vram_mb=looping_vram_mb,
            estimated_zip_size_bytes=estimated_zip_size,
            warnings=tuple(warnings),
        )

    @staticmethod
    def determine_fps(meta: VideoMetadata, custom_fps: int | None) -> int:
        if custom_fps is not None and custom_fps > 0:
            return custom_fps

        if isinstance(meta.fps, (int, float)) and meta.fps > 0:
            return min(round(meta.fps), 30)

        return 30

    @staticmethod
    def collect_warnings(
        canvas: CanvasResolution,
        looping_vram_mb: float,
        parts: list[PartEstimate],
    ) -> list[str]:
        warnings: list[str] = []

        # GPU drivers require row stride divisible by 16 to avoid pitch misalignment
        if canvas.width % 16 != 0:
            warnings.append(f"Canvas width {canvas.width} is not a multiple of 16")

        # SurfaceFlinger loads all looping frames into VRAM simultaneously; exceeding 150 MB risks watchdog panic
        if looping_vram_mb > 150.0:
            warnings.append(
                f"Looping VRAM ({looping_vram_mb:.1f} MB) exceeds 150.0 MB safe threshold"
            )

        # Texture handle limits during early boot
        if any(p.frame_count > 300 and p.is_looping for p in parts):
            warnings.append("Looping partition frame count exceeds 300 safe ceiling")

        return warnings


__all__ = ["CompilationEstimator", "DEFAULT_CANVAS"]
