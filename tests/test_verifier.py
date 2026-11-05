from collections.abc import Callable
import io
from pathlib import Path
import zipfile

import pytest
from PIL import Image

from mkboota.archive import ZipStorePacker
from mkboota.verifier import BootAnimationVerifier


def _create_test_png8(size: tuple[int, int] = (64, 64)) -> bytes:
    img = Image.new("P", size, color=0)
    palette = [0] * 768
    palette[0] = 255
    img.putpalette(palette)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _create_test_rgb_png(size: tuple[int, int] = (64, 64)) -> bytes:
    img = Image.new("RGB", size, color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_verify_clean_archive(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "desc.txt").write_bytes(b"960 2142 60\np 0 0 part0\n")

    part0 = build_dir / "part0"
    part0.mkdir()
    png_bytes = _create_test_png8()
    (part0 / "00000.png").write_bytes(png_bytes)
    (part0 / "00001.png").write_bytes(png_bytes)
    (part0 / "trim.txt").write_text("64x64+0+0\n64x64+0+0\n", encoding="utf-8")

    zip_path = tmp_path / "bootanimation.zip"
    ZipStorePacker().pack(build_dir, zip_path)

    verifier = BootAnimationVerifier()
    report = verifier.verify(zip_path)

    assert report.is_valid is True
    assert report.errors == ()
    assert report.warnings == ()


def test_verify_corrupted_file(tmp_path: Path) -> None:
    fake_zip = tmp_path / "corrupt.zip"
    fake_zip.write_bytes(b"not a real zip file header")

    verifier = BootAnimationVerifier()
    report = verifier.verify(fake_zip)

    assert report.is_valid is False
    assert len(report.errors) > 0


def _build_deflate_zip(zf: zipfile.ZipFile) -> None:
    zf.writestr("desc.txt", b"960 2142 60\np 0 0 part0\n", compress_type=zipfile.ZIP_DEFLATED)
    zf.writestr("part0/00000.png", _create_test_png8(), compress_type=zipfile.ZIP_DEFLATED)


def _build_missing_desc_zip(zf: zipfile.ZipFile) -> None:
    zf.writestr("part0/00000.png", _create_test_png8())


def _build_crlf_desc_zip(zf: zipfile.ZipFile) -> None:
    zf.writestr("desc.txt", b"960 2142 60\r\np 0 0 part0\r\n")
    zf.writestr("part0/00000.png", _create_test_png8())


def _build_unaligned_width_zip(zf: zipfile.ZipFile) -> None:
    zf.writestr("desc.txt", b"965 2142 60\np 0 0 part0\n")
    zf.writestr("part0/00000.png", _create_test_png8())


def _build_invalid_path_zip(zf: zipfile.ZipFile) -> None:
    zf.writestr("/desc.txt", b"960 2142 60\np 0 0 part0\n")
    zf.writestr("part 0/00000.png", _create_test_png8())


def _build_missing_part_folder_zip(zf: zipfile.ZipFile) -> None:
    zf.writestr("desc.txt", b"960 2142 60\np 0 0 part1\n")
    zf.writestr("part0/00000.png", _create_test_png8())


def _build_empty_part_folder_zip(zf: zipfile.ZipFile) -> None:
    zf.writestr("desc.txt", b"960 2142 60\np 0 0 part0\n")
    zf.writestr("part0/readme.txt", b"no images")


def _build_corrupt_png_magic_zip(zf: zipfile.ZipFile) -> None:
    zf.writestr("desc.txt", b"960 2142 60\np 0 0 part0\n")
    zf.writestr("part0/00000.png", b"INVALID_HEADER_BYTES")


def _build_extraneous_file_zip(zf: zipfile.ZipFile) -> None:
    zf.writestr("desc.txt", b"960 2142 60\np 0 0 part0\n")
    zf.writestr("part0/00000.png", _create_test_png8())
    zf.writestr("part0/notes.txt", b"extraneous content")


@pytest.mark.parametrize(
    "builder_fn",
    [
        _build_deflate_zip,
        _build_missing_desc_zip,
        _build_crlf_desc_zip,
        _build_unaligned_width_zip,
        _build_invalid_path_zip,
        _build_missing_part_folder_zip,
        _build_empty_part_folder_zip,
        _build_corrupt_png_magic_zip,
        _build_extraneous_file_zip,
    ],
)
def test_verify_invalid_archive_flags(
    tmp_path: Path, builder_fn: Callable[[zipfile.ZipFile], None]
) -> None:
    zip_path = tmp_path / "test_case.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        builder_fn(zf)

    verifier = BootAnimationVerifier()
    report = verifier.verify(zip_path)

    assert report.is_valid is False
    assert len(report.errors) > 0


def test_verify_trim_specifications(tmp_path: Path) -> None:
    zip_path = tmp_path / "trim_tests.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("desc.txt", b"960 2142 60\np 0 0 part0\n")
        zf.writestr("part0/00000.png", _create_test_png8((64, 64)))
        zf.writestr("part0/trim.txt", b"500x500+600+0\n")

    verifier = BootAnimationVerifier()
    report = verifier.verify(zip_path)

    assert report.is_valid is False
    assert len(report.errors) > 0


def test_verify_warnings_for_non_fatal_issues(tmp_path: Path) -> None:
    zip_path = tmp_path / "warnings.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("desc.txt", b"960 2142 60\np 0 0 part0\n")
        zf.writestr("part0/00001.png", _create_test_rgb_png())
        zf.writestr("part0/00000.png", _create_test_rgb_png())

    verifier = BootAnimationVerifier()
    report = verifier.verify(zip_path)

    assert report.is_valid is True
    assert len(report.warnings) > 0


def test_verify_vram_budget_enforcement(tmp_path: Path) -> None:
    heavy_zip = tmp_path / "heavy_loop.zip"
    with zipfile.ZipFile(heavy_zip, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("desc.txt", b"960 2142 60\np 0 0 part0\n")
        png_sample = _create_test_png8()
        for i in range(30):
            zf.writestr(f"part0/{i:05d}.png", png_sample)

    verifier = BootAnimationVerifier()

    report_heavy = verifier.verify(heavy_zip, max_vram_mb=150.0)
    assert report_heavy.is_valid is False
    assert len(report_heavy.errors) > 0

    report_override = verifier.verify(heavy_zip, max_vram_mb=300.0)
    assert report_override.is_valid is True
    assert report_override.errors == ()

    trimmed_zip = tmp_path / "trimmed_loop.zip"
    with zipfile.ZipFile(trimmed_zip, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("desc.txt", b"960 2142 60\np 0 0 part0\n")
        small_sample = _create_test_png8((64, 64))
        trims = []
        for i in range(30):
            zf.writestr(f"part0/{i:05d}.png", small_sample)
            trims.append("64x64+0+0\n")
        zf.writestr("part0/trim.txt", "".join(trims).encode("utf-8"))

    report_trimmed = verifier.verify(trimmed_zip, max_vram_mb=150.0)

    assert report_trimmed.is_valid is True
    assert report_trimmed.errors == ()
