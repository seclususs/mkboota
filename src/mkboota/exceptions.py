from pathlib import Path


class MkbootaError(Exception):
    """Base exception for mkboota errors"""


class InvalidCanvasError(MkbootaError):
    pass


class InvalidCropError(MkbootaError):
    pass


class DescFormatError(MkbootaError):
    pass


class VideoNotFoundError(MkbootaError):
    pass


class VideoProbeError(MkbootaError):
    pass


class VideoExtractionError(MkbootaError):
    pass


class ImagingError(MkbootaError):
    pass


class TrimmerError(ImagingError):
    pass


class QuantizationError(ImagingError):
    pass


class EditorError(MkbootaError):
    pass


class CropCancelledError(EditorError):
    pass


class OutputRequiredError(MkbootaError):
    pass


class PackagerError(MkbootaError):
    pass


class ArchiveError(PackagerError):
    def __init__(self, message: str, path: Path | None = None) -> None:
        super().__init__(message)
        self.path = path


class VerificationError(PackagerError):
    def __init__(self, message: str, path: Path | None = None) -> None:
        super().__init__(message)
        self.path = path


class EngineError(MkbootaError):
    pass


class CompilationError(EngineError):
    pass


__all__ = [
    "ArchiveError",
    "CompilationError",
    "CropCancelledError",
    "DescFormatError",
    "EditorError",
    "EngineError",
    "ImagingError",
    "InvalidCanvasError",
    "InvalidCropError",
    "MkbootaError",
    "OutputRequiredError",
    "PackagerError",
    "QuantizationError",
    "TrimmerError",
    "VerificationError",
    "VideoExtractionError",
    "VideoNotFoundError",
    "VideoProbeError",
]
