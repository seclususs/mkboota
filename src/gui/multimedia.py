from __future__ import annotations

import ctypes
import os
from pathlib import Path

import PySide6


def _apply_av_log_level(library_path: Path, level: int) -> bool:
    try:
        cdll = ctypes.CDLL(str(library_path))
        fn = getattr(cdll, "av_log_set_level", None)
        if fn is None:
            return False

        fn.argtypes = [ctypes.c_int]
        fn.restype = None
        fn(level)
        return True
    except OSError:
        return False


def _silence_avutil_logger() -> None:
    pyside_dir = Path(PySide6.__file__).resolve().parent
    candidates = [
        path
        for path in pyside_dir.glob("*avutil*")
        if path.suffix.lower() in (".dll", ".so", ".dylib") or ".so." in path.name
    ]

    for path in candidates:
        if _apply_av_log_level(path, 8):
            break


def silence_multimedia_logging() -> None:
    # Silence diagnostic encoder probing and backend noise from QtMultimedia and FFmpeg
    os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia*=false;qt.multimedia.ffmpeg=false")
    try:
        from PySide6.QtCore import QLoggingCategory

        QLoggingCategory.setFilterRules("qt.multimedia*=false\nqt.multimedia.ffmpeg=false")
    except (ImportError, RuntimeError):
        pass

    _silence_avutil_logger()


__all__ = ["silence_multimedia_logging"]
