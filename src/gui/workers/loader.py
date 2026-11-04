from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
from typing import Generic, TypeVar
import zipfile

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage, QPixmap

from mkboota.desc import DescParser
from mkboota.exceptions import DescFormatError, InvalidCropError
from mkboota.models import FrameTrim, alphanumeric_sort_key

FrameT = TypeVar("FrameT", QPixmap, QImage)


@dataclass
class SimulatorPart(Generic[FrameT]):
    folder_name: str
    type_code: str
    count: int
    pause: int
    frames: list[FrameT] = field(default_factory=list)
    bg_color: str = "#000000"
    clock_pos: int = -1
    frame_names: list[str] = field(default_factory=list)


def _parse_trim_lines(
    content: str, folder: str, frame_names: list[str], trims_out: dict[str, FrameTrim]
) -> None:
    lines = [line.strip() for line in content.splitlines() if line.strip()]

    for idx, line in enumerate(lines):
        try:
            trim = FrameTrim.from_trim_entry(line)
        except (ValueError, InvalidCropError):
            continue

        if idx >= len(frame_names):
            continue

        fname = frame_names[idx]
        trims_out[fname] = trim
        trims_out[f"{folder}/{fname}"] = trim


class SimulatorLoadWorker(QThread):
    progress = Signal(int, int, str)
    loaded = Signal(object)
    error = Signal(str)
    cancelled = Signal()

    def __init__(self, path: Path | str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.path = Path(path)
        self.fps: int = 30
        self._cancel_requested = threading.Event()

    def cancel(self) -> None:
        self._cancel_requested.set()

    def is_cancelled(self) -> bool:
        return self._cancel_requested.is_set()

    def _count_zip_frames(self, zf: zipfile.ZipFile, folders: list[str]) -> int:
        prefixes = tuple(f"{folder}/" for folder in folders)
        return sum(
            1 for n in zf.namelist() if n.startswith(prefixes) and n.lower().endswith(".png")
        )

    def _extract_zip_frames(
        self,
        zf: zipfile.ZipFile,
        folder: str,
        start_count: int,
        total_count: int,
    ) -> tuple[list[QImage], list[str]]:
        prefix = f"{folder}/"
        raw_names = [
            n for n in zf.namelist() if n.startswith(prefix) and n.lower().endswith(".png")
        ]
        names = sorted(raw_names, key=alphanumeric_sort_key)
        images: list[QImage] = []
        frame_names: list[str] = []
        curr = start_count

        for fname in names:
            if self.is_cancelled():
                return [], []

            data = zf.read(fname)
            img = QImage()
            img.loadFromData(data)
            if img.isNull():
                continue

            images.append(img)
            frame_names.append(Path(fname).name)
            curr += 1
            self.progress.emit(curr, total_count, f"Loading {folder} ({curr}/{total_count})")

        return images, frame_names

    def _load_zip(self, zip_path: Path) -> None:
        trims: dict[str, FrameTrim] = {}
        with zipfile.ZipFile(zip_path, "r") as zf:
            namelist = set(zf.namelist())
            if "desc.txt" not in namelist:
                self.error.emit(f"Missing desc.txt in {zip_path.name}")
                return

            try:
                desc_text = zf.read("desc.txt").decode("utf-8", errors="replace")
                canvas, fps, part_configs = DescParser.parse(desc_text, auto_align=False)
            except DescFormatError as exc:
                self.error.emit(f"Invalid desc.txt in {zip_path.name}: {exc}")
                return

            canvas_w, canvas_h = canvas.width, canvas.height
            self.fps = max(1, fps)
            total_frames = self._count_zip_frames(zf, [p.folder_name for p in part_configs])
            current_frame = 0

            parts: list[SimulatorPart[QImage]] = []
            for part in part_configs:
                if self.is_cancelled():
                    self.cancelled.emit()
                    return

                images, frame_names = self._extract_zip_frames(
                    zf, part.folder_name, current_frame, total_frames
                )
                if self.is_cancelled():
                    self.cancelled.emit()
                    return

                current_frame += len(images)
                trim_path = f"{part.folder_name}/trim.txt"
                if trim_path in namelist:
                    trim_data = zf.read(trim_path).decode("utf-8", errors="replace")
                    _parse_trim_lines(trim_data, part.folder_name, frame_names, trims)

                parts.append(
                    SimulatorPart(
                        folder_name=part.folder_name,
                        type_code=part.type_code,
                        count=part.count,
                        pause=part.pause,
                        frames=images,
                        bg_color=part.bg_color,
                        clock_pos=part.clock_pos,
                        frame_names=frame_names,
                    )
                )

            if self.is_cancelled():
                self.cancelled.emit()
                return

            self.loaded.emit((parts, trims, canvas_w, canvas_h))

    def _count_dir_frames(self, dir_path: Path, folders: list[str]) -> int:
        total = 0

        for folder in folders:
            folder_dir = dir_path / folder
            if folder_dir.is_dir():
                total += sum(1 for p in folder_dir.iterdir() if p.suffix.lower() == ".png")

        return total

    def _extract_dir_frames(
        self,
        folder_dir: Path,
        folder_name: str,
        start_count: int,
        total_count: int,
    ) -> tuple[list[QImage], list[str]]:
        if not folder_dir.is_dir():
            return [], []

        raw_files = [p for p in folder_dir.iterdir() if p.suffix.lower() == ".png"]
        files = sorted(raw_files, key=lambda p: alphanumeric_sort_key(p.name))
        images: list[QImage] = []
        frame_names: list[str] = []
        curr = start_count

        for p in files:
            if self.is_cancelled():
                return [], []

            img = QImage(str(p))
            if img.isNull():
                continue

            images.append(img)
            frame_names.append(p.name)
            curr += 1
            self.progress.emit(curr, total_count, f"Loading {folder_name} ({curr}/{total_count})")

        return images, frame_names

    def _load_directory(self, dir_path: Path) -> None:
        desc_file = dir_path / "desc.txt"
        if not desc_file.is_file():
            self.error.emit(f"Missing desc.txt in {dir_path.name}")
            return

        try:
            desc_text = desc_file.read_text(encoding="utf-8", errors="replace")
            canvas, fps, part_configs = DescParser.parse(desc_text, auto_align=False)
        except DescFormatError as exc:
            self.error.emit(f"Invalid desc.txt in {dir_path.name}: {exc}")
            return

        canvas_w, canvas_h = canvas.width, canvas.height
        self.fps = max(1, fps)

        total_frames = self._count_dir_frames(dir_path, [p.folder_name for p in part_configs])
        current_frame = 0
        trims: dict[str, FrameTrim] = {}
        parts: list[SimulatorPart[QImage]] = []

        for part in part_configs:
            if self.is_cancelled():
                self.cancelled.emit()
                return

            folder_dir = dir_path / part.folder_name
            images, frame_names = self._extract_dir_frames(
                folder_dir, part.folder_name, current_frame, total_frames
            )
            if self.is_cancelled():
                self.cancelled.emit()
                return

            current_frame += len(images)
            trim_file = folder_dir / "trim.txt"
            if trim_file.is_file():
                trim_data = trim_file.read_text(encoding="utf-8", errors="replace")
                _parse_trim_lines(trim_data, part.folder_name, frame_names, trims)

            parts.append(
                SimulatorPart(
                    folder_name=part.folder_name,
                    type_code=part.type_code,
                    count=part.count,
                    pause=part.pause,
                    frames=images,
                    bg_color=part.bg_color,
                    clock_pos=part.clock_pos,
                    frame_names=frame_names,
                )
            )

        if self.is_cancelled():
            self.cancelled.emit()
            return

        self.loaded.emit((parts, trims, canvas_w, canvas_h))

    def run(self) -> None:
        if not self.path.exists():
            self.error.emit(f"File not found: {self.path.name}")
            return

        if self.is_cancelled():
            self.cancelled.emit()
            return

        try:
            if self.path.is_dir():
                self._load_directory(self.path)
            elif zipfile.is_zipfile(self.path):
                self._load_zip(self.path)
            else:
                self.error.emit(f"Invalid archive: {self.path.name}")
        except (OSError, zipfile.BadZipFile, ValueError) as exc:
            self.error.emit(f"Load error: {exc}")
