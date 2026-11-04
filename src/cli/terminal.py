from pathlib import Path
from typing import Self

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

from mkboota.compiler import DurationAdvice, DurationCategory
from mkboota.inspector import (
    ArchiveInspectInfo,
    PartInspectInfo,
    inspect_archive,
    load_part_trims_from_zip,
)
from mkboota.models import (
    CanvasResolution,
    CompilationEstimate,
    RepairReport,
    VerificationReport,
)
from mkboota.segments import parse_parts_arg


console = Console()
error_console = Console(stderr=True)


class ProgressTracker:
    def __init__(self, c: Console | None = None, quiet: bool = False) -> None:
        self.console = c or console
        self.quiet = quiet
        self.progress = Progress(
            SpinnerColumn(spinner_name="dots"),
            TextColumn("[bold cyan]{task.description}[/bold cyan]"),
            BarColumn(bar_width=32),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=self.console,
            transient=True,
        )
        self.current_task_id: TaskID | None = None
        self.current_phase: str = ""

    def __enter__(self) -> Self:
        if not self.quiet:
            self.progress.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        if not self.quiet:
            self.progress.stop()

    def _switch_phase(self, desc: str, phase: str, total: int) -> None:
        if self.current_task_id is not None:
            self.progress.update(self.current_task_id, completed=total, visible=False)

        self.current_phase = phase
        self.current_task_id = self.progress.add_task(desc, total=max(1, total))

    def update(self, phase: str, current: int, total: int) -> None:
        if self.quiet:
            return

        phase_names = {
            "extract": "[1/3] Extracting frames",
            "quantize": "[2/3] Quantizing PNG-8 frames",
            "repair_extract": "Extracting archive",
            "sanitize": "Sanitizing frames",
            "pack": "Packaging bootanimation.zip",
            "verify": "Verifying AOSP compliance",
        }
        desc = phase_names.get(phase, f"Processing {phase}")

        if phase != self.current_phase or self.current_task_id is None:
            self._switch_phase(desc, phase, total)

        if self.current_task_id is not None:
            self.progress.update(
                self.current_task_id,
                completed=current,
                total=max(1, total),
                description=desc,
            )


def print_banner(c: Console | None = None) -> None:
    out = c or console

    banner_text = Text()
    banner_text.append("mkboota\n", style="bold cyan")
    banner_text.append("AOSP bootanimation tool", style="dim")
    out.print(Panel(banner_text, border_style="cyan", expand=False))


def print_advice(advice: DurationAdvice, c: Console | None = None) -> None:
    out = c or console

    title = "[bold cyan]Duration Advisory[/bold cyan]"
    if advice.category == DurationCategory.SHORT:
        border_style = "cyan"
    elif advice.category == DurationCategory.STANDARD:
        border_style = "green"
    else:
        border_style = "yellow"

    content = (
        f"[bold]Duration:[/bold] {advice.duration:.2f}s\n"
        f"[bold]Recommended Mode:[/bold] {advice.recommended_loop_mode.value}\n"
        f"[bold]Note:[/bold] {advice.message}"
    )
    out.print(Panel(content, title=title, border_style=border_style, expand=False))


def print_success(output_zip: Path, report: VerificationReport, c: Console | None = None) -> None:
    out = c or console

    size_bytes = output_zip.stat().st_size if output_zip.exists() else 0
    if size_bytes >= 1024 * 1024:
        size_str = f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        size_str = f"{size_bytes / 1024:.1f} KB"

    details = [
        f"[bold]Output File:[/bold] {output_zip.resolve()}",
        f"[bold]Archive Size:[/bold] {size_str} ({size_bytes:,} bytes)",
        f"[bold]AOSP Compliance:[/bold] {'PASSED' if report.is_valid else 'FAILED'}",
    ]

    if report.errors:
        details.append("[bold red]Errors:[/bold red]")
        for e in report.errors:
            details.append(f"  * {e}")

    if report.warnings:
        details.append("[bold yellow]Warnings:[/bold yellow]")
        for w in report.warnings:
            details.append(f"  * {w}")

    out.print(
        Panel(
            "\n".join(details),
            title="[bold green]Build Complete[/bold green]",
            border_style="green",
            expand=False,
        )
    )


def print_summary_card(
    output_path: Path,
    file_size: int,
    is_valid: bool,
    canvas: CanvasResolution,
    frame_count: int,
    fps: int,
    vram_mb: float | None = None,
    c: Console | None = None,
) -> None:
    out = c or console

    if file_size >= 1024 * 1024:
        size_str = f"{file_size / (1024 * 1024):.1f} MB"
    else:
        size_str = f"{file_size / 1024:.1f} KB"

    status_str = "[bold green]PASSED[/bold green]" if is_valid else "[bold red]FAILED[/bold red]"

    table = Table(title="Build Summary", box=box.ROUNDED, show_header=False)
    table.add_column("Property", style="bold cyan")
    table.add_column("Value")

    table.add_row("Output File", f"{output_path} ({size_str})")
    table.add_row("Resolution", f"{canvas.width}x{canvas.height} ({canvas.aspect_ratio:.2f})")
    table.add_row("Frames & FPS", f"{frame_count} frames @ {fps} FPS")
    if vram_mb is not None:
        table.add_row("Looping VRAM", f"~{vram_mb:.1f} MB")
    table.add_row("AOSP Compliance", status_str)

    out.print(table)


def _format_size_change(original: int, repaired: int) -> str:
    if original <= 0:
        return f"{original} bytes -> {repaired} bytes"

    diff_pct = ((repaired - original) / original) * 100
    if abs(diff_pct) < 0.05:
        return f"{original} bytes -> {repaired} bytes (0.0%)"

    return f"{original} bytes -> {repaired} bytes ({diff_pct:+.1f}%)"


def _format_fixes(fixes: tuple[str, ...]) -> str:
    if not fixes:
        return "None"
    return "\n".join(f"• {escape(fix)}" for fix in fixes)


def print_repair_summary_card(report: RepairReport, c: Console | None = None) -> None:
    out = c or console

    status_str = (
        "[bold green]PASSED[/bold green]"
        if report.verification.is_valid
        else "[bold red]FAILED[/bold red]"
    )
    size_str = _format_size_change(report.original_size_bytes, report.repaired_size_bytes)
    vram_str = f"{report.original_vram_mb:.1f} MB -> {report.repaired_vram_mb:.1f} MB"
    fixes_str = _format_fixes(report.fixes_applied)

    table = Table(title="Repair Summary", box=box.ROUNDED, show_header=False)
    table.add_column("Property", style="bold cyan")
    table.add_column("Value")

    table.add_row("Target Archive", str(report.output_path))
    table.add_row("File Size", size_str)
    table.add_row("Looping VRAM", vram_str)
    table.add_row("Fixes Applied", fixes_str)
    table.add_row("AOSP Compliance", status_str)

    out.print(table)


def print_structured_error(
    message: str,
    why: str | None = None,
    fix: str | None = None,
    c: Console | None = None,
) -> None:
    out = c or error_console

    lines = [f"[bold red]error:[/bold red] {escape(message)}"]

    if why is not None:
        lines.append(f"  [dim]=[/dim] [bold]why:[/bold] {escape(why)}")

    if fix is not None:
        lines.append(f"  [dim]=[/dim] [bold cyan]fix:[/bold cyan] {escape(fix)}")

    out.print("\n".join(lines))


def print_error(message: str, c: Console | None = None) -> None:
    out = c or error_console
    out.print(
        Panel(
            f"[bold red]{message}[/bold red]",
            title="[bold red]Error[/bold red]",
            border_style="red",
            expand=False,
        )
    )


def print_info(message: str, c: Console | None = None) -> None:
    out = c or console
    out.print(f"[cyan]•[/cyan] {message}")


def print_compilation_estimate_table(
    estimate: CompilationEstimate, c: Console | None = None
) -> None:
    out = c or console

    table = Table(title="Compilation Estimate", box=box.ROUNDED)
    table.add_column("Property", style="bold cyan")
    table.add_column("Value")

    table.add_row(
        "Resolution",
        f"{estimate.canvas.width}x{estimate.canvas.height} ({estimate.canvas.aspect_ratio:.2f})",
    )
    table.add_row("Framerate", f"{estimate.fps} FPS")
    table.add_row("Total Frames", str(estimate.total_frames))
    table.add_row("Looping VRAM", f"~{estimate.looping_vram_mb:.1f} MB")

    size_bytes = estimate.estimated_zip_size_bytes
    if size_bytes >= 1024 * 1024:
        size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        size_str = f"{size_bytes / 1024:.1f} KB"

    table.add_row("Estimated Zip Size", f"~{size_str}")
    out.print(table)

    parts_table = Table(title="Partitions", box=box.SIMPLE)
    parts_table.add_column("Folder", style="cyan")
    parts_table.add_column("Type")
    parts_table.add_column("Count")
    parts_table.add_column("Pause")
    parts_table.add_column("Frames")
    parts_table.add_column("VRAM")
    parts_table.add_column("Looping")

    for p in estimate.parts:
        parts_table.add_row(
            p.folder_name,
            p.type_code,
            str(p.count),
            str(p.pause),
            str(p.frame_count),
            f"{p.vram_mb:.1f} MB",
            "Yes" if p.is_looping else "No",
        )
    out.print(parts_table)

    if estimate.warnings:
        warn_text = "\n".join(f"• {escape(w)}" for w in estimate.warnings)
        out.print(
            Panel(warn_text, title="[bold yellow]Warnings[/bold yellow]", border_style="yellow")
        )


def print_verification_table(
    report: VerificationReport, zip_path: Path | None = None, c: Console | None = None
) -> None:
    out = c or console
    status_str = (
        "[bold green]PASSED[/bold green]" if report.is_valid else "[bold red]FAILED[/bold red]"
    )

    table = Table(title="Verification Report", box=box.ROUNDED)
    table.add_column("Check", style="bold cyan")
    table.add_column("Result")

    if zip_path is not None:
        table.add_row("Target Archive", str(zip_path))

    table.add_row("AOSP Compliance", status_str)
    table.add_row("Errors", str(len(report.errors)))
    table.add_row("Warnings", str(len(report.warnings)))
    out.print(table)

    if report.errors:
        err_text = "\n".join(f"• {escape(e)}" for e in report.errors)
        out.print(Panel(err_text, title="[bold red]Errors[/bold red]", border_style="red"))

    if report.warnings:
        warn_text = "\n".join(f"• {escape(w)}" for w in report.warnings)
        out.print(
            Panel(warn_text, title="[bold yellow]Warnings[/bold yellow]", border_style="yellow")
        )


def print_inspect_card(zip_path: Path, c: Console | None = None) -> None:
    out = c or console
    info = inspect_archive(zip_path)

    size_bytes = info.file_size
    if size_bytes >= 1024 * 1024:
        size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        size_str = f"{size_bytes / 1024:.1f} KB"
    status_str = (
        "[bold green]PASSED[/bold green]" if info.is_valid else "[bold red]FAILED[/bold red]"
    )

    table = Table(title="Archive Inspection", box=box.ROUNDED)
    table.add_column("Property", style="bold cyan")
    table.add_column("Value")

    table.add_row("Archive Path", str(info.zip_path))
    table.add_row("Archive Size", f"{size_str} ({size_bytes:,} bytes)")
    table.add_row(
        "Resolution",
        f"{info.canvas.width}x{info.canvas.height} ({info.canvas.aspect_ratio:.2f})",
    )
    table.add_row("Framerate", f"{info.fps} FPS")
    table.add_row("Looping VRAM", f"~{info.total_looping_vram_mb:.1f} MB")
    table.add_row("AOSP Compliance", status_str)
    out.print(table)

    if info.parts:
        parts_table = Table(title="Partitions", box=box.SIMPLE)
        parts_table.add_column("Folder", style="cyan")
        parts_table.add_column("Type")
        parts_table.add_column("Count")
        parts_table.add_column("Pause")
        parts_table.add_column("Frames")
        parts_table.add_column("VRAM")
        parts_table.add_column("Looping")

        for p in info.parts:
            parts_table.add_row(
                p.folder_name,
                p.type_code,
                str(p.count),
                str(p.pause),
                str(p.frame_count),
                f"{p.vram_mb:.1f} MB",
                "Yes" if p.is_looping else "No",
            )
        out.print(parts_table)


__all__ = [
    "ArchiveInspectInfo",
    "PartInspectInfo",
    "ProgressTracker",
    "inspect_archive",
    "load_part_trims_from_zip",
    "parse_parts_arg",
    "print_advice",
    "print_banner",
    "print_compilation_estimate_table",
    "print_error",
    "print_info",
    "print_inspect_card",
    "print_repair_summary_card",
    "print_structured_error",
    "print_success",
    "print_summary_card",
    "print_verification_table",
]
