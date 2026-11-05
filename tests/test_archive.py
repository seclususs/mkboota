import zipfile
from pathlib import Path

import pytest

from mkboota.archive import ZipStorePacker
from mkboota.exceptions import ArchiveError
from mkboota.inspector import inspect_archive


def test_pack_valid_directory(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "desc.txt").write_bytes(b"960 2142 60\np 0 0 part0\n")

    part0 = build_dir / "part0"
    part0.mkdir()
    (part0 / "00000.png").write_bytes(b"\x89PNG\r\n\x1a\nfake_png_data")
    (part0 / "trim.txt").write_text("100x100+0+0\n", encoding="utf-8")

    (build_dir / ".DS_Store").write_bytes(b"garbage")
    (part0 / ".hidden_file").write_bytes(b"garbage")
    (build_dir / "temp.tmp").write_bytes(b"garbage")

    output_zip = tmp_path / "output" / "bootanimation.zip"
    packer = ZipStorePacker()

    result = packer.pack(build_dir, output_zip)

    assert result == output_zip
    assert output_zip.exists()
    assert not output_zip.with_suffix(".tmp").exists()

    with zipfile.ZipFile(output_zip, "r") as zf:
        names = zf.namelist()
        assert names[0] == "desc.txt"
        assert "part0/00000.png" in names
        assert "part0/trim.txt" in names

        assert ".DS_Store" not in names
        assert "part0/.hidden_file" not in names
        assert "temp.tmp" not in names

        for info in zf.infolist():
            assert info.compress_type == zipfile.ZIP_STORED


def test_pack_missing_desc_txt(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    part0 = build_dir / "part0"
    part0.mkdir()
    (part0 / "00000.png").write_bytes(b"fake_png")

    packer = ZipStorePacker()
    output_zip = tmp_path / "out.zip"

    with pytest.raises(ArchiveError):
        packer.pack(build_dir, output_zip)


def test_pack_alphanumeric_numerical_sorting_for_unpadded_frames(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "desc.txt").write_bytes(b"960 2142 60\np 0 0 part0\n")

    part0 = build_dir / "part0"
    part0.mkdir()
    for name in ["10.png", "1.png", "2.png", "20.png", "trim.txt"]:
        (part0 / name).write_bytes(
            b"\x89PNG\r\n\x1a\nfake" if name.endswith(".png") else b"64x64+0+0\n"
        )

    output_zip = tmp_path / "bootanimation.zip"
    packer = ZipStorePacker()

    packer.pack(build_dir, output_zip)

    with zipfile.ZipFile(output_zip, "r") as zf:
        namelist = zf.namelist()
        assert namelist[0] == "desc.txt"
        part0_entries = [n for n in namelist if n.startswith("part0/")]
        expected = ["part0/1.png", "part0/2.png", "part0/10.png", "part0/20.png", "part0/trim.txt"]
        assert part0_entries == expected


def test_inspect_archive_metadata(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "desc.txt").write_bytes(b"960 2142 60\np 0 0 part0\n")

    part0 = build_dir / "part0"
    part0.mkdir()
    (part0 / "00000.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (part0 / "trim.txt").write_text("100x100+0+0\n", encoding="utf-8")

    zip_path = tmp_path / "inspected.zip"
    ZipStorePacker().pack(build_dir, zip_path)

    info = inspect_archive(zip_path)

    assert info.is_valid is True
    assert info.fps == 60
    assert info.canvas.width == 960
    assert info.canvas.height == 2142
    assert len(info.parts) == 1
    assert info.parts[0].is_looping is True
    assert info.parts[0].frame_count == 1
