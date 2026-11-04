from __future__ import annotations

import argparse
from pathlib import Path
import zipfile

from cli.common import cancel_exit, handle_error
from cli.parsers import (
    build_repair_parser,
    has_graphical_display,
    resolve_canvas,
    resolve_output_path,
)
from cli.terminal import (
    ProgressTracker,
    print_repair_summary_card,
    print_structured_error,
)
from mkboota.inspector import load_part_trims_from_zip
from mkboota.models import (
    CanvasResolution,
    PartConfig,
    RepairBudgetOptions,
    RepairReport,
    VerificationReport,
    align_mod16,
    bytes_to_mb,
    calculate_partition_vram_bytes,
)
from mkboota.repairer import AnimationRepairer


def _parse_repair_args(
    repair_argv: list[str],
) -> tuple[argparse.Namespace | None, int | None]:
    parser = build_repair_parser()
    try:
        return parser.parse_args(repair_argv), None
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 0
        return None, 0 if code == 0 else 1


def _print_repair_errors(report: RepairReport) -> None:
    for err in report.verification.errors:
        print_structured_error(err)


def _report_repair_result(report: RepairReport, quiet: bool) -> None:
    if not quiet:
        print_repair_summary_card(report)
        if not report.is_success:
            _print_repair_errors(report)
        return

    if report.is_success:
        print(str(report.output_path))
        return

    _print_repair_errors(report)


def _make_dry_run_failure(input_zip: Path, target_zip: Path, msg: str) -> RepairReport:
    orig_size = input_zip.stat().st_size if input_zip.exists() else 0
    return RepairReport(
        input_path=input_zip,
        output_path=target_zip,
        is_success=False,
        fixes_applied=(),
        original_vram_mb=0.0,
        repaired_vram_mb=0.0,
        original_size_bytes=orig_size,
        repaired_size_bytes=0,
        verification=VerificationReport(is_valid=False, errors=(msg,)),
    )


def _check_zip_compression_and_junk(zf: zipfile.ZipFile, fixes: list[str]) -> None:
    if any(e.compress_type != zipfile.ZIP_STORED for e in zf.infolist()):
        fixes.append("re-packed with ZIP_STORED")

    junk_indicators = ("__MACOSX", "Thumbs.db", ".DS_Store")
    has_junk = any(
        any(
            p in junk_indicators or p.startswith(".")
            for p in e.filename.replace("\\", "/").split("/")
        )
        for e in zf.infolist()
    )
    if has_junk:
        fixes.append("Removed junk metadata files")


def _parse_dry_run_parts(lines: list[str], fixes: list[str]) -> list[PartConfig]:
    parts: list[PartConfig] = []
    for line in lines:
        tokens = line.split()
        if len(tokens) < 4:
            continue

        p_type = tokens[0]
        try:
            count = int(tokens[1])
            pause = int(tokens[2])
        except ValueError:
            continue

        folder = tokens[3]
        if p_type == "f":
            fixes.append(f"Normalized 'f' (fade) partition '{folder}'")

        type_code = ("c" if count == 1 else "p") if p_type == "f" else p_type
        parts.append(
            PartConfig(
                type_code=type_code,
                count=count,
                pause=pause,
                folder_name=folder,
            )
        )

    return parts


def _analyze_dry_run_desc(
    zf: zipfile.ZipFile,
    fixes: list[str],
) -> tuple[CanvasResolution, int, list[PartConfig]] | None:
    content = zf.read("desc.txt")
    if b"\r\n" in content or b"\r" in content:
        fixes.append("Line endings normalized to LF")

    if content.startswith(b"\xef\xbb\xbf"):
        fixes.append("Removed UTF-8 BOM from desc.txt")

    if b"\t" in content:
        fixes.append("Normalized tabs in desc.txt to spaces")

    text = content.decode("utf-8-sig", errors="replace")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None

    header = lines[0].split()
    if len(header) < 3:
        return None
    try:
        orig_w = int(header[0])
        h = int(header[1])
        fps = int(header[2])
    except ValueError:
        return None

    w = align_mod16(orig_w) if orig_w % 16 != 0 else orig_w
    if w != orig_w:
        fixes.append(f"Canvas width adjusted from {orig_w} to {w}")

    canvas = CanvasResolution(width=w, height=h)
    parts = _parse_dry_run_parts(lines[1:], fixes)
    return canvas, fps, parts


def _calc_zf_part_vram(
    zf: zipfile.ZipFile,
    folder: str,
    canvas: CanvasResolution,
    namelist: set[str],
) -> int:
    trims = load_part_trims_from_zip(zf, folder)
    if trims is not None:
        return calculate_partition_vram_bytes(len(trims), canvas, trims)

    valid_exts = (".png", ".jpg", ".jpeg", ".webp")
    frames = [n for n in namelist if n.startswith(f"{folder}/") and n.lower().endswith(valid_exts)]
    return calculate_partition_vram_bytes(len(frames), canvas)


def _project_dry_run_vram(
    zf: zipfile.ZipFile,
    canvas: CanvasResolution,
    parts: list[PartConfig],
    options: RepairBudgetOptions,
    fixes: list[str],
) -> tuple[float, float]:
    namelist = set(zf.namelist())
    looping = [p for p in parts if p.count != 1]
    orig_bytes = sum(_calc_zf_part_vram(zf, p.folder_name, canvas, namelist) for p in looping)
    orig_mb = round(bytes_to_mb(orig_bytes), 2)
    active_canvas = canvas

    if options.target_resolution is not None:
        target_w = align_mod16(options.target_resolution.width)
        target_h = options.target_resolution.height

        if target_w != canvas.width or target_h != canvas.height:
            fixes.append(
                f"Rescaled canvas to {options.target_resolution.width}x{options.target_resolution.height}"
            )
            active_canvas = CanvasResolution(width=target_w, height=target_h)

    if not looping:
        return orig_mb, 0.0

    repaired_bytes = sum(
        _calc_zf_part_vram(zf, p.folder_name, active_canvas, namelist) for p in looping
    )
    repaired_mb = round(bytes_to_mb(repaired_bytes), 2)

    if options.convert_hold_last:
        for p in looping:
            fixes.append(f"Converted looping part '{p.folder_name}' to 1-frame hold")
        return orig_mb, round(
            bytes_to_mb(len(looping) * active_canvas.width * active_canvas.height * 4), 2
        )

    if options.decimate_loop:
        for p in looping:
            fixes.append(
                f"Decimated looping part '{p.folder_name}' for {options.max_vram_mb:.1f} MB VRAM budget"
            )
        return orig_mb, round(repaired_mb / 2.0, 2)

    if repaired_mb > options.max_vram_mb:
        for p in looping:
            fixes.append(
                f"Decimated looping part '{p.folder_name}' for {options.max_vram_mb:.1f} MB VRAM budget"
            )
        return orig_mb, min(repaired_mb, round(options.max_vram_mb, 2))

    return orig_mb, repaired_mb


def _inspect_dry_run_archive(
    zf: zipfile.ZipFile,
    input_zip: Path,
    target_zip: Path,
    options: RepairBudgetOptions,
) -> RepairReport:
    if "desc.txt" not in zf.namelist():
        return _make_dry_run_failure(input_zip, target_zip, "Missing desc.txt")

    fixes: list[str] = []
    _check_zip_compression_and_junk(zf, fixes)
    desc_info = _analyze_dry_run_desc(zf, fixes)
    if desc_info is None:
        return _make_dry_run_failure(input_zip, target_zip, "Invalid desc.txt format")

    canvas, _, parts = desc_info
    orig_vram, rep_vram = _project_dry_run_vram(zf, canvas, parts, options, fixes)
    orig_size = input_zip.stat().st_size

    return RepairReport(
        input_path=input_zip,
        output_path=target_zip,
        is_success=True,
        fixes_applied=tuple(fixes),
        original_vram_mb=orig_vram,
        repaired_vram_mb=rep_vram,
        original_size_bytes=orig_size,
        repaired_size_bytes=orig_size,
        verification=VerificationReport(is_valid=True),
    )


def _analyze_repair_dry_run(
    input_zip: Path,
    output_arg: Path | None,
    options: RepairBudgetOptions,
) -> RepairReport:
    target_zip = output_arg or Path(f"{input_zip.stem}_repaired.zip")
    if not input_zip.exists():
        return _make_dry_run_failure(input_zip, target_zip, f"Zip file not found: {input_zip}")

    if not zipfile.is_zipfile(input_zip):
        return _make_dry_run_failure(input_zip, target_zip, f"Not a valid zip archive: {input_zip}")

    try:
        with zipfile.ZipFile(input_zip, "r") as zf:
            return _inspect_dry_run_archive(zf, input_zip, target_zip, options)
    except (zipfile.BadZipFile, OSError) as exc:
        return _make_dry_run_failure(input_zip, target_zip, f"Corrupt zip archive: {exc}")


def _execute_repair_dry_run(args: argparse.Namespace, options: RepairBudgetOptions) -> int:
    report = _analyze_repair_dry_run(args.input_zip, args.output, options)
    _report_repair_result(report, quiet=args.quiet)
    return 0 if report.is_success else 1


def _execute_repair(args: argparse.Namespace) -> int:
    if not args.input_zip.exists():
        print_structured_error(
            f"Zip file not found: {args.input_zip}",
            why="Archive file not found",
            fix="Verify file path and access permissions",
        )
        return 1

    resolved_target_res = resolve_canvas(
        args.target_resolution, strict=args.strict, quiet=args.quiet
    )
    budget_options = RepairBudgetOptions(
        max_vram_mb=args.max_vram,
        convert_hold_last=args.convert_hold_last,
        decimate_loop=args.decimate_loop,
        loop_fps=args.loop_fps,
        target_resolution=resolved_target_res,
    )

    if args.dry_run:
        return _execute_repair_dry_run(args, budget_options)

    target_zip = resolve_output_path(
        args.output,
        f"{args.input_zip.stem}_repaired.zip",
        title="Save Repaired Bootanimation",
        allow_gui=has_graphical_display(),
        quiet=args.quiet,
    )
    if target_zip is None:
        return 0

    target_zip.parent.mkdir(parents=True, exist_ok=True)
    repairer = AnimationRepairer()
    with ProgressTracker(quiet=args.quiet) as tracker:
        report = repairer.repair(
            args.input_zip,
            target_zip,
            progress_callback=tracker.update,
            options=budget_options,
        )
    _report_repair_result(report, quiet=args.quiet)
    return 0 if report.is_success else 1


def run_repair(repair_argv: list[str]) -> int:
    args, exit_code = _parse_repair_args(repair_argv)
    if exit_code is not None or args is None:
        return exit_code if exit_code is not None else 1

    try:
        return _execute_repair(args)
    except KeyboardInterrupt:
        return cancel_exit("Operation interrupted", quiet=args.quiet)
    except Exception as exc:
        return handle_error(exc)


__all__ = ["run_repair"]
