from __future__ import annotations

from pathlib import Path
import re
import shutil
import zipfile

from mkboota.models import PartConfig, alphanumeric_sort_key


class PartSanitizer:
    _FRAME_NAMING_REGEX = re.compile(r"^(?:frame_)?(\d+)\.png$")

    def extract_clean_archive(self, input_zip: Path, work_dir: Path, fixes: list[str]) -> None:
        has_junk = False

        with zipfile.ZipFile(input_zip, "r") as zf:
            for entry in zf.infolist():
                if self._is_junk_entry(entry.filename):
                    has_junk = True
                    continue

                self._extract_single_entry(zf, entry, work_dir)
        if has_junk:
            fixes.append("Removed junk metadata files")

    def _is_junk_entry(self, name: str) -> bool:
        norm = name.replace("\\", "/")

        parts = [p for p in norm.split("/") if p and p != ".."]
        if any(p.startswith(".") for p in parts):
            return True

        return "__MACOSX" in parts or "Thumbs.db" in parts

    def _extract_single_entry(
        self, zf: zipfile.ZipFile, entry: zipfile.ZipInfo, work_dir: Path
    ) -> None:
        clean_parts = [
            p
            for p in entry.filename.replace("\r", "").replace("\\", "/").split("/")
            if p and p != ".."
        ]
        if not clean_parts:
            return

        dest_path = work_dir.joinpath(*clean_parts)
        if not dest_path.resolve().is_relative_to(work_dir.resolve()):
            return

        if entry.is_dir():
            dest_path.mkdir(parents=True, exist_ok=True)
            return

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(zf.read(entry))

    def collect_and_sort_frames(self, part_dir: Path) -> list[Path]:
        valid_exts = {".png", ".jpg", ".jpeg", ".webp"}
        files = [p for p in part_dir.iterdir() if p.is_file() and p.suffix.lower() in valid_exts]
        # AOSP loads textures in sequential order; numerical alphanumeric sort guarantees correct frame order
        return sorted(files, key=lambda p: alphanumeric_sort_key(p.name))

    def check_frame_naming(
        self, image_paths: list[Path], folder_name: str, fixes: list[str]
    ) -> bool:
        if self._is_valid_frame_sequence(image_paths):
            return False

        fixes.append(f"Part '{folder_name}' frames re-indexed to sequential format")
        return True

    @classmethod
    def _is_valid_frame_sequence(cls, paths: list[Path]) -> bool:
        if not paths:
            return True

        has_prefix = paths[0].name.startswith("frame_")
        indices: list[int] = []
        for p in paths:
            m = cls._FRAME_NAMING_REGEX.match(p.name)
            if not m:
                return False

            if p.name.startswith("frame_") != has_prefix:
                return False

            indices.append(int(m.group(1)))
        return indices == list(range(len(paths)))

    def has_matching_trim(self, trim_file: Path, frame_count: int) -> bool:
        if not trim_file.exists():
            return False

        lines = [
            line.strip()
            for line in trim_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return len(lines) == frame_count

    def purge_extraneous_work_dir_entries(self, work_dir: Path, parts: list[PartConfig]) -> None:
        allowed = {p.folder_name for p in parts}
        allowed.add("desc.txt")

        for item in work_dir.iterdir():
            if item.name in allowed:
                continue

            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink(missing_ok=True)


__all__ = ["PartSanitizer"]
