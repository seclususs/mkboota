from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import zipfile

from mkboota.models import (
    CanvasResolution,
    FrameTrim,
    bytes_to_mb,
    calculate_partition_vram_bytes,
)
from mkboota.verifier import BootAnimationVerifier


@dataclass(frozen=True, slots=True)
class PartInspectInfo:
    folder_name: str
    type_code: str
    count: int
    pause: int
    frame_count: int
    vram_mb: float
    is_looping: bool


@dataclass(frozen=True, slots=True)
class ArchiveInspectInfo:
    zip_path: Path
    file_size: int
    canvas: CanvasResolution
    fps: int
    parts: tuple[PartInspectInfo, ...]
    total_looping_vram_mb: float
    is_valid: bool


def load_part_trims_from_zip(zf: zipfile.ZipFile, folder_name: str) -> tuple[FrameTrim, ...] | None:
    trim_path = f"{folder_name}/trim.txt"
    if trim_path not in zf.namelist():
        return None

    try:
        content = zf.read(trim_path).decode("utf-8", errors="replace")
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        return tuple(FrameTrim.from_trim_entry(ln) for ln in lines)
    except (ValueError, KeyError):
        return None


class ArchiveInspector:
    def __init__(self, verifier: BootAnimationVerifier | None = None) -> None:
        self._verifier = verifier or BootAnimationVerifier()

    def inspect(self, zip_path: Path) -> ArchiveInspectInfo:
        if not zip_path.exists():
            raise FileNotFoundError(f"Archive file not found: {zip_path}")

        if not zipfile.is_zipfile(zip_path):
            raise ValueError(f"Not a valid zip archive: {zip_path}")

        file_size = zip_path.stat().st_size
        with zipfile.ZipFile(zip_path, "r") as zf:
            namelist = zf.namelist()
            if "desc.txt" not in namelist:
                raise ValueError(f"Missing desc.txt in {zip_path}")

            desc_content = zf.read("desc.txt").decode("utf-8", errors="replace")
            lines = [ln.strip() for ln in desc_content.splitlines() if ln.strip()]
            if not lines:
                raise ValueError("desc.txt is empty")

            header_parts = lines[0].split()
            if len(header_parts) < 3:
                raise ValueError(f"Invalid desc.txt header: '{lines[0]}'")

            width = int(header_parts[0])
            height = int(header_parts[1])
            fps = int(header_parts[2])

            # Auto-align width if not mod-16 to estimate aligned hardware buffer consumption
            canvas = CanvasResolution.from_dimensions(width, height, auto_align=(width % 16 != 0))
            part_infos = self._collect_archive_part_infos(zf, lines, namelist, width, height)

        report = self._verifier.verify(zip_path)
        total_looping_vram = round(sum(p.vram_mb for p in part_infos if p.is_looping), 2)

        return ArchiveInspectInfo(
            zip_path=zip_path,
            file_size=file_size,
            canvas=canvas,
            fps=fps,
            parts=tuple(part_infos),
            total_looping_vram_mb=total_looping_vram,
            is_valid=report.is_valid,
        )

    def _collect_archive_part_infos(
        self,
        zf: zipfile.ZipFile,
        lines: list[str],
        namelist: list[str],
        width: int,
        height: int,
    ) -> list[PartInspectInfo]:
        part_infos: list[PartInspectInfo] = []

        for line in lines[1:]:
            parts = line.strip().split()
            folder = parts[3] if len(parts) >= 4 else ""
            trims = load_part_trims_from_zip(zf, folder) if folder else None

            parsed_part = self._inspect_part_line(line, namelist, width, height, trims)
            if parsed_part is not None:
                part_infos.append(parsed_part)

        return part_infos

    def _inspect_part_line(
        self,
        line: str,
        namelist: Sequence[str],
        width: int,
        height: int,
        trim_entries: Sequence[FrameTrim] | None = None,
    ) -> PartInspectInfo | None:
        parts = line.strip().split()
        if len(parts) < 4:
            return None

        type_code = parts[0]
        try:
            count = int(parts[1])
            pause = int(parts[2])
        except ValueError:
            return None

        folder_name = parts[3]
        prefix = f"{folder_name}/"
        frame_count = sum(
            1 for name in namelist if name.startswith(prefix) and name.lower().endswith(".png")
        )
        is_looping = count != 1
        canvas = CanvasResolution.from_dimensions(width, height, auto_align=(width % 16 != 0))

        # SurfaceFlinger caches textures only for looping partitions (count != 1)
        vram_bytes = (
            calculate_partition_vram_bytes(frame_count, canvas, trim_entries) if is_looping else 0
        )
        vram_mb = bytes_to_mb(vram_bytes)

        return PartInspectInfo(
            folder_name=folder_name,
            type_code=type_code,
            count=count,
            pause=pause,
            frame_count=frame_count,
            vram_mb=round(vram_mb, 2),
            is_looping=is_looping,
        )


def inspect_archive(zip_path: Path) -> ArchiveInspectInfo:
    return ArchiveInspector().inspect(zip_path)


__all__ = [
    "ArchiveInspectInfo",
    "ArchiveInspector",
    "PartInspectInfo",
    "inspect_archive",
    "load_part_trims_from_zip",
]
