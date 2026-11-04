from pathlib import Path
import threading

from PySide6.QtCore import QObject, QThread

from gui.workers.signals import OperationCancelledError, WorkerSignals
from mkboota.exceptions import MkbootaError
from mkboota.models import RepairBudgetOptions
from mkboota.repairer import AnimationRepairer


class RepairWorker(QThread):
    def __init__(
        self,
        input_zip: Path,
        output_zip: Path | None = None,
        options: RepairBudgetOptions | None = None,
        repairer: AnimationRepairer | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.input_zip = input_zip
        self.output_zip = output_zip
        self.options = options
        self.repairer = repairer if repairer is not None else AnimationRepairer()
        self.signals = WorkerSignals()
        self._cancel_requested = threading.Event()

    def cancel(self) -> None:
        self._cancel_requested.set()

    def is_cancelled(self) -> bool:
        return self._cancel_requested.is_set()

    def _target_output(self) -> Path:
        if self.output_zip is not None:
            return self.output_zip
        return self.input_zip.parent / f"{self.input_zip.stem}_repaired.zip"

    def _cleanup_output(self) -> None:
        target = self._target_output()
        if not target.exists():
            return
        try:
            target.unlink()
        except OSError:
            pass

    def _progress_callback(self, phase: str, current: int, total: int) -> None:
        if self._cancel_requested.is_set():
            raise OperationCancelledError("Repair cancelled")

        fraction = current / max(1, total)
        bounded = max(0.0, min(1.0, fraction))
        self.signals.progress.emit(bounded)
        self.signals.status_message.emit(phase)

    def run(self) -> None:
        self.signals.started.emit()
        if self._cancel_requested.is_set():
            self._cleanup_output()
            self.signals.cancelled.emit()
            return

        try:
            report = self.repairer.repair(
                input_zip=self.input_zip,
                output_zip=self.output_zip,
                progress_callback=self._progress_callback,
                options=self.options,
            )

            if self._cancel_requested.is_set():
                self._cleanup_output()
                self.signals.cancelled.emit()
                return

            self.signals.finished.emit(report)
        except OperationCancelledError:
            self._cleanup_output()
            self.signals.cancelled.emit()
        except (MkbootaError, OSError, ValueError, RuntimeError, TypeError) as exc:
            self._cleanup_output()
            self.signals.error.emit(str(exc))
