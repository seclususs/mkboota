from __future__ import annotations

import argparse
from pathlib import Path
import sys

from cli.common import banner, cancel_exit, handle_error, info
from cli.parsers import (
    build_parser,
    has_graphical_display,
    resolve_canvas,
    resolve_output_path,
)
from cli.terminal import (
    ProgressTracker,
    print_advice,
    print_compilation_estimate_table,
    print_structured_error,
    print_summary_card,
)
from cli.wizard import prompt_wizard_inputs
from mkboota.compiler import AnimationCompiler
from mkboota.editor import CenterCropper, CropperUIProtocol
from mkboota.exceptions import (
    CropCancelledError,
    VideoNotFoundError,
    VideoProbeError,
)
from mkboota.models import (
    CanvasResolution,
    LoopMode,
    VerificationReport,
    VideoMetadata,
)
from mkboota.segments import parse_parts_arg


def _ensure_video_exists(video_path: Path) -> None:
    if not video_path.exists():
        raise VideoNotFoundError(f"Video file not found: {video_path}")


def _probe_metadata_and_advise(
    compiler: AnimationCompiler, video_path: Path, quiet: bool = False
) -> VideoMetadata | None:
    try:
        meta = compiler.video_inspector.probe(video_path)
    except (OSError, VideoProbeError, ValueError):
        return None

    if not quiet and isinstance(meta.duration, (int, float)):
        advice = compiler.duration_advisor.evaluate(meta.duration)
        print_advice(advice)

    return meta


def _determine_effective_fps(requested_fps: int | None, meta: VideoMetadata | None) -> int:
    if requested_fps is not None:
        return requested_fps

    if meta is not None and isinstance(meta.fps, (int, float)) and meta.fps > 0:
        return min(round(meta.fps), 30)

    return 30


def _determine_effective_duration(
    requested_duration: float | None, meta: VideoMetadata | None, quiet: bool = False
) -> float | None:
    if meta is None or not isinstance(meta.duration, (int, float)) or meta.duration <= 6.0:
        return requested_duration

    if requested_duration is not None:
        return requested_duration

    info("Duration capped to 5.0s (source exceeds 6.0s)", quiet=quiet)
    return 5.0


def _determine_effective_loop_mode(
    requested_mode: str | None,
    meta: VideoMetadata | None,
    loop_flag: bool = False,
    loop_start: float | None = None,
    quiet: bool = False,
) -> LoopMode:
    if loop_start is not None and loop_start > 0.0:
        return LoopMode.SEGMENT

    if loop_flag or requested_mode == "loop":
        return LoopMode.LOOP

    if requested_mode is not None:
        return LoopMode(requested_mode)

    if meta is None or not isinstance(meta.duration, (int, float)) or meta.duration <= 6.0:
        return LoopMode.LOOP

    info("Using LoopMode.HOLD_LAST to prevent VRAM exhaustion", quiet=quiet)
    return LoopMode.HOLD_LAST


def _compile_animation(
    compiler: AnimationCompiler,
    video_path: Path,
    output_zip: Path,
    canvas: CanvasResolution,
    loop_mode: LoopMode,
    fps: int,
    interactive_crop: bool,
    max_duration: float | None,
    quiet: bool,
    loop_start: float | None = None,
    bg_color: str = "#000000",
) -> Path:
    with ProgressTracker(quiet=quiet) as tracker:
        return compiler.compile_from_video(
            video_path=video_path,
            output_zip=output_zip,
            canvas=canvas,
            loop_mode=loop_mode,
            fps=fps,
            interactive_crop=interactive_crop,
            max_duration=max_duration,
            progress_callback=tracker.update,
            loop_start=loop_start,
            bg_color=bg_color,
        )


def _report_success(
    output_zip: Path,
    report: VerificationReport,
    canvas: CanvasResolution,
    meta: VideoMetadata | None,
    fps: int,
    quiet: bool,
) -> None:
    if quiet:
        print(str(output_zip))
        return

    file_size = output_zip.stat().st_size if output_zip.exists() else 0
    frame_count = meta.frame_count if meta is not None else 0
    print_summary_card(
        output_path=output_zip,
        file_size=file_size,
        is_valid=report.is_valid,
        canvas=canvas,
        frame_count=frame_count,
        fps=fps,
    )


def _execute_compile_dry_run(
    args: argparse.Namespace,
    compiler: AnimationCompiler,
    canvas: CanvasResolution,
    effective_fps: int,
) -> int:
    parsed_segments = parse_parts_arg(args.parts) if args.parts else None
    mode_str = args.loop_mode or ("loop" if args.loop else "infinite")

    estimate = compiler.estimate_compilation(
        video_path=args.video,
        segments=parsed_segments,
        canvas=canvas,
        fps=effective_fps,
        loop=args.loop,
        loop_start=args.loop_start,
        loop_mode=mode_str,
    )

    if not args.quiet:
        print_compilation_estimate_table(estimate)

    return 0


def _compile_parts_flow(
    compiler: AnimationCompiler,
    args: argparse.Namespace,
    output_zip: Path,
    canvas: CanvasResolution,
    fps: int,
    meta: VideoMetadata | None,
) -> int:
    try:
        segments = parse_parts_arg(args.parts)
    except ValueError as exc:
        print_structured_error(
            str(exc),
            why="Invalid segment format",
            fix="Format: <start>-<end>[s]:<type>:<count>:<pause>[:folder][:bgcolor][:clock]",
        )
        return 1

    bg_color = getattr(args, "bg_color", "#000000")
    with ProgressTracker(quiet=args.quiet) as tracker:
        res_zip = compiler.compile_from_segments(
            video_path=args.video,
            output_zip=output_zip,
            segments=segments,
            canvas=canvas,
            fps=fps,
            progress_callback=tracker.update,
            bg_color=bg_color,
        )

    report = compiler.verifier.verify(res_zip)
    _report_success(
        output_zip=res_zip,
        report=report,
        canvas=canvas,
        meta=meta,
        fps=fps,
        quiet=args.quiet,
    )

    return 0


def _resolve_interactive_cropper(interactive_crop: bool, quiet: bool = False) -> CropperUIProtocol:
    if not interactive_crop:
        return CenterCropper()

    try:
        from gui.dialogs import PySide6Cropper

        return PySide6Cropper()
    except (ImportError, RuntimeError, AttributeError, TypeError, OSError) as exc:
        info(
            f"GUI cropper unavailable ({exc}); falling back to center crop",
            quiet=quiet,
        )
        return CenterCropper()


def _execute_compile(args: argparse.Namespace) -> int:
    if args.wizard:
        if not sys.stdin.isatty():
            print_structured_error(
                "Wizard requires interactive TTY session",
                why="stdin not attached to terminal",
                fix="Run without --wizard or attach terminal",
            )
            return 1

        args.resolution, args.fps, wizard_parts = prompt_wizard_inputs(args.resolution, args.fps)
        if wizard_parts:
            args.parts = wizard_parts

    _ensure_video_exists(args.video)

    effective_canvas = resolve_canvas(args.resolution, strict=args.strict, quiet=args.quiet)
    if effective_canvas is None:
        effective_canvas = CanvasResolution(width=960, height=2142)

    interactive_crop = not args.no_ui
    if interactive_crop and not has_graphical_display():
        info("Display not available; defaulting to --no-ui", quiet=args.quiet)
        interactive_crop = False

    interactive_cropper = _resolve_interactive_cropper(interactive_crop, quiet=args.quiet)
    if interactive_crop and isinstance(interactive_cropper, CenterCropper):
        interactive_crop = False

    compiler = AnimationCompiler(interactive_cropper=interactive_cropper)
    meta = _probe_metadata_and_advise(compiler, args.video, quiet=args.quiet)

    effective_fps = _determine_effective_fps(args.fps, meta)
    effective_duration = _determine_effective_duration(args.max_duration, meta, quiet=args.quiet)
    effective_loop_mode = _determine_effective_loop_mode(
        args.loop_mode,
        meta,
        loop_flag=args.loop,
        loop_start=args.loop_start,
        quiet=args.quiet,
    )

    if args.dry_run:
        return _execute_compile_dry_run(args, compiler, effective_canvas, effective_fps)

    allow_gui = interactive_crop and not args.no_ui
    output_zip = resolve_output_path(
        args.output,
        "bootanimation.zip",
        allow_gui=allow_gui,
        quiet=args.quiet,
    )
    if output_zip is None:
        return 0

    output_zip.parent.mkdir(parents=True, exist_ok=True)
    info(f"Compiling '{args.video.name}' to '{output_zip}'", quiet=args.quiet)

    if args.parts:
        return _compile_parts_flow(
            compiler, args, output_zip, effective_canvas, effective_fps, meta
        )

    res_zip = _compile_animation(
        compiler=compiler,
        video_path=args.video,
        output_zip=output_zip,
        canvas=effective_canvas,
        loop_mode=effective_loop_mode,
        fps=effective_fps,
        interactive_crop=interactive_crop,
        max_duration=effective_duration,
        quiet=args.quiet,
        loop_start=args.loop_start,
        bg_color=args.bg_color,
    )

    report = compiler.verifier.verify(res_zip)
    _report_success(
        output_zip=res_zip,
        report=report,
        canvas=effective_canvas,
        meta=meta,
        fps=effective_fps,
        quiet=args.quiet,
    )
    return 0


def run_compile(compile_argv: list[str]) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(compile_argv)
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 0
        return 0 if code == 0 else 1

    banner(args.quiet)

    try:
        return _execute_compile(args)
    except CropCancelledError:
        return cancel_exit("Cropping cancelled", quiet=args.quiet)
    except KeyboardInterrupt:
        return cancel_exit("Operation interrupted", quiet=args.quiet)
    except Exception as exc:
        return handle_error(exc)


__all__ = ["run_compile"]
