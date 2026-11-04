from collections.abc import Callable, Sequence
import logging
import math
from pathlib import Path
import tempfile

from PIL import Image, UnidentifiedImageError

from mkboota.advisor import DurationAdvice, DurationAdvisor, DurationCategory
from mkboota.archive import ZipPackerProtocol, ZipStorePacker
from mkboota.editor import CenterCropper, CropperUIProtocol, EditorResult
from mkboota.estimator import CompilationEstimator
from mkboota.exceptions import (
    ArchiveError,
    CompilationError,
    VideoExtractionError,
    VideoNotFoundError,
)
from mkboota.imaging import (
    BoundingBoxTrimmer,
    FrameQuantizerProtocol,
    FrameTrimmerProtocol,
    PaletteQuantizer,
)
from mkboota.models import (
    CanvasResolution,
    CompilationEstimate,
    CropRect,
    DescBuilder,
    FrameTrim,
    LoopMode,
    PartConfig,
    SegmentSpec,
    VideoMetadata,
    bytes_to_mb,
    calculate_partition_vram_bytes,
    is_valid_hex_color,
    normalize_hex_color,
)
from mkboota.segments import (
    resolve_canonical_segments,
    resolve_explicit_segments,
    resolve_loop_flags,
)
from mkboota.verifier import BootAnimationVerifier, VerifierProtocol
from mkboota.video import (
    FFmpegExtractor,
    FFprobeInspector,
    VideoExtractorProtocol,
    VideoInspectorProtocol,
    calculate_fit_scale,
)

logger = logging.getLogger(__name__)

DEFAULT_CANVAS = CanvasResolution()

ProgressCallback = Callable[[str, int, int], None]


def _validate_bg_color(color: str) -> str:
    if not is_valid_hex_color(color) or len(color) != 7:
        raise ValueError(f"bg_color must be hex like '#RRGGBB', got {color!r}")
    return normalize_hex_color(color)


def _emit_progress(
    callback: Callable[..., None] | None,
    phase: str,
    current: int,
    total: int,
) -> None:
    if callback is None:
        return
    fraction = current / max(1, total)
    try:
        callback(phase, fraction)
    except TypeError:
        callback(phase, current, total)


_resolve_canonical_segments = resolve_canonical_segments
_resolve_explicit_segments = resolve_explicit_segments
_resolve_loop_flags = resolve_loop_flags


class AnimationCompiler:
    """Compiles video files into AOSP bootanimation.zip archives"""

    def __init__(
        self,
        video_inspector: VideoInspectorProtocol | None = None,
        video_extractor: VideoExtractorProtocol | None = None,
        interactive_cropper: CropperUIProtocol | None = None,
        headless_cropper: CropperUIProtocol | None = None,
        frame_trimmer: FrameTrimmerProtocol | None = None,
        frame_quantizer: FrameQuantizerProtocol | None = None,
        zip_packer: ZipPackerProtocol | None = None,
        verifier: VerifierProtocol | None = None,
        duration_advisor: DurationAdvisor | None = None,
    ) -> None:
        self.video_inspector = video_inspector or FFprobeInspector()
        self.video_extractor = video_extractor or FFmpegExtractor()
        self.interactive_cropper = interactive_cropper or CenterCropper()
        self.headless_cropper = headless_cropper or CenterCropper()
        self.frame_trimmer = frame_trimmer or BoundingBoxTrimmer()
        self.frame_quantizer = frame_quantizer or PaletteQuantizer()
        self.zip_packer = zip_packer or ZipStorePacker()
        self.verifier = verifier or BootAnimationVerifier()
        self.duration_advisor = duration_advisor or DurationAdvisor()

    _resolve_canonical_segments = staticmethod(_resolve_canonical_segments)

    @staticmethod
    def _sample_frame_paths(paths: list[Path], max_samples: int = 20) -> list[Path]:
        if len(paths) <= 30:
            return paths

        step = len(paths) / max_samples
        indices = [min(int(i * step), len(paths) - 1) for i in range(max_samples)]
        indices.append(len(paths) - 1)

        seen: set[int] = set()
        sampled: list[Path] = []

        for idx in indices:
            if idx not in seen:
                seen.add(idx)
                sampled.append(paths[idx])

        return sampled

    def _compute_uniform_trim(
        self, frame_paths: list[Path], canvas: CanvasResolution
    ) -> tuple[CropRect | None, FrameTrim]:
        sample_paths = self._sample_frame_paths(frame_paths)
        sample_images: list[Image.Image] = []

        for p in sample_paths:
            with Image.open(p) as img:
                sample_images.append(img.copy())

        return self.frame_trimmer.calculate_uniform_trim(sample_images, canvas)

    def _quantize_and_record_frame(
        self,
        frame_path: Path,
        idx: int,
        crop_rect: CropRect | None,
        uniform_trim: FrameTrim,
        part_dir: Path,
    ) -> str:
        with Image.open(frame_path) as img:
            if crop_rect is not None:
                box = (
                    crop_rect.x,
                    crop_rect.y,
                    crop_rect.x + crop_rect.width,
                    crop_rect.y + crop_rect.height,
                )
                cropped_frame = img.crop(box)
            else:
                cropped_frame = img

            out_png = part_dir / f"{idx:05d}.png"
            self.frame_quantizer.quantize_png8(cropped_frame, out_png, crop=None)
        return uniform_trim.to_trim_entry()

    def _compile_part(
        self,
        part_dir: Path,
        frame_paths: list[Path],
        canvas: CanvasResolution,
        progress_callback: ProgressCallback | None = None,
        progress_offset: int = 0,
        total_frames: int = 0,
        precomputed_trim: tuple[CropRect | None, FrameTrim] | None = None,
    ) -> tuple[CropRect | None, FrameTrim]:
        part_dir.mkdir(parents=True, exist_ok=True)
        if precomputed_trim is not None:
            crop_rect, uniform_trim = precomputed_trim
        else:
            crop_rect, uniform_trim = self._compute_uniform_trim(frame_paths, canvas)

        trim_lines: list[str] = []
        for idx, frame_path in enumerate(frame_paths):
            trim_lines.append(
                self._quantize_and_record_frame(frame_path, idx, crop_rect, uniform_trim, part_dir)
            )
            if progress_callback:
                progress_callback("quantize", progress_offset + idx + 1, total_frames)

        trim_content = "\n".join(trim_lines) + "\n"
        (part_dir / "trim.txt").write_bytes(trim_content.encode())
        return crop_rect, uniform_trim

    @staticmethod
    def _open_image_copy(path: Path) -> Image.Image | None:
        try:
            with Image.open(path) as img:
                return img.copy()
        except (
            OSError,
            UnidentifiedImageError,
            ValueError,
        ):
            return None

    def _extract_preview_image(
        self, video_path: Path, meta: VideoMetadata, tmp_dir: Path
    ) -> Image.Image:
        preview_img = self._open_image_copy(video_path)
        if preview_img is not None:
            return preview_img

        preview_dir = tmp_dir / "preview"
        preview_dir.mkdir(parents=True, exist_ok=True)
        try:
            preview_frames = self.video_extractor.extract_frames(
                video_path,
                preview_dir,
                target_fps=1,
                max_duration=1.0,
            )
        except (
            OSError,
            UnidentifiedImageError,
            VideoExtractionError,
            ValueError,
        ):
            preview_frames = []

        if not preview_frames:
            return Image.new("RGB", (meta.width, meta.height))

        frame_img = self._open_image_copy(preview_frames[0])
        if frame_img is not None:
            return frame_img

        return Image.new("RGB", (meta.width, meta.height))

    def _resolve_crop(
        self,
        video_path: Path,
        meta: VideoMetadata,
        canvas: CanvasResolution,
        effective_fps: int,
        interactive_crop: bool,
        start_time: float,
        duration: float | None,
        tmp_dir: Path,
    ) -> tuple[CropRect | None, float, float | None]:
        target_aspect = canvas.width / canvas.height
        video_aspect = meta.width / meta.height

        if not interactive_crop and abs(video_aspect - target_aspect) <= 1e-3:
            return None, start_time, duration

        preview_img = self._extract_preview_image(video_path, meta, tmp_dir)
        cropper = self.interactive_cropper if interactive_crop else self.headless_cropper
        crop_result = cropper.select_crop(
            preview_img,
            canvas,
            video_duration=meta.duration,
            fps=effective_fps,
        )

        if isinstance(crop_result, EditorResult):
            resolved_crop = crop_result.crop
            resolved_start = (
                crop_result.start_time
                if crop_result.start_time > 0 and start_time == 0.0
                else start_time
            )
            resolved_duration = (
                crop_result.duration if crop_result.duration is not None else duration
            )
            return resolved_crop, resolved_start, resolved_duration

        return crop_result, start_time, duration

    def _compile_loop_part(
        self,
        build_dir: Path,
        raw_frames: list[Path],
        canvas: CanvasResolution,
        progress_callback: ProgressCallback | None,
        precomputed_trim: tuple[CropRect | None, FrameTrim] | None,
        bg_color: str = "#000000",
    ) -> list[PartConfig]:
        part0_dir = build_dir / "part0"
        self._compile_part(
            part0_dir,
            raw_frames,
            canvas,
            progress_callback=progress_callback,
            progress_offset=0,
            total_frames=len(raw_frames),
            precomputed_trim=precomputed_trim,
        )

        return [
            PartConfig(
                type_code="p",
                count=0,
                pause=0,
                folder_name="part0",
                bg_color=bg_color,
                clock_pos=-1,
            )
        ]

    def _compile_hold_last_parts(
        self,
        build_dir: Path,
        raw_frames: list[Path],
        canvas: CanvasResolution,
        progress_callback: ProgressCallback | None,
        bg_color: str = "#000000",
    ) -> list[PartConfig]:
        total_frames = len(raw_frames)
        if total_frames == 1:
            # A 1-frame animation has no preceding sequence to play once, so it is
            # normalized to a single partition part0 that loops indefinitely (count=0)
            part0_dir = build_dir / "part0"
            self._compile_part(
                part0_dir,
                raw_frames,
                canvas,
                progress_callback=progress_callback,
                progress_offset=0,
                total_frames=1,
            )

            return [
                PartConfig(
                    type_code="p",
                    count=0,
                    pause=0,
                    folder_name="part0",
                    bg_color=bg_color,
                    clock_pos=-1,
                )
            ]

        part0_dir = build_dir / "part0"
        part1_dir = build_dir / "part1"

        self._compile_part(
            part0_dir,
            raw_frames[:-1],
            canvas,
            progress_callback=progress_callback,
            progress_offset=0,
            total_frames=total_frames,
        )

        self._compile_part(
            part1_dir,
            raw_frames[-1:],
            canvas,
            progress_callback=progress_callback,
            progress_offset=total_frames - 1,
            total_frames=total_frames,
        )

        return [
            PartConfig(
                type_code="c",
                count=1,
                pause=0,
                folder_name="part0",
                bg_color=bg_color,
                clock_pos=-1,
            ),
            PartConfig(
                type_code="p",
                count=0,
                pause=0,
                folder_name="part1",
                bg_color=bg_color,
                clock_pos=-1,
            ),
        ]

    def _compile_segment_parts(
        self,
        build_dir: Path,
        raw_frames: list[Path],
        canvas: CanvasResolution,
        effective_fps: int,
        loop_start: float,
        progress_callback: ProgressCallback | None,
        bg_color: str = "#000000",
    ) -> list[PartConfig]:
        total_duration = len(raw_frames) / effective_fps
        if loop_start >= total_duration:
            raise CompilationError(
                f"loop_start ({loop_start}s) must be less than duration ({total_duration:.2f}s)"
            )

        split_idx = max(1, min(len(raw_frames) - 1, round(loop_start * effective_fps)))
        total_frames = len(raw_frames)

        part0_dir = build_dir / "part0"
        part1_dir = build_dir / "part1"

        self._compile_part(
            part0_dir,
            raw_frames[:split_idx],
            canvas,
            progress_callback=progress_callback,
            progress_offset=0,
            total_frames=total_frames,
        )
        self._compile_part(
            part1_dir,
            raw_frames[split_idx:],
            canvas,
            progress_callback=progress_callback,
            progress_offset=split_idx,
            total_frames=total_frames,
        )

        return [
            PartConfig(
                type_code="c",
                count=1,
                pause=0,
                folder_name="part0",
                bg_color=bg_color,
                clock_pos=-1,
            ),
            PartConfig(
                type_code="p",
                count=0,
                pause=0,
                folder_name="part1",
                bg_color=bg_color,
                clock_pos=-1,
            ),
        ]

    def _extract_raw_frames(
        self,
        video_path: Path,
        raw_frames_dir: Path,
        effective_fps: int,
        crop: CropRect | None,
        fit_scale: tuple[int, int],
        max_duration: float | None,
        start_time: float,
        duration: float | None,
    ) -> list[Path]:
        try:
            raw_frames = self.video_extractor.extract_frames(
                video_path,
                raw_frames_dir,
                target_fps=effective_fps,
                crop=crop,
                scale=fit_scale,
                max_duration=max_duration,
                start_time=start_time,
                duration=duration,
            )
        except (OSError, VideoExtractionError) as exc:
            raise CompilationError(f"Frame extraction failed: {exc}") from exc

        if not raw_frames:
            raise CompilationError("No frames extracted from video")

        return raw_frames

    def _resolve_loop_mode(
        self,
        raw_frames: list[Path],
        canvas: CanvasResolution,
        loop_mode: LoopMode,
    ) -> tuple[LoopMode, tuple[CropRect | None, FrameTrim] | None]:
        if loop_mode != LoopMode.LOOP:
            return loop_mode, None

        crop_rect, uniform_trim = self._compute_uniform_trim(raw_frames, canvas)
        total_frames = len(raw_frames)
        vram_bytes = total_frames * uniform_trim.width * uniform_trim.height * 4
        vram_mb = vram_bytes / (1024 * 1024)
        if vram_mb <= 150.0 and total_frames <= 300:
            return LoopMode.LOOP, (crop_rect, uniform_trim)

        self._log_loop_fallback(vram_mb, total_frames)
        return LoopMode.HOLD_LAST, None

    @staticmethod
    def _log_loop_fallback(vram_mb: float, total_frames: int) -> None:
        if vram_mb > 150.0:
            logger.warning(
                "Looping VRAM (%.1f MB) exceeds 150.0 MB threshold; falling back to hold-last",
                vram_mb,
            )
            return

        logger.warning(
            "Looping frame count (%d) exceeds 300 ceiling; falling back to hold-last",
            total_frames,
        )

    def _package_and_verify(
        self,
        build_dir: Path,
        output_zip: Path,
        progress_callback: ProgressCallback | None,
        max_vram_mb: float = 150.0,
        force_override_vram: bool = False,
    ) -> Path:
        if progress_callback:
            progress_callback("pack", 0, 1)

        try:
            self.zip_packer.pack(build_dir, output_zip)
        except (OSError, ArchiveError) as exc:
            raise CompilationError(f"Packaging failed: {exc}") from exc

        if progress_callback:
            progress_callback("pack", 1, 1)

        if progress_callback:
            progress_callback("verify", 0, 1)

        if force_override_vram:
            report = self.verifier.verify(output_zip, max_vram_mb=float("inf"))
        elif max_vram_mb != 150.0:
            report = self.verifier.verify(output_zip, max_vram_mb=max_vram_mb)
        else:
            report = self.verifier.verify(output_zip)

        if not report.is_valid:
            raise CompilationError(f"Verification failed: {report.errors}")

        if progress_callback:
            progress_callback("verify", 1, 1)

        return output_zip

    def _execute_compilation(
        self,
        video_path: Path,
        output_zip: Path,
        canvas: CanvasResolution,
        loop_mode: LoopMode,
        fps: int | None,
        interactive_crop: bool,
        max_duration: float | None,
        start_time: float,
        duration: float | None,
        progress_callback: ProgressCallback | None,
        tmp_dir: Path,
        loop_start: float | None = None,
        bg_color: str = "#000000",
        max_vram_mb: float = 150.0,
        force_override_vram: bool = False,
    ) -> Path:
        build_dir = tmp_dir / "bootanimation"
        build_dir.mkdir(parents=True, exist_ok=True)

        meta = self.video_inspector.probe(video_path)
        if fps is not None:
            effective_fps = fps
        elif isinstance(meta.fps, (int, float)) and meta.fps > 0:
            effective_fps = min(round(meta.fps), 30)
        else:
            effective_fps = 30

        crop, start_time, duration = self._resolve_crop(
            video_path=video_path,
            meta=meta,
            canvas=canvas,
            effective_fps=effective_fps,
            interactive_crop=interactive_crop,
            start_time=start_time,
            duration=duration,
            tmp_dir=tmp_dir,
        )

        if progress_callback:
            progress_callback("extract", 0, 1)

        source_w = crop.width if crop is not None and isinstance(crop.width, int) else meta.width
        source_h = crop.height if crop is not None and isinstance(crop.height, int) else meta.height
        fit_scale = calculate_fit_scale(source_w, source_h, canvas)
        raw_frames_dir = tmp_dir / "raw_frames"

        raw_frames = self._extract_raw_frames(
            video_path=video_path,
            raw_frames_dir=raw_frames_dir,
            effective_fps=effective_fps,
            crop=crop,
            fit_scale=fit_scale,
            max_duration=max_duration,
            start_time=start_time,
            duration=duration,
        )

        if progress_callback:
            progress_callback("extract", 1, 1)

        if loop_start is not None and loop_start > 0.0:
            parts = self._compile_segment_parts(
                build_dir,
                raw_frames,
                canvas,
                effective_fps,
                loop_start,
                progress_callback,
                bg_color=bg_color,
            )
        else:
            loop_mode, loop_trim = self._resolve_loop_mode(raw_frames, canvas, loop_mode)

            if loop_mode == LoopMode.LOOP:
                parts = self._compile_loop_part(
                    build_dir,
                    raw_frames,
                    canvas,
                    progress_callback,
                    loop_trim,
                    bg_color=bg_color,
                )
            else:
                parts = self._compile_hold_last_parts(
                    build_dir,
                    raw_frames,
                    canvas,
                    progress_callback,
                    bg_color=bg_color,
                )

        desc_text = DescBuilder.build(canvas=canvas, fps=effective_fps, parts=parts)
        (build_dir / "desc.txt").write_bytes(desc_text.encode())

        return self._package_and_verify(
            build_dir,
            output_zip,
            progress_callback,
            max_vram_mb=max_vram_mb,
            force_override_vram=force_override_vram,
        )

    def compile_from_video(
        self,
        video_path: Path,
        output_zip: Path,
        canvas: CanvasResolution = DEFAULT_CANVAS,
        loop_mode: LoopMode = LoopMode.LOOP,
        fps: int | None = None,
        interactive_crop: bool = True,
        max_duration: float | None = None,
        start_time: float = 0.0,
        duration: float | None = None,
        progress_callback: ProgressCallback | None = None,
        loop_start: float | None = None,
        segments: Sequence[SegmentSpec] | None = None,
        max_vram_mb: float = 150.0,
        bg_color: str = "#000000",
        auto_repair_vram: bool = False,
        force_override_vram: bool = False,
    ) -> Path:
        _validate_bg_color(bg_color)

        if segments is not None:
            effective_fps = fps or 30
            return self.compile_from_segments(
                video_path=video_path,
                output_zip=output_zip,
                segments=segments,
                canvas=canvas,
                fps=effective_fps,
                progress_callback=progress_callback,
                max_vram_mb=max_vram_mb,
                bg_color=bg_color,
                auto_repair_vram=auto_repair_vram,
                force_override_vram=force_override_vram,
            )

        if not video_path.exists():
            raise VideoNotFoundError(f"Video file not found: {video_path}")

        with tempfile.TemporaryDirectory(prefix="mkboota_build_") as tmp_dir_str:
            return self._execute_compilation(
                video_path=video_path,
                output_zip=output_zip,
                canvas=canvas,
                loop_mode=loop_mode,
                fps=fps,
                interactive_crop=interactive_crop,
                max_duration=max_duration,
                start_time=start_time,
                duration=duration,
                progress_callback=progress_callback,
                tmp_dir=Path(tmp_dir_str),
                loop_start=loop_start,
                bg_color=bg_color,
                max_vram_mb=max_vram_mb,
                force_override_vram=force_override_vram,
            )

    def estimate_compilation(
        self,
        video_path: Path,
        segments: Sequence[SegmentSpec] | None = None,
        canvas: CanvasResolution | None = None,
        fps: int | None = None,
        loop: bool = False,
        loop_start: float | None = None,
        loop_mode: str = "infinite",
    ) -> CompilationEstimate:
        """Inspect video metadata and calculate pre-compilation resource budget"""
        estimator = CompilationEstimator(video_inspector=self.video_inspector)
        return estimator.estimate(
            video_path=video_path,
            segments=segments,
            canvas=canvas,
            fps=fps,
            loop=loop,
            loop_start=loop_start,
            loop_mode=loop_mode,
        )

    _determine_fps = staticmethod(CompilationEstimator.determine_fps)
    _collect_estimate_warnings = staticmethod(CompilationEstimator.collect_warnings)

    def compile_from_segments(
        self,
        video_path: Path,
        output_zip: Path,
        segments: Sequence[SegmentSpec],
        canvas: CanvasResolution = DEFAULT_CANVAS,
        fps: int = 30,
        crop: CropRect | None = None,
        progress_callback: Callable[[str, float], None]
        | Callable[[str, int, int], None]
        | None = None,
        max_vram_mb: float = 150.0,
        bg_color: str = "#000000",
        auto_repair_vram: bool = False,
        force_override_vram: bool = False,
    ) -> Path:
        """Compile video into bootanimation.zip using multi-segment timeline specification"""
        _validate_bg_color(bg_color)
        if not video_path.exists():
            raise VideoNotFoundError(f"Video file not found: {video_path}")

        if fps <= 0:
            raise ValueError(f"Framerate must be positive, got {fps}")

        if not segments:
            raise ValueError("Segments sequence cannot be empty")

        meta = self.video_inspector.probe(video_path)
        canonical_segments = _resolve_canonical_segments(
            video_duration=meta.duration, segments=segments
        )

        total_looping_bytes = 0
        for seg in canonical_segments:
            if not seg.is_looping:
                continue
            seg_end = seg.end_time if seg.end_time is not None else meta.duration
            seg_dur = max(0.0, seg_end - seg.start_time)
            frame_count = max(1, round(seg_dur * fps))
            total_looping_bytes += calculate_partition_vram_bytes(frame_count, canvas)

        projected_vram_mb = bytes_to_mb(total_looping_bytes)
        if not force_override_vram and not auto_repair_vram and projected_vram_mb > max_vram_mb:
            raise CompilationError(
                f"Looping segment VRAM ({projected_vram_mb:.1f} MB) exceeds limit ({max_vram_mb:.1f} MB)"
            )

        with tempfile.TemporaryDirectory(prefix="mkboota_segment_build_") as tmp_dir_str:
            return self._execute_segment_compilation(
                video_path=video_path,
                output_zip=output_zip,
                canonical_segments=canonical_segments,
                meta=meta,
                canvas=canvas,
                fps=fps,
                crop=crop,
                progress_callback=progress_callback,
                tmp_dir=Path(tmp_dir_str),
                bg_color=bg_color,
                auto_repair_vram=auto_repair_vram,
                force_override_vram=force_override_vram,
                max_vram_mb=max_vram_mb,
            )

    @staticmethod
    def _select_decimation_step(
        frame_count: int,
        canvas: CanvasResolution,
        max_vram_mb: float,
    ) -> int:
        for step in (2, 3, 4):
            decimated_count = len(range(0, frame_count, step))
            decimated_vram = bytes_to_mb(calculate_partition_vram_bytes(decimated_count, canvas))
            if decimated_vram <= max_vram_mb:
                return step

        frame_mb = bytes_to_mb(calculate_partition_vram_bytes(1, canvas))
        max_allowed = int(max_vram_mb / frame_mb) if frame_mb > 0 else frame_count
        return max(2, math.ceil(frame_count / max(1, max_allowed)))

    @staticmethod
    def _apply_frame_decimation(part_dir: Path, frames: list[Path], step: int) -> None:
        retained = frames[0::step]
        retained_set = set(retained)

        for frame in frames:
            if frame not in retained_set:
                frame.unlink()

        for idx, frame in enumerate(retained):
            frame.rename(part_dir / f"_tmp_{idx:05d}.png")

        for idx in range(len(retained)):
            temp_file = part_dir / f"_tmp_{idx:05d}.png"
            temp_file.rename(part_dir / f"{idx:05d}.png")

        trim_path = part_dir / "trim.txt"
        if not trim_path.is_file():
            return

        raw_text = trim_path.read_text(encoding="utf-8")
        trim_lines = [line for line in raw_text.splitlines() if line.strip()]
        retained_lines = trim_lines[0::step]
        trim_path.write_text("\n".join(retained_lines) + "\n", encoding="utf-8")

    def _maybe_decimate_looping_part(
        self,
        part_dir: Path,
        canvas: CanvasResolution,
        max_vram_mb: float,
    ) -> None:
        frames = sorted(part_dir.glob("*.png"), key=lambda p: p.name)
        if not frames:
            return

        current_vram = bytes_to_mb(calculate_partition_vram_bytes(len(frames), canvas))
        if current_vram <= max_vram_mb:
            return

        step = self._select_decimation_step(len(frames), canvas, max_vram_mb)
        self._apply_frame_decimation(part_dir, frames, step)

    def _execute_segment_compilation(
        self,
        video_path: Path,
        output_zip: Path,
        canonical_segments: Sequence[SegmentSpec],
        meta: VideoMetadata,
        canvas: CanvasResolution,
        fps: int,
        crop: CropRect | None,
        progress_callback: Callable[..., None] | None,
        tmp_dir: Path,
        bg_color: str = "#000000",
        auto_repair_vram: bool = False,
        force_override_vram: bool = False,
        max_vram_mb: float = 150.0,
    ) -> Path:
        build_dir = tmp_dir / "bootanimation"
        build_dir.mkdir(parents=True, exist_ok=True)

        source_w = crop.width if crop is not None else meta.width
        source_h = crop.height if crop is not None else meta.height
        fit_scale = calculate_fit_scale(source_w, source_h, canvas)

        parts_config: list[PartConfig] = []
        total_segments = len(canonical_segments)

        total_looping_frames = sum(
            max(
                1,
                round(
                    ((s.end_time if s.end_time is not None else meta.duration) - s.start_time) * fps
                ),
            )
            for s in canonical_segments
            if s.is_looping
        )

        for idx, seg in enumerate(canonical_segments):
            folder_name = seg.folder_name or f"part{idx}"
            part_dir = build_dir / folder_name
            raw_dir = tmp_dir / f"raw_{folder_name}"

            seg_duration = (
                seg.end_time if seg.end_time is not None else meta.duration
            ) - seg.start_time
            seg_frame_count = max(1, round(seg_duration * fps))

            _emit_progress(progress_callback, "extract", idx, total_segments)
            try:
                raw_frames = self.video_extractor.extract_frames(
                    video_path=video_path,
                    output_dir=raw_dir,
                    target_fps=fps,
                    crop=crop,
                    scale=fit_scale,
                    start_time=seg.start_time,
                    duration=seg_duration,
                )
            except (OSError, VideoExtractionError) as exc:
                raise CompilationError(
                    f"Frame extraction failed for segment '{folder_name}': {exc}"
                ) from exc

            if not raw_frames:
                raise CompilationError(f"No frames extracted for segment '{folder_name}'")

            _emit_progress(progress_callback, "extract", idx + 1, total_segments)

            _emit_progress(progress_callback, "quantize", idx, total_segments)
            self._compile_part(part_dir=part_dir, frame_paths=raw_frames, canvas=canvas)
            _emit_progress(progress_callback, "quantize", idx + 1, total_segments)

            if auto_repair_vram and seg.is_looping:
                part_budget = (
                    max_vram_mb
                    if total_looping_frames <= 0
                    else max_vram_mb * (seg_frame_count / total_looping_frames)
                )
                self._maybe_decimate_looping_part(part_dir, canvas, part_budget)

            part_bg = seg.bg_color if seg.bg_color != "#000000" else bg_color
            parts_config.append(
                PartConfig(
                    type_code=seg.type_code,
                    count=seg.count,
                    pause=seg.pause,
                    folder_name=folder_name,
                    bg_color=part_bg,
                    clock_pos=seg.clock_pos,
                )
            )

        desc_content = DescBuilder.build(canvas=canvas, fps=fps, parts=parts_config)
        (build_dir / "desc.txt").write_bytes(desc_content.encode())

        return self._package_and_verify_segments(
            build_dir=build_dir,
            output_zip=output_zip,
            progress_callback=progress_callback,
            force_override_vram=force_override_vram,
            max_vram_mb=max_vram_mb,
        )

    def _package_and_verify_segments(
        self,
        build_dir: Path,
        output_zip: Path,
        progress_callback: Callable[..., None] | None,
        force_override_vram: bool = False,
        max_vram_mb: float = 150.0,
    ) -> Path:
        _emit_progress(progress_callback, "pack", 0, 1)
        try:
            self.zip_packer.pack(build_dir, output_zip)
        except (OSError, ArchiveError) as exc:
            raise CompilationError(f"Packaging failed: {exc}") from exc
        _emit_progress(progress_callback, "pack", 1, 1)

        _emit_progress(progress_callback, "verify", 0, 1)
        effective_vram_mb = float("inf") if force_override_vram else max_vram_mb
        report = self.verifier.verify(output_zip, max_vram_mb=effective_vram_mb)
        if not report.is_valid:
            raise CompilationError(f"Verification failed: {report.errors}")
        _emit_progress(progress_callback, "verify", 1, 1)

        return output_zip

    def compile(
        self,
        video_path: Path,
        output_zip: Path,
        canvas: CanvasResolution = DEFAULT_CANVAS,
        loop_mode: LoopMode = LoopMode.LOOP,
        fps: int | None = None,
        interactive_crop: bool = True,
        max_duration: float | None = None,
        start_time: float = 0.0,
        duration: float | None = None,
        progress_callback: ProgressCallback | None = None,
        loop_start: float | None = None,
        segments: Sequence[SegmentSpec] | None = None,
        max_vram_mb: float = 150.0,
        bg_color: str = "#000000",
        auto_repair_vram: bool = False,
        force_override_vram: bool = False,
    ) -> Path:
        """Compile video into bootanimation.zip with backward-compatible dispatch"""
        if segments is not None:
            effective_fps = fps or 30
            return self.compile_from_segments(
                video_path=video_path,
                output_zip=output_zip,
                segments=segments,
                canvas=canvas,
                fps=effective_fps,
                progress_callback=progress_callback,
                max_vram_mb=max_vram_mb,
                bg_color=bg_color,
                auto_repair_vram=auto_repair_vram,
                force_override_vram=force_override_vram,
            )

        return self.compile_from_video(
            video_path=video_path,
            output_zip=output_zip,
            canvas=canvas,
            loop_mode=loop_mode,
            fps=fps,
            interactive_crop=interactive_crop,
            max_duration=max_duration,
            start_time=start_time,
            duration=duration,
            progress_callback=progress_callback,
            loop_start=loop_start,
            max_vram_mb=max_vram_mb,
            bg_color=bg_color,
            auto_repair_vram=auto_repair_vram,
            force_override_vram=force_override_vram,
        )


__all__ = [
    "DEFAULT_CANVAS",
    "AnimationCompiler",
    "DurationAdvice",
    "DurationAdvisor",
    "DurationCategory",
    "ProgressCallback",
]
