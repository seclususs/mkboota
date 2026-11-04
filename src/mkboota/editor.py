from __future__ import annotations

from dataclasses import dataclass
from PIL import Image

from mkboota.exceptions import CropCancelledError, InvalidCropError
from mkboota.models import CanvasResolution, CropRect
from mkboota.protocols import CropperUIProtocol

DEFAULT_CANVAS = CanvasResolution()


def _make_even_dimension(dim: int, max_limit: int) -> int:
    if dim % 2 == 0:
        return dim

    if dim + 1 <= max_limit:
        return dim + 1

    if dim > 1:
        return dim - 1

    return dim


@dataclass(frozen=True, slots=True)
class EditorResult(CropRect):
    start_time: float = 0.0
    duration: float | None = None

    @property
    def crop(self) -> CropRect:
        return CropRect(x=self.x, y=self.y, width=self.width, height=self.height)


class CenterCropper(CropperUIProtocol):
    def select_crop(
        self,
        preview_image: Image.Image,
        canvas: CanvasResolution = DEFAULT_CANVAS,
        video_duration: float = 0.0,
        fps: int = 30,
    ) -> CropRect:
        w_in, h_in = preview_image.size
        if w_in <= 0 or h_in <= 0:
            raise InvalidCropError(f"Preview image dimensions must be positive: {w_in}x{h_in}")

        target_r = canvas.width / canvas.height
        in_r = w_in / h_in

        if abs(in_r - target_r) < 1e-5:
            return CropRect(x=0, y=0, width=w_in, height=h_in)

        if in_r > target_r:
            h_crop = h_in
            w_crop = round(h_in * target_r)
            w_crop = max(1, min(w_in, w_crop))
        else:
            w_crop = w_in
            h_crop = round(w_in / target_r)
            h_crop = max(1, min(h_in, h_crop))

        w_crop = _make_even_dimension(w_crop, w_in)
        h_crop = _make_even_dimension(h_crop, h_in)

        x_crop = (w_in - w_crop) // 2
        y_crop = (h_in - h_crop) // 2

        return CropRect(x=x_crop, y=y_crop, width=w_crop, height=h_crop)


__all__ = [
    "CenterCropper",
    "CropCancelledError",
    "CropperUIProtocol",
    "DEFAULT_CANVAS",
    "EditorResult",
    "InvalidCropError",
]
