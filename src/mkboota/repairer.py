from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import shutil
import tempfile
import zipfile

from mkboota.archive import ZipStorePacker
from mkboota.exceptions import ArchiveError, DescFormatError
from mkboota.imaging import BoundingBoxTrimmer, PaletteQuantizer
from mkboota.models import (
    PartConfig,
    RepairBudgetOptions,
    RepairReport,
    VerificationReport,
)
from mkboota.protocols import (
    ProgressCallback,
    RepairerProtocol,
    VerifierProtocol,
    ZipPackerProtocol,
)
from mkboota.repair.budget_optimizer import BudgetOptimizer
from mkboota.repair.desc_sanitizer import DescSanitizer
from mkboota.repair.frame_sanitizer import FrameSanitizer
from mkboota.repair.part_sanitizer import PartSanitizer
from mkboota.verifier import BootAnimationVerifier


class AnimationRepairer(RepairerProtocol):
    def __init__(
        self,
        verifier: VerifierProtocol | None = None,
        packer: ZipPackerProtocol | None = None,
        quantizer: PaletteQuantizer | None = None,
        trimmer: BoundingBoxTrimmer | None = None,
        desc_sanitizer: DescSanitizer | None = None,
        part_sanitizer: PartSanitizer | None = None,
        frame_sanitizer: FrameSanitizer | None = None,
        budget_optimizer: BudgetOptimizer | None = None,
    ) -> None:
        self._verifier = verifier or BootAnimationVerifier()
        self._packer = packer or ZipStorePacker()
        self._quantizer = quantizer or PaletteQuantizer()
        self._trimmer = trimmer or BoundingBoxTrimmer()
        self._desc_sanitizer = desc_sanitizer or DescSanitizer()
        self._part_sanitizer = part_sanitizer or PartSanitizer()
        self._frame_sanitizer = frame_sanitizer or FrameSanitizer(
            quantizer=self._quantizer, trimmer=self._trimmer
        )
        self._budget_optimizer = budget_optimizer or BudgetOptimizer(quantizer=self._quantizer)

    def repair(
        self,
        input_zip: Path,
        output_zip: Path | None = None,
        progress_callback: ProgressCallback | None = None,
        options: RepairBudgetOptions | None = None,
    ) -> RepairReport:
        target_zip = output_zip if output_zip is not None else self._default_output_path(input_zip)
        if input_zip.resolve() == target_zip.resolve():
            return self._create_failure_report(
                input_zip,
                target_zip,
                "Target output path cannot be identical to input archive",
            )

        precheck_error = self._precheck_archive(input_zip)
        if precheck_error is not None:
            return self._create_failure_report(input_zip, target_zip, precheck_error)

        orig_size = input_zip.stat().st_size
        orig_vram_mb = self._calculate_archive_vram(input_zip)
        was_compressed = self._archive_has_compression(input_zip)

        return self._run_repair_in_temp_dir(
            input_zip,
            target_zip,
            orig_size,
            orig_vram_mb,
            was_compressed,
            progress_callback,
            options,
        )

    def _run_repair_in_temp_dir(
        self,
        input_zip: Path,
        target_zip: Path,
        orig_size: int,
        orig_vram_mb: float,
        was_compressed: bool,
        progress_callback: ProgressCallback | None = None,
        options: RepairBudgetOptions | None = None,
    ) -> RepairReport:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            try:
                return self._execute_repair_pipeline(
                    input_zip,
                    target_zip,
                    work_dir,
                    orig_size,
                    orig_vram_mb,
                    was_compressed,
                    progress_callback,
                    options,
                )
            except (DescFormatError, ValueError, OSError, ArchiveError) as exc:
                return self._create_failure_report(input_zip, target_zip, str(exc))

    def _default_output_path(self, input_zip: Path) -> Path:
        return input_zip.parent / f"{input_zip.stem}_repaired{input_zip.suffix}"

    def _precheck_archive(self, input_zip: Path) -> str | None:
        if not input_zip.exists():
            return f"Zip file not found: {input_zip}"

        if not zipfile.is_zipfile(input_zip):
            return f"Not a valid zip archive: {input_zip}"

        return self._check_zip_desc(input_zip)

    def _check_zip_desc(self, input_zip: Path) -> str | None:
        try:
            with zipfile.ZipFile(input_zip, "r") as zf:
                has_desc = "desc.txt" in zf.namelist()
        except (zipfile.BadZipFile, OSError) as exc:
            return f"Corrupt zip archive: {exc}"

        if not has_desc:
            return "Missing desc.txt"

        return None

    def _create_failure_report(
        self, input_zip: Path, target_zip: Path, error_msg: str
    ) -> RepairReport:
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
            verification=VerificationReport(
                is_valid=False,
                errors=(error_msg,),
                warnings=(),
            ),
        )

    def _archive_has_compression(self, input_zip: Path) -> bool:
        try:
            with zipfile.ZipFile(input_zip, "r") as zf:
                return any(entry.compress_type != zipfile.ZIP_STORED for entry in zf.infolist())
        except (zipfile.BadZipFile, OSError):
            return False

    def _execute_repair_pipeline(
        self,
        input_zip: Path,
        target_zip: Path,
        work_dir: Path,
        orig_size: int,
        orig_vram_mb: float,
        was_compressed: bool,
        progress_callback: ProgressCallback | None = None,
        options: RepairBudgetOptions | None = None,
    ) -> RepairReport:
        fixes: list[str] = []

        # AOSP requires ZIP_STORED (mode 0) for direct memory mapping by SurfaceFlinger
        if was_compressed:
            fixes.append("re-packed with ZIP_STORED")

        self._emit_progress(progress_callback, "repair_extract", 0, 1)
        self._part_sanitizer.extract_clean_archive(input_zip, work_dir, fixes)
        self._emit_progress(progress_callback, "repair_extract", 1, 1)

        desc_path = work_dir / "desc.txt"
        if not desc_path.exists():
            return self._create_failure_report(input_zip, target_zip, "Missing desc.txt")

        canvas_w, canvas_h, fps, orig_w, parts = self._desc_sanitizer.parse_and_normalize(
            desc_path, fixes
        )
        self._sanitize_all_parts(
            work_dir, canvas_w, canvas_h, orig_w, parts, fixes, progress_callback
        )
        budget_options = options if options is not None else RepairBudgetOptions()
        canvas_w, canvas_h, fps = self._budget_optimizer.optimize_budget(
            work_dir, parts, canvas_w, canvas_h, fps, budget_options, fixes
        )

        self._part_sanitizer.purge_extraneous_work_dir_entries(work_dir, parts)
        self._desc_sanitizer.write_final_desc(desc_path, canvas_w, canvas_h, fps, parts)

        self._emit_progress(progress_callback, "pack", 0, 1)
        self._packer.pack(work_dir, target_zip)
        self._emit_progress(progress_callback, "pack", 1, 1)

        self._emit_progress(progress_callback, "verify", 0, 1)
        try:
            verif = self._verifier.verify(target_zip, max_vram_mb=budget_options.max_vram_mb)
        except TypeError:
            verif = self._verifier.verify(target_zip)
        self._emit_progress(progress_callback, "verify", 1, 1)

        repaired_vram_mb = self._calculate_archive_vram(target_zip)
        repaired_size = target_zip.stat().st_size if target_zip.exists() else 0

        return RepairReport(
            input_path=input_zip,
            output_path=target_zip,
            is_success=verif.is_valid,
            fixes_applied=tuple(fixes),
            original_vram_mb=orig_vram_mb,
            repaired_vram_mb=repaired_vram_mb,
            original_size_bytes=orig_size,
            repaired_size_bytes=repaired_size,
            verification=verif,
        )

    def _sanitize_all_parts(
        self,
        work_dir: Path,
        canvas_w: int,
        canvas_h: int,
        orig_w: int,
        parts: list[PartConfig],
        fixes: list[str],
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        total_frames = sum(
            len(self._part_sanitizer.collect_and_sort_frames(work_dir / p.folder_name))
            for p in parts
            if (work_dir / p.folder_name).is_dir()
        )
        offset = 0

        for part in parts:
            part_dir = work_dir / part.folder_name
            if not (part_dir.exists() and part_dir.is_dir()):
                continue

            offset = self._sanitize_single_part(
                part_dir,
                part.folder_name,
                canvas_w,
                canvas_h,
                orig_w,
                fixes,
                progress_callback,
                offset,
                total_frames,
                bg_color=part.bg_color,
            )

    def _sanitize_single_part(
        self,
        part_dir: Path,
        folder_name: str,
        canvas_w: int,
        canvas_h: int,
        orig_w: int,
        fixes: list[str],
        progress_callback: ProgressCallback | None = None,
        offset: int = 0,
        total_frames: int = 0,
        bg_color: str = "#000000",
    ) -> int:
        image_paths = self._part_sanitizer.collect_and_sort_frames(part_dir)
        if not image_paths:
            return offset

        needs_reindex = self._part_sanitizer.check_frame_naming(image_paths, folder_name, fixes)
        trim_file = part_dir / "trim.txt"
        has_matching_trim = self._part_sanitizer.has_matching_trim(trim_file, len(image_paths))

        temp_part_dir = part_dir.with_name(f"{part_dir.name}_sanitized")
        temp_part_dir.mkdir(parents=True, exist_ok=True)

        audio_file = part_dir / "audio.wav"
        if audio_file.is_file():
            shutil.copy2(audio_file, temp_part_dir / "audio.wav")

        if has_matching_trim:
            self._frame_sanitizer.process_part_with_existing_trim(
                temp_part_dir,
                folder_name,
                image_paths,
                trim_file,
                canvas_w,
                orig_w,
                fixes,
                progress_callback,
                offset,
                total_frames,
                needs_reindex=needs_reindex,
                bg_color=bg_color,
            )
        else:
            self._frame_sanitizer.process_part_without_existing_trim(
                temp_part_dir,
                folder_name,
                image_paths,
                canvas_w,
                canvas_h,
                fixes,
                progress_callback,
                offset,
                total_frames,
                needs_reindex=needs_reindex,
                bg_color=bg_color,
            )

        shutil.rmtree(part_dir)
        temp_part_dir.rename(part_dir)
        return offset + len(image_paths)

    def _calculate_archive_vram(self, zip_path: Path) -> float:
        if not zip_path.exists() or not zipfile.is_zipfile(zip_path):
            return 0.0
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                return self._calculate_zip_vram(zf)
        except (zipfile.BadZipFile, OSError):
            return 0.0

    def _calculate_zip_vram(self, zf: zipfile.ZipFile) -> float:
        if "desc.txt" not in zf.namelist():
            return 0.0

        desc_text = zf.read("desc.txt").decode("utf-8-sig", errors="replace")
        lines = [line.strip() for line in desc_text.splitlines() if line.strip()]
        if not lines:
            return 0.0

        header_tokens = lines[0].split()
        if len(header_tokens) < 2:
            return 0.0

        try:
            canvas_w = int(header_tokens[0])
            canvas_h = int(header_tokens[1])
        except ValueError:
            return 0.0

        total_bytes = 0
        namelist = set(zf.namelist())

        for line in lines[1:]:
            tokens = line.split()
            if len(tokens) < 4:
                continue

            try:
                count = int(tokens[1])
            except ValueError:
                continue

            if count == 1:
                continue

            folder = tokens[3]
            total_bytes += self._calculate_part_vram(zf, folder, canvas_w, canvas_h, namelist)

        return round(total_bytes / (1024 * 1024), 2)

    def _calculate_part_vram(
        self,
        zf: zipfile.ZipFile,
        folder: str,
        canvas_w: int,
        canvas_h: int,
        namelist: set[str],
    ) -> int:
        trim_path = f"{folder}/trim.txt"
        if trim_path in namelist:
            trim_bytes = zf.read(trim_path)
            lines = [
                line.strip()
                for line in trim_bytes.decode("utf-8", errors="replace").splitlines()
                if line.strip()
            ]
            return sum(BudgetOptimizer._parse_trim_line_bytes(line) for line in lines)

        valid_exts = (".png", ".jpg", ".jpeg", ".webp")
        frames = [
            n for n in namelist if n.startswith(f"{folder}/") and n.lower().endswith(valid_exts)
        ]
        return len(frames) * canvas_w * canvas_h * 4

    @staticmethod
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


__all__ = ["AnimationRepairer", "RepairBudgetOptions", "RepairerProtocol"]
