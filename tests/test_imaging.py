from pathlib import Path

from PIL import Image, ImageDraw

from mkboota.editor import CenterCropper
from mkboota.imaging import (
    BoundingBoxTrimmer,
    PaletteQuantizer,
)
from mkboota.models import CanvasResolution, CropRect, FrameTrim


def test_trimmer_centered_square() -> None:
    canvas = CanvasResolution(width=960, height=2142)
    img = Image.new("RGB", (canvas.width, canvas.height), (0, 0, 0))

    draw = ImageDraw.Draw(img)
    draw.rectangle([405, 996, 405 + 150 - 1, 996 + 150 - 1], fill=(255, 255, 255))

    trimmer = BoundingBoxTrimmer()
    trim = trimmer.calculate_trim(img, canvas=canvas, threshold=12)

    assert trim == FrameTrim(width=150, height=150, offset_x=405, offset_y=996)
    assert trim.to_trim_entry() == "150x150+405+996"


def test_trimmer_all_black_frame_guard() -> None:
    canvas = CanvasResolution(width=960, height=2142)
    img = Image.new("RGB", (canvas.width, canvas.height), (0, 0, 0))

    trimmer = BoundingBoxTrimmer()
    trim = trimmer.calculate_trim(img, canvas=canvas)

    assert trim == FrameTrim(width=2, height=2, offset_x=480, offset_y=1071)
    assert trim.to_trim_entry() == "2x2+480+1071"


def test_uniform_trim_multi_frame_union_bbox() -> None:
    trimmer = BoundingBoxTrimmer()
    canvas = CanvasResolution(width=960, height=2142)
    img1 = Image.new("RGB", (540, 960), color=(0, 0, 0))
    img1.paste(Image.new("RGB", (50, 50), (255, 255, 255)), (100, 100))
    img2 = Image.new("RGB", (540, 960), color=(0, 0, 0))
    img2.paste(Image.new("RGB", (50, 50), (255, 255, 255)), (200, 200))
    img3 = Image.new("RGB", (540, 960), color=(0, 0, 0))

    crop_rect, frame_trim = trimmer.calculate_uniform_trim([img1, img2, img3], canvas)

    assert crop_rect == CropRect(x=100, y=100, width=150, height=150)
    assert frame_trim == FrameTrim(
        width=150,
        height=150,
        offset_x=(960 - 540) // 2 + 100,
        offset_y=(2142 - 960) // 2 + 100,
    )


def test_quantize_full_image(tmp_path: Path) -> None:
    img = Image.new("RGB", (200, 200), (120, 50, 200))
    img.info["icc_profile"] = b"MOCK_ICC_PROFILE_PAYLOAD"
    output_file = tmp_path / "frame_00001.png"

    quantizer = PaletteQuantizer()
    quantizer.quantize_png8(img, output_file)

    assert output_file.exists()
    with Image.open(output_file) as loaded:
        assert loaded.format == "PNG"
        assert loaded.mode == "P"
        assert loaded.size == (200, 200)
        assert "icc_profile" not in loaded.info
        palette = loaded.getpalette()
        assert palette is not None
        assert len(palette) // 3 <= 256


def test_center_cropper_aspect_ratio_and_even_dimensions() -> None:
    canvas = CanvasResolution(width=960, height=2142)
    cropper = CenterCropper()

    # Wide 16:9 input image (1920x1080)
    wide_img = Image.new("RGB", (1920, 1080))
    crop = cropper.select_crop(wide_img, canvas=canvas)

    assert crop.width % 2 == 0
    assert crop.height % 2 == 0
    assert crop.height == 1080
    assert crop.width <= 1920
    assert crop.x >= 0
    assert crop.y == 0
