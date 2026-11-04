import os
from pathlib import Path
import zipfile

from mkboota.exceptions import ArchiveError
from mkboota.models import alphanumeric_sort_key
from mkboota.protocols import ZipPackerProtocol


class ZipStorePacker(ZipPackerProtocol):
    def pack(self, build_dir: Path, output_zip: Path) -> Path:
        if not build_dir.exists() or not build_dir.is_dir():
            raise ArchiveError(
                f"Build directory not found: {build_dir}",
                path=build_dir,
            )

        desc_file = build_dir / "desc.txt"
        if not desc_file.exists() or not desc_file.is_file():
            raise ArchiveError(
                f"Missing desc.txt: {build_dir}",
                path=build_dir,
            )

        output_zip.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = output_zip.with_suffix(".tmp")

        try:
            with zipfile.ZipFile(tmp_path, mode="w", compression=zipfile.ZIP_STORED) as zf:
                self._write_archive_entries(zf, build_dir)

            tmp_path.replace(output_zip)
            self._set_archive_permissions(output_zip)
            return output_zip
        except ArchiveError:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise
        except (OSError, zipfile.BadZipFile) as exc:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise ArchiveError(
                f"Archive creation failed: {exc}",
                path=output_zip,
            ) from exc

    def _set_archive_permissions(self, path: Path) -> None:
        try:
            path.chmod(0o644)
        except OSError:
            pass

    def _write_archive_entries(self, zf: zipfile.ZipFile, build_dir: Path) -> None:
        desc_file = build_dir / "desc.txt"
        self._write_file_entry(zf, build_dir, desc_file)

        # os.walk visits root first; alphanumeric sorting ensures desc.txt is entry 0 for AOSP mmap
        for root, dirs, files in os.walk(build_dir):
            dirs[:] = sorted((d for d in dirs if not d.startswith(".")), key=alphanumeric_sort_key)
            self._write_directory_files(zf, build_dir, Path(root), files)

    def _write_directory_files(
        self,
        zf: zipfile.ZipFile,
        build_dir: Path,
        root_path: Path,
        files: list[str],
    ) -> None:
        file_paths = [
            root_path / f for f in files if not f.startswith(".") and not f.endswith(".tmp")
        ]
        file_paths = [p for p in file_paths if p.is_file()]

        if root_path == build_dir:
            file_paths = [p for p in file_paths if p.name != "desc.txt"]

        sorted_paths = sorted(file_paths, key=lambda p: alphanumeric_sort_key(p.name))
        for file_path in sorted_paths:
            self._write_file_entry(zf, build_dir, file_path)

    def _write_file_entry(
        self,
        zf: zipfile.ZipFile,
        build_dir: Path,
        file_path: Path,
    ) -> None:
        arcname = file_path.relative_to(build_dir).as_posix()
        info = zipfile.ZipInfo(arcname)
        info.compress_type = zipfile.ZIP_STORED
        # Set standard POSIX regular file permissions (-rw-r--r--)
        info.external_attr = 0o100644 << 16
        zf.writestr(info, file_path.read_bytes())


__all__ = ["ZipPackerProtocol", "ZipStorePacker"]
