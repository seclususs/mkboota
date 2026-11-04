from collections.abc import Sequence
import io
from pathlib import Path
import re
import zipfile

from PIL import Image

from mkboota.exceptions import DescFormatError, VerificationError
from mkboota.models import (
    CanvasResolution,
    PartConfig,
    VerificationReport,
    alphanumeric_sort_key,
    bytes_to_mb,
    calculate_partition_vram_bytes,
)
from mkboota.protocols import VerifierProtocol


class BootAnimationVerifier(VerifierProtocol):
    def verify(self, zip_path: Path, max_vram_mb: float = 150.0) -> VerificationReport:
        if not zip_path.exists():
            return VerificationReport(
                is_valid=False,
                errors=("Zip file not found",),
                warnings=(),
            )

        if not zipfile.is_zipfile(zip_path):
            return VerificationReport(
                is_valid=False,
                errors=(f"Not a valid zip archive: {zip_path}",),
                warnings=(),
            )

        errors: list[str] = []
        warnings: list[str] = []

        try:
            with zipfile.ZipFile(zip_path, mode="r") as zf:
                self._verify_archive_contents(zf, errors, warnings, max_vram_mb=max_vram_mb)

            return VerificationReport(
                is_valid=len(errors) == 0,
                errors=tuple(errors),
                warnings=tuple(warnings),
            )
        except zipfile.BadZipFile as exc:
            return VerificationReport(
                is_valid=False,
                errors=(f"Corrupt zip archive: {exc}",),
                warnings=(),
            )
        except (OSError, UnicodeDecodeError) as exc:
            raise VerificationError(
                f"Archive verification failed for {zip_path}: {exc}",
                path=zip_path,
            ) from exc

    def _verify_archive_contents(
        self,
        zf: zipfile.ZipFile,
        errors: list[str],
        warnings: list[str],
        max_vram_mb: float = 150.0,
    ) -> None:
        self._verify_zip_entries(zf, errors)

        namelist = zf.namelist()
        if "desc.txt" not in namelist:
            errors.append("Missing desc.txt")
            return

        desc_content = zf.read("desc.txt")
        self._verify_desc_lines(
            zf, desc_content, namelist, errors, warnings, max_vram_mb=max_vram_mb
        )

    def _verify_zip_entries(
        self,
        zf: zipfile.ZipFile,
        errors: list[str],
    ) -> None:
        for entry in zf.infolist():
            self._verify_entry_compression(entry, errors)
            self._verify_entry_path(entry, errors)

    def _verify_entry_compression(
        self,
        entry: zipfile.ZipInfo,
        errors: list[str],
    ) -> None:
        if entry.compress_type != zipfile.ZIP_STORED:
            errors.append(
                f"Entry '{entry.filename}' is compressed (mode {entry.compress_type}); "
                "requires 0 (ZIP_STORED)"
            )

    def _verify_entry_path(
        self,
        entry: zipfile.ZipInfo,
        errors: list[str],
    ) -> None:
        raw_name = getattr(entry, "orig_filename", entry.filename)

        if len(raw_name) > 255:
            errors.append(f"Path exceeds 255 characters: '{raw_name}'")

        if " " in raw_name:
            errors.append(f"Path contains whitespace: '{raw_name}'")

        if raw_name.startswith("/") or entry.filename.startswith("/"):
            errors.append(f"Path has leading slash: '{raw_name}'")

        if "\\" in raw_name or "\\" in entry.filename:
            errors.append(f"Path uses backslash separator: '{raw_name}'")

        norm_parts = raw_name.replace("\\", "/").split("/")
        if ".." in norm_parts:
            errors.append(f"Path contains directory traversal '..': '{raw_name}'")

    def _verify_desc_lines(
        self,
        zf: zipfile.ZipFile,
        desc_content: bytes,
        namelist: list[str],
        errors: list[str],
        warnings: list[str],
        max_vram_mb: float = 150.0,
    ) -> None:
        if b"\r\n" in desc_content:
            errors.append("desc.txt contains CRLF; requires Unix LF (\\n)")

        if not desc_content.endswith(b"\n"):
            errors.append("desc.txt missing trailing newline")

        desc_text = desc_content.decode("utf-8", errors="replace")
        lines = [line.strip() for line in desc_text.splitlines() if line.strip()]
        if not lines:
            errors.append("desc.txt is empty")
            return

        canvas_width, canvas_height = self._parse_resolution_line(lines[0], errors)
        part_defs = self._parse_part_definitions(lines[1:], errors)

        inspected_folders: set[str] = set()
        for part in part_defs:
            self._verify_part_directory(
                zf,
                part,
                namelist,
                canvas_width,
                canvas_height,
                inspected_folders,
                errors,
                warnings,
            )

        if (
            canvas_width is not None
            and canvas_height is not None
            and canvas_width > 0
            and canvas_height > 0
        ):
            canvas = CanvasResolution.from_dimensions(canvas_width, canvas_height, auto_align=True)
            vram_errors, vram_warnings = self._verify_looping_vram(
                zf, part_defs, canvas, max_vram_mb=max_vram_mb
            )
            errors.extend(vram_errors)
            warnings.extend(vram_warnings)

    def _parse_resolution_line(
        self,
        line: str,
        errors: list[str],
    ) -> tuple[int | None, int | None]:
        header_tokens = line.split()
        if len(header_tokens) < 3:
            errors.append(f"Invalid resolution in desc.txt: '{line}'; expected 'width height fps'")
            return None, None

        try:
            canvas_width = int(header_tokens[0])
            canvas_height = int(header_tokens[1])
            fps = int(header_tokens[2])
        except ValueError:
            errors.append(f"Invalid integer in resolution line: '{line}'")
            return None, None

        if canvas_width <= 0:
            errors.append(f"Width {canvas_width} must be positive")
        elif canvas_width % 16 != 0:
            errors.append(f"Width {canvas_width} is not divisible by 16")

        if canvas_height <= 0:
            errors.append(f"Height {canvas_height} must be positive")

        if fps <= 0:
            errors.append(f"Framerate {fps} must be positive")

        return canvas_width, canvas_height

    def _parse_part_definitions(
        self,
        lines: list[str],
        errors: list[str],
    ) -> list[PartConfig]:
        part_defs: list[PartConfig] = []

        for line in lines:
            parsed = self._parse_single_part_definition(line, errors)
            if parsed is not None:
                part_defs.append(parsed)

        return part_defs

    def _parse_single_part_definition(
        self,
        line: str,
        errors: list[str],
    ) -> PartConfig | None:
        tokens = line.split()
        if len(tokens) < 4:
            errors.append(f"Invalid part definition in desc.txt: '{line}'")
            return None

        p_type = tokens[0]
        if p_type not in ("p", "c"):
            errors.append(f"Invalid part type '{p_type}' in desc.txt: '{line}'; must be 'p' or 'c'")
            return None

        try:
            p_count = int(tokens[1])
            p_pause = int(tokens[2])
        except ValueError:
            errors.append(f"Invalid integer in part definition: '{line}'")
            return None

        if p_count < 0 or p_pause < 0:
            errors.append(f"Part count and pause must be non-negative in '{line}'")
            return None

        p_folder = tokens[3]
        bg = tokens[4] if len(tokens) >= 5 else "#000000"
        clock = -1
        if len(tokens) >= 6:
            try:
                clock = int(tokens[5])
            except ValueError:
                errors.append(f"Invalid clock position in '{line}'")
                return None

        if len(tokens) > 6:
            errors.append(f"Unexpected extra tokens in part definition: '{line}'")
            return None

        try:
            return PartConfig(
                type_code=p_type,
                count=p_count,
                pause=p_pause,
                folder_name=p_folder,
                bg_color=bg,
                clock_pos=clock,
            )
        except DescFormatError as exc:
            errors.append(str(exc))
            return None

    def _verify_part_directory(
        self,
        zf: zipfile.ZipFile,
        part: PartConfig,
        namelist: list[str],
        canvas_width: int | None,
        canvas_height: int | None,
        inspected_folders: set[str],
        errors: list[str],
        warnings: list[str],
    ) -> None:
        part_folder = part.folder_name
        folder_entries = [name for name in namelist if name.startswith(f"{part_folder}/")]
        if not folder_entries:
            errors.append(f"Part folder '{part_folder}' missing in archive")
            return

        folder_pngs = [name for name in folder_entries if name.lower().endswith(".png")]
        sorted_pngs = sorted(folder_pngs, key=alphanumeric_sort_key)
        if folder_pngs != sorted_pngs:
            warnings.append(
                f"Part '{part_folder}' PNG frames are not in sequential order in archive"
            )

        if not sorted_pngs:
            errors.append(f"Part folder '{part_folder}' contains no PNG frames")

        trim_path = f"{part_folder}/trim.txt"
        trim_lines = self._read_trim_lines(zf, trim_path) if trim_path in namelist else []

        if part_folder not in inspected_folders:
            inspected_folders.add(part_folder)
            self._verify_part_entries(part_folder, folder_entries, errors)
            self._inspect_part_folder(
                zf,
                part_folder,
                sorted_pngs,
                trim_path,
                trim_lines,
                namelist,
                canvas_width,
                canvas_height,
                errors,
                warnings,
            )

    def _verify_part_entries(
        self,
        part_folder: str,
        folder_entries: list[str],
        errors: list[str],
    ) -> None:
        for name in folder_entries:
            leaf = name.split("/")[-1]
            if not leaf:
                continue

            if leaf != "audio.wav" and leaf != "trim.txt" and not leaf.lower().endswith(".png"):
                errors.append(f"Partition '{part_folder}' contains unsupported file '{leaf}'")

    def _read_trim_lines(self, zf: zipfile.ZipFile, trim_path: str) -> list[str]:
        trim_bytes = zf.read(trim_path)
        return [
            line_str.strip()
            for line_str in trim_bytes.decode("utf-8", errors="replace").splitlines()
            if line_str.strip()
        ]

    def _inspect_part_folder(
        self,
        zf: zipfile.ZipFile,
        part_folder: str,
        png_frames: list[str],
        trim_path: str,
        trim_lines: list[str],
        namelist: list[str],
        canvas_width: int | None,
        canvas_height: int | None,
        errors: list[str],
        warnings: list[str],
    ) -> None:
        if png_frames:
            self._verify_png_magic(zf, png_frames, errors)

        if trim_path in namelist:
            self._verify_trim_spec(
                part_folder,
                trim_lines,
                len(png_frames),
                canvas_width,
                canvas_height,
                errors,
            )

        if png_frames:
            self._verify_sample_frame(
                zf,
                part_folder,
                png_frames[0],
                trim_path in namelist,
                trim_lines,
                errors,
                warnings,
            )

    def _verify_png_magic(
        self,
        zf: zipfile.ZipFile,
        frames: list[str],
        errors: list[str],
    ) -> None:
        for test_name in (frames[0], frames[-1]):
            self._verify_single_frame_magic(zf, test_name, errors)

    def _verify_single_frame_magic(
        self,
        zf: zipfile.ZipFile,
        test_name: str,
        errors: list[str],
    ) -> None:
        try:
            p_hdr = zf.read(test_name)[:8]
        except (KeyError, OSError, zipfile.BadZipFile) as exc:
            errors.append(f"Cannot read frame '{test_name}': {exc}")
            return

        if p_hdr != b"\x89PNG\r\n\x1a\n":
            errors.append(f"Frame '{test_name}' is not a valid PNG")

    def _verify_trim_spec(
        self,
        part_folder: str,
        trim_lines: list[str],
        frame_count: int,
        canvas_width: int | None,
        canvas_height: int | None,
        errors: list[str],
    ) -> None:
        if len(trim_lines) != frame_count:
            errors.append(
                f"Part '{part_folder}' trim.txt line count ({len(trim_lines)}) does not match frame count ({frame_count})"
            )

        for line in trim_lines:
            self._verify_single_trim_entry(part_folder, line, canvas_width, canvas_height, errors)

    def _verify_single_trim_entry(
        self,
        part_folder: str,
        line: str,
        canvas_width: int | None,
        canvas_height: int | None,
        errors: list[str],
    ) -> None:
        match = re.match(r"^(\d+)x(\d+)\+(\d+)\+(\d+)$", line)
        if not match:
            return

        t_w = int(match.group(1))
        t_h = int(match.group(2))
        t_x = int(match.group(3))
        t_y = int(match.group(4))
        if canvas_width is None or canvas_height is None:
            return

        if t_x + t_w > canvas_width or t_y + t_h > canvas_height:
            errors.append(
                f"Part '{part_folder}' trim entry '{line}' "
                f"exceeds canvas bounds ({canvas_width}x{canvas_height})"
            )

    def _verify_sample_frame(
        self,
        zf: zipfile.ZipFile,
        part_folder: str,
        sample_frame_name: str,
        has_trim: bool,
        trim_lines: list[str],
        errors: list[str],
        warnings: list[str],
    ) -> None:
        try:
            img_data = io.BytesIO(zf.read(sample_frame_name))
            img = Image.open(img_data)
        except (OSError, ValueError) as exc:
            warnings.append(f"Cannot inspect frame '{sample_frame_name}': {exc}")
            return

        with img:
            if img.mode != "P":
                warnings.append(
                    f"Part '{part_folder}' frames are not PNG-8 (found mode '{img.mode}')"
                )
            if has_trim and trim_lines:
                self._check_frame_trim_size(
                    part_folder, sample_frame_name, img.size, trim_lines[0], errors
                )

    def _check_frame_trim_size(
        self,
        part_folder: str,
        sample_frame_name: str,
        actual_size: tuple[int, int],
        trim_line: str,
        errors: list[str],
    ) -> None:
        match = re.match(r"^(\d+)x(\d+)\+(\d+)\+(\d+)$", trim_line)
        if not match:
            return

        t_w = int(match.group(1))
        t_h = int(match.group(2))
        if actual_size != (t_w, t_h):
            errors.append(
                f"Part '{part_folder}' frame '{sample_frame_name}' "
                f"size {actual_size} does not match trim {t_w}x{t_h}"
            )

    def _verify_looping_vram(
        self,
        zf: zipfile.ZipFile,
        parts: Sequence[PartConfig],
        canvas: CanvasResolution,
        max_vram_mb: float = 150.0,
    ) -> tuple[list[str], list[str]]:
        vram_errors: list[str] = []
        vram_warnings: list[str] = []
        namelist = zf.namelist()

        total_vram_bytes = 0
        seen_folders: set[str] = set()

        for part in parts:
            if part.count == 1:
                continue

            if part.folder_name in seen_folders:
                continue

            seen_folders.add(part.folder_name)

            folder_prefix = f"{part.folder_name}/"
            png_frames = [
                n for n in namelist if n.startswith(folder_prefix) and n.lower().endswith(".png")
            ]
            if not png_frames:
                continue

            trim_path = f"{part.folder_name}/trim.txt"
            raw_trim = self._read_trim_lines(zf, trim_path) if trim_path in namelist else []
            valid_trim = [
                line for line in raw_trim if re.match(r"^(\d+)x(\d+)\+(\d+)\+(\d+)$", line)
            ]
            trim_entries = valid_trim if valid_trim else None

            part_bytes = calculate_partition_vram_bytes(
                frame_count=len(png_frames),
                canvas=canvas,
                trim_entries=trim_entries,
            )
            total_vram_bytes += part_bytes

            if len(png_frames) > 300:
                vram_warnings.append(
                    f"Looping part '{part.folder_name}' frame count ({len(png_frames)}) "
                    f"exceeds 300 ceiling"
                )

        vram_mb = bytes_to_mb(total_vram_bytes)
        if vram_mb > max_vram_mb:
            vram_errors.append(
                f"Looping partitions require {vram_mb:.1f} MB VRAM, exceeding {max_vram_mb:.1f} MB safety limit"
            )

        return vram_errors, vram_warnings


__all__ = ["BootAnimationVerifier", "VerifierProtocol"]
