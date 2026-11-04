from __future__ import annotations

from pathlib import Path
import re

from PIL import Image

from mkboota.imaging import PaletteQuantizer
from mkboota.models import (
    CanvasResolution,
    PartConfig,
    RepairBudgetOptions,
    align_mod16,
    alphanumeric_sort_key,
    bytes_to_mb,
    calculate_looping_vram_bytes,
    calculate_partition_vram_bytes,
)


class BudgetOptimizer:
    def __init__(self, quantizer: PaletteQuantizer | None = None) -> None:
        self._quantizer = quantizer or PaletteQuantizer()

    def optimize_budget(
        self,
        work_dir: Path,
        parts: list[PartConfig],
        canvas_w: int,
        canvas_h: int,
        fps: int,
        options: RepairBudgetOptions,
        fixes: list[str],
    ) -> tuple[int, int, int]:
        canvas_w, canvas_h = self.apply_target_resolution(
            work_dir, parts, canvas_w, canvas_h, options, fixes
        )

        looping_parts = [p for p in parts if p.count != 1]
        if not looping_parts:
            return canvas_w, canvas_h, fps

        applied = self._apply_explicit_loop_options(work_dir, looping_parts, fps, options, fixes)
        if not applied:
            self._enforce_vram_budget(work_dir, looping_parts, canvas_w, canvas_h, options, fixes)

        return canvas_w, canvas_h, fps

    def apply_target_resolution(
        self,
        work_dir: Path,
        parts: list[PartConfig],
        canvas_w: int,
        canvas_h: int,
        options: RepairBudgetOptions,
        fixes: list[str],
    ) -> tuple[int, int]:
        if options.target_resolution is None:
            return canvas_w, canvas_h

        target_w = align_mod16(options.target_resolution.width)
        target_h = options.target_resolution.height
        if target_w == canvas_w and target_h == canvas_h:
            return canvas_w, canvas_h

        for part in parts:
            part_dir = work_dir / part.folder_name
            self.rescale_part_frames(part_dir, canvas_w, canvas_h, target_w, target_h)

        fixes.append(
            f"Rescaled canvas to {options.target_resolution.width}x{options.target_resolution.height}"
        )
        return target_w, target_h

    def rescale_part_frames(
        self,
        part_dir: Path,
        old_w: int,
        old_h: int,
        new_w: int,
        new_h: int,
    ) -> None:
        if not part_dir.is_dir():
            return

        scale_x = new_w / old_w
        scale_y = new_h / old_h

        trim_file = part_dir / "trim.txt"
        if trim_file.is_file():
            self._rescale_trimmed_part(part_dir, trim_file, scale_x, scale_y, new_w, new_h)
            return

        self._rescale_full_canvas_part(part_dir, new_w, new_h)

    def _rescale_full_canvas_part(self, part_dir: Path, new_w: int, new_h: int) -> None:
        for frame_path in self._collect_and_sort_frames(part_dir):
            with Image.open(frame_path) as img:
                resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            self._quantizer.quantize_png8(resized, frame_path)
            resized.close()

    def _rescale_trimmed_part(
        self,
        part_dir: Path,
        trim_file: Path,
        scale_x: float,
        scale_y: float,
        canvas_w: int,
        canvas_h: int,
    ) -> None:
        trim_lines = [
            line.strip()
            for line in trim_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        new_trim_lines: list[str] = []

        frames = self._collect_and_sort_frames(part_dir)
        for idx, frame_path in enumerate(frames):
            trim_line = trim_lines[idx] if idx < len(trim_lines) else ""
            new_line = self._rescale_single_trim_frame(
                frame_path, trim_line, scale_x, scale_y, canvas_w, canvas_h
            )
            new_trim_lines.append(new_line)

        trim_file.write_text("\n".join(new_trim_lines) + "\n", encoding="utf-8")

    def _rescale_single_trim_frame(
        self,
        frame_path: Path,
        trim_line: str,
        scale_x: float,
        scale_y: float,
        canvas_w: int,
        canvas_h: int,
    ) -> str:
        m = re.match(r"^(\d+)x(\d+)\+(\d+)\+(\d+)$", trim_line)
        if not m:
            return trim_line

        w = max(2, round(int(m.group(1)) * scale_x))
        h = max(2, round(int(m.group(2)) * scale_y))

        if w % 2 != 0:
            w += 1

        if h % 2 != 0:
            h += 1

        w = min(w, canvas_w)
        h = min(h, canvas_h)
        x = min(round(int(m.group(3)) * scale_x), canvas_w - w)
        y = min(round(int(m.group(4)) * scale_y), canvas_h - h)
        with Image.open(frame_path) as img:
            resized = img.resize((w, h), Image.Resampling.LANCZOS)

        self._quantizer.quantize_png8(resized, frame_path)
        resized.close()
        return f"{w}x{h}+{x}+{y}"

    def _apply_explicit_loop_options(
        self,
        work_dir: Path,
        looping_parts: list[PartConfig],
        fps: int,
        options: RepairBudgetOptions,
        fixes: list[str],
    ) -> bool:
        if options.convert_hold_last:
            self._convert_looping_parts_to_hold_last(work_dir, looping_parts, fixes)
            return True

        if options.loop_fps is not None and options.loop_fps < fps:
            step = max(2, round(fps / options.loop_fps))

            for part in looping_parts:
                part_dir = work_dir / part.folder_name
                self.decimate_part_frames(part_dir, step)
                fixes.append(
                    f"Downsampled looping part '{part.folder_name}' to ~{options.loop_fps} fps (step {step})"
                )

            return True

        if options.decimate_loop:
            for part in looping_parts:
                part_dir = work_dir / part.folder_name
                self.decimate_part_frames(part_dir, step=2)
                fixes.append(f"Decimated looping part '{part.folder_name}' by 2x")

            return True

        return False

    def _enforce_vram_budget(
        self,
        work_dir: Path,
        looping_parts: list[PartConfig],
        canvas_w: int,
        canvas_h: int,
        options: RepairBudgetOptions,
        fixes: list[str],
    ) -> None:
        looping_vram = self.calculate_work_dir_looping_vram(
            work_dir, looping_parts, canvas_w, canvas_h
        )
        if looping_vram <= options.max_vram_mb:
            return

        canvas = CanvasResolution.from_dimensions(canvas_w, canvas_h, auto_align=True)

        for part in looping_parts:
            part_dir = work_dir / part.folder_name
            if not part_dir.is_dir():
                continue

            self._budget_single_looping_part(part, part_dir, canvas, options, fixes)

    def _budget_single_looping_part(
        self,
        part: PartConfig,
        part_dir: Path,
        canvas: CanvasResolution,
        options: RepairBudgetOptions,
        fixes: list[str],
    ) -> None:
        original_frames = self._collect_and_sort_frames(part_dir)
        orig_count = len(original_frames)
        if orig_count <= 1:
            return

        trim_file = part_dir / "trim.txt"
        trim_lines = (
            [
                line.strip()
                for line in trim_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if trim_file.is_file()
            else None
        )

        for step in (2, 3, 4):
            decimated_frames = original_frames[0::step]
            decimated_count = len(decimated_frames)
            decimated_trim = trim_lines[0::step] if trim_lines is not None else None

            projected_bytes = calculate_partition_vram_bytes(
                frame_count=decimated_count,
                canvas=canvas,
                trim_entries=decimated_trim,
            )
            projected_mb = bytes_to_mb(projected_bytes)
            if projected_mb <= options.max_vram_mb:
                self.decimate_part_frames(part_dir, step=step)
                fixes.append(
                    f"Decimated looping part '{part.folder_name}' by {step}x "
                    f"({orig_count} -> {decimated_count} frames) for "
                    f"{options.max_vram_mb:.1f} MB VRAM budget"
                )
                return

        self._handle_unbudgeted_looping_part(
            part, part_dir, orig_count, canvas, trim_lines, options, fixes
        )

    def _handle_unbudgeted_looping_part(
        self,
        part: PartConfig,
        part_dir: Path,
        orig_count: int,
        canvas: CanvasResolution,
        trim_lines: list[str] | None,
        options: RepairBudgetOptions,
        fixes: list[str],
    ) -> None:
        if options.convert_hold_last:
            self.convert_part_to_hold_last(part_dir)
            fixes.append(f"Converted looping part '{part.folder_name}' to 1-frame hold")
            return

        kept_count = self.decimate_part_frames(part_dir, step=4)
        decimated_trim = trim_lines[0::4] if trim_lines is not None else None

        proj_bytes = calculate_partition_vram_bytes(
            frame_count=kept_count,
            canvas=canvas,
            trim_entries=decimated_trim,
        )
        proj_mb = bytes_to_mb(proj_bytes)
        fixes.append(
            f"Decimated looping part '{part.folder_name}' by 4x "
            f"({orig_count} -> {kept_count} frames); "
            f"VRAM exceeds budget ({proj_mb:.1f} MB > {options.max_vram_mb:.1f} MB)"
        )

    def decimate_part_frames(self, part_dir: Path, step: int) -> int:
        if not part_dir.is_dir() or step <= 1:
            return len(self._collect_and_sort_frames(part_dir))

        frames = self._collect_and_sort_frames(part_dir)
        if len(frames) <= 1:
            return len(frames)

        self._decimate_trim_file_if_present(part_dir, step)
        has_prefix = frames[0].name.startswith("frame_")
        kept_frames: list[Path] = []
        for idx, frame_path in enumerate(frames):
            if idx % step == 0:
                kept_frames.append(frame_path)
            else:
                frame_path.unlink()

        for new_idx, frame_path in enumerate(kept_frames):
            prefix = "frame_" if has_prefix else ""
            target_name = f"{prefix}{new_idx:05d}.png"
            if frame_path.name != target_name:
                frame_path.rename(part_dir / target_name)

        return len(kept_frames)

    def _decimate_trim_file_if_present(self, part_dir: Path, step: int) -> None:
        trim_file = part_dir / "trim.txt"
        if not trim_file.is_file():
            return

        lines = [
            line.strip()
            for line in trim_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        kept_lines = [line for idx, line in enumerate(lines) if idx % step == 0]
        trim_file.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")

    def convert_part_to_hold_last(self, part_dir: Path) -> int:
        if not part_dir.is_dir():
            return 0

        frames = self._collect_and_sort_frames(part_dir)
        if not frames:
            return 0

        self._keep_last_trim_line_if_present(part_dir)
        last_frame = frames[-1]
        for frame_path in frames[:-1]:
            frame_path.unlink()

        target_path = part_dir / "00000.png"
        if last_frame != target_path:
            last_frame.rename(target_path)

        return 1

    def _keep_last_trim_line_if_present(self, part_dir: Path) -> None:
        trim_file = part_dir / "trim.txt"
        if not trim_file.is_file():
            return

        lines = [
            line.strip()
            for line in trim_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if lines:
            trim_file.write_text(lines[-1] + "\n", encoding="utf-8")

    def _convert_looping_parts_to_hold_last(
        self, work_dir: Path, parts: list[PartConfig], fixes: list[str]
    ) -> None:
        for part in parts:
            if part.count != 1:
                part_dir = work_dir / part.folder_name
                self.convert_part_to_hold_last(part_dir)
                fixes.append(f"Converted looping part '{part.folder_name}' to 1-frame hold")

    def calculate_work_dir_looping_vram(
        self,
        work_dir: Path,
        parts: list[PartConfig],
        canvas_w: int,
        canvas_h: int,
    ) -> float:
        total_bytes = 0

        for part in parts:
            if part.count == 1:
                continue

            part_dir = work_dir / part.folder_name
            if not part_dir.is_dir():
                continue

            total_bytes += self._calculate_dir_part_vram(part_dir, canvas_w, canvas_h)

        return round(bytes_to_mb(total_bytes), 2)

    def _calculate_dir_part_vram(
        self,
        part_dir: Path,
        canvas_w: int,
        canvas_h: int,
    ) -> int:
        trim_file = part_dir / "trim.txt"
        if trim_file.is_file():
            lines = [
                line.strip()
                for line in trim_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            return sum(self._parse_trim_line_bytes(line) for line in lines)

        frames = self._collect_and_sort_frames(part_dir)
        return calculate_looping_vram_bytes(len(frames), canvas_w, canvas_h)

    def _collect_and_sort_frames(self, part_dir: Path) -> list[Path]:
        valid_exts = {".png", ".jpg", ".jpeg", ".webp"}
        files = [p for p in part_dir.iterdir() if p.is_file() and p.suffix.lower() in valid_exts]
        return sorted(files, key=lambda p: alphanumeric_sort_key(p.name))

    @staticmethod
    def _parse_trim_line_bytes(line: str) -> int:
        m = re.match(r"^(\d+)x(\d+)\+", line)
        if not m:
            return 0

        return int(m.group(1)) * int(m.group(2)) * 4


__all__ = ["BudgetOptimizer"]
