from collections.abc import Sequence
from pathlib import Path
import threading

from PySide6.QtCore import QObject, QThread

from gui.workers.signals import OperationCancelledError, WorkerSignals
from mkboota.compiler import DEFAULT_CANVAS, AnimationCompiler
from mkboota.exceptions import MkbootaError
from mkboota.models import CanvasResolution, CropRect, SegmentSpec


class CompileWorker(QThread):
    def __init__(
        self,
        video_path: Path,
        output_zip: Path,
        segments: Sequence[SegmentSpec],
        canvas: CanvasResolution = DEFAULT_CANVAS,
        fps: int = 30,
        crop: CropRect | None = None,
        compiler: AnimationCompiler | None = None,
        parent: QObject | None = None,
        auto_repair_vram: bool = False,
        force_override_vram: bool = False,
    ) -> None:
        super().__init__(parent)
        self.video_path = video_path
        self.output_zip = output_zip
        self.segments = segments
        self.canvas = canvas
        self.fps = fps
        self.crop = crop
        self.compiler = compiler if compiler is not None else AnimationCompiler()
        self.signals = WorkerSignals()
        self._cancel_requested = threading.Event()
        self.auto_repair_vram = auto_repair_vram
        self.force_override_vram = force_override_vram

    def cancel(self) -> None:
        self._cancel_requested.set()

    def is_cancelled(self) -> bool:
        return self._cancel_requested.is_set()

    def _progress_callback(
        self, phase: str, progress: float | int, total: int | None = None
    ) -> None:
        if self._cancel_requested.is_set():
            raise OperationCancelledError("Compilation cancelled")

        fraction = progress / max(1, total) if total is not None else float(progress)
        bounded = max(0.0, min(1.0, fraction))
        self.signals.progress.emit(bounded)
        self.signals.status_message.emit(phase)

    def _cleanup_output(self) -> None:
        if not self.output_zip.exists():
            return
        try:
            self.output_zip.unlink()
        except OSError:
            pass

    def run(self) -> None:
        self.signals.started.emit()
        if self._cancel_requested.is_set():
            self._cleanup_output()
            self.signals.cancelled.emit()
            return

        try:
            result = self.compiler.compile_from_segments(
                video_path=self.video_path,
                output_zip=self.output_zip,
                segments=self.segments,
                canvas=self.canvas,
                fps=self.fps,
                crop=self.crop,
                progress_callback=self._progress_callback,
                auto_repair_vram=self.auto_repair_vram,
                force_override_vram=self.force_override_vram,
            )

            if self._cancel_requested.is_set():
                self._cleanup_output()
                self.signals.cancelled.emit()
                return

            self.signals.finished.emit(result)
        except OperationCancelledError:
            self._cleanup_output()
            self.signals.cancelled.emit()
        except (MkbootaError, OSError, ValueError, RuntimeError, TypeError) as exc:
            self._cleanup_output()
            self.signals.error.emit(str(exc))
