from pathlib import Path

from PIL import Image

from mkboota.exceptions import (
    QuantizationError,
    TrimmerError,
)
from mkboota.models import CanvasResolution, CropRect, FrameTrim
from mkboota.protocols import FrameQuantizerProtocol, FrameTrimmerProtocol

DEFAULT_CANVAS = CanvasResolution()


class BoundingBoxTrimmer:
    def calculate_trim(
        self,
        image: Image.Image,
        canvas: CanvasResolution = DEFAULT_CANVAS,
        threshold: int = 2,
    ) -> FrameTrim:
        if not (0 <= threshold <= 255):
            raise ValueError(f"Threshold must be in [0, 255], got {threshold}")

        try:
            grayscale = image.convert("L")
            lut = [0 if i <= threshold else 255 for i in range(256)]
            thresholded = grayscale.point(lut, mode="L")
            bbox = thresholded.getbbox()
            if bbox is None:
                return FrameTrim(2, 2, canvas.width // 2, canvas.height // 2)

            min_x, min_y, max_x, max_y = bbox
            width = max_x - min_x
            height = max_y - min_y

            w_even = max(2, ((width + 1) // 2) * 2)
            h_even = max(2, ((height + 1) // 2) * 2)

            clamped_x = max(0, min(min_x, canvas.width - w_even))
            clamped_y = max(0, min(min_y, canvas.height - h_even))
            clamped_w = max(2, min(w_even, canvas.width - clamped_x))
            clamped_h = max(2, min(h_even, canvas.height - clamped_y))

            if clamped_w % 2 != 0:
                clamped_w -= 1
            if clamped_h % 2 != 0:
                clamped_h -= 1

            return FrameTrim(clamped_w, clamped_h, clamped_x, clamped_y)
        except ValueError:
            raise
        except Exception as err:
            raise TrimmerError(f"Frame trim failed: {err}") from err

    def _collect_frame_bboxes(
        self, images: list[Image.Image], threshold: int
    ) -> list[tuple[int, int, int, int]]:
        lut = [0 if i <= threshold else 255 for i in range(256)]
        bboxes: list[tuple[int, int, int, int]] = []

        for img in images:
            grayscale = img.convert("L")
            thresholded = grayscale.point(lut, mode="L")
            bbox = thresholded.getbbox()
            if bbox is not None:
                bboxes.append(bbox)

        return bboxes

    @staticmethod
    def _create_empty_trim(canvas: CanvasResolution) -> tuple[CropRect | None, FrameTrim]:
        off_x = max(0, (canvas.width - 2) // 2)
        off_y = max(0, (canvas.height - 2) // 2)
        return (CropRect(0, 0, 2, 2), FrameTrim(2, 2, off_x, off_y))

    @staticmethod
    def _create_active_trim(
        bboxes: list[tuple[int, int, int, int]],
        frame_size: tuple[int, int],
        canvas: CanvasResolution,
    ) -> tuple[CropRect | None, FrameTrim]:
        u_min_x = min(b[0] for b in bboxes)
        u_min_y = min(b[1] for b in bboxes)
        u_max_x = max(b[2] for b in bboxes)
        u_max_y = max(b[3] for b in bboxes)
        w = u_max_x - u_min_x
        h = u_max_y - u_min_y
        frame_w, frame_h = frame_size

        coverage = (w * h) / (frame_w * frame_h)
        if coverage >= 0.95:
            even_w = max(2, (frame_w // 2) * 2)
            even_h = max(2, (frame_h // 2) * 2)
            offset_x = max(0, (canvas.width - even_w) // 2)
            offset_y = max(0, (canvas.height - even_h) // 2)
            crop = (
                CropRect(0, 0, even_w, even_h) if (frame_w % 2 != 0 or frame_h % 2 != 0) else None
            )
            return (crop, FrameTrim(even_w, even_h, offset_x, offset_y))

        w_even = max(2, ((w + 1) // 2) * 2)
        h_even = max(2, ((h + 1) // 2) * 2)
        crop_x = max(0, min(u_min_x, frame_w - w_even))
        crop_y = max(0, min(u_min_y, frame_h - h_even))
        offset_x = max(0, (canvas.width - frame_w) // 2 + crop_x)
        offset_y = max(0, (canvas.height - frame_h) // 2 + crop_y)
        crop = CropRect(crop_x, crop_y, w_even, h_even)
        trim = FrameTrim(w_even, h_even, offset_x, offset_y)

        return (crop, trim)

    def calculate_uniform_trim(
        self,
        images: list[Image.Image],
        canvas: CanvasResolution = DEFAULT_CANVAS,
        threshold: int = 2,
    ) -> tuple[CropRect | None, FrameTrim]:
        if not images:
            raise ValueError("Images list cannot be empty")

        if any(img.size != images[0].size for img in images[1:]):
            raise ValueError("Frames must have identical dimensions")

        if not (0 <= threshold <= 255):
            raise ValueError(f"Threshold must be in [0, 255], got {threshold}")

        try:
            bboxes = self._collect_frame_bboxes(images, threshold)
            if not bboxes:
                return self._create_empty_trim(canvas)

            return self._create_active_trim(bboxes, images[0].size, canvas)
        except ValueError:
            raise
        except Exception as err:
            raise TrimmerError(f"Uniform trim failed: {err}") from err


class PaletteQuantizer:
    @staticmethod
    def _prepare_target_image(image: Image.Image, crop: CropRect | None) -> Image.Image:
        if crop is None:
            return image

        if crop.width <= 2 and crop.height <= 2:
            return Image.new("RGB", (crop.width, crop.height), (0, 0, 0))

        box = (
            crop.x,
            crop.y,
            crop.x + crop.width,
            crop.y + crop.height,
        )
        return image.crop(box)

    def quantize_png8(
        self,
        image: Image.Image,
        output_path: Path,
        crop: CropRect | None = None,
        trim: FrameTrim | None = None,
    ) -> None:
        try:
            target_crop = crop
            if target_crop is None and trim is not None:
                target_crop = CropRect(
                    trim.offset_x,
                    trim.offset_y,
                    trim.width,
                    trim.height,
                )

            target = self._prepare_target_image(image, target_crop)
            if target.mode != "RGB":
                target = target.convert("RGB")

            quantized = target.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
            quantized.info.pop("icc_profile", None)
            quantized.info.pop("photoshop", None)
            quantized.info.pop("exif", None)

            output_path.parent.mkdir(parents=True, exist_ok=True)
            quantized.save(output_path, format="PNG", optimize=True, compress_level=9)
        except Exception as err:
            raise QuantizationError(
                f"PNG-8 quantization failed for '{output_path}': {err}"
            ) from err


__all__ = [
    "DEFAULT_CANVAS",
    "BoundingBoxTrimmer",
    "FrameQuantizerProtocol",
    "FrameTrimmerProtocol",
    "PaletteQuantizer",
]
