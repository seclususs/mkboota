"""Worker signals and cancellation definitions for background tasks"""

from PySide6.QtCore import QObject, Signal


class OperationCancelledError(Exception):
    pass


class WorkerSignals(QObject):
    started = Signal()
    progress = Signal(float)
    status_message = Signal(str)
    finished = Signal(object)
    error = Signal(str)
    cancelled = Signal()
