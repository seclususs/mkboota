from __future__ import annotations

import io
import zipfile
from pathlib import Path

from PIL import Image

from mkboota import (
    AnimationRepairer,
    RepairReport,
)


def _create_test_image(
    size: tuple[int, int] = (100, 100),
    color: tuple[int, int, int] = (255, 0, 0),
) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _collect_zip_entries(
    parts: dict[str, list[tuple[str, tuple[int, int]]]],
    trim_files: dict[str, str] | None,
) -> list[tuple[str, bytes]]:
    entries: list[tuple[str, bytes]] = []

    if trim_files:
        for trim_name, trim_text in trim_files.items():
            entries.append((trim_name, trim_text.encode("utf-8")))

    for folder, frame_specs in parts.items():
        for fname, size in frame_specs:
            entries.append((f"{folder}/{fname}", _create_test_image(size)))

    return entries


def _create_test_zip(
    zip_path: Path,
    desc_content: str | bytes,
    parts: dict[str, list[tuple[str, tuple[int, int]]]],
    trim_files: dict[str, str] | None = None,
    compression: int = zipfile.ZIP_STORED,
    order: list[str] | None = None,
) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    desc_bytes = desc_content.encode("utf-8") if isinstance(desc_content, str) else desc_content
    entries = _collect_zip_entries(parts, trim_files)

    entry_map = dict(entries)
    ordered_entries = (
        entries
        if order is None
        else [(name, entry_map[name]) for name in order if name in entry_map]
    )

    with zipfile.ZipFile(zip_path, mode="w", compression=compression) as zf:
        zf.writestr("desc.txt", desc_bytes)
        for name, data in ordered_entries:
            zf.writestr(name, data)

    return zip_path


def _create_synthetic_defective_archive(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)

    desc_bytes = b"959 2141 30\r\np 0 0 part0\r\n"
    frame_size = (64, 64)
    frame_bytes = _create_test_image(frame_size)
    trim_lines = [f"{frame_size[0]}x{frame_size[1]}+0+0" for _ in range(10)]
    trim_bytes = ("\r\n".join(trim_lines) + "\r\n").encode("utf-8")

    entries: list[tuple[str, bytes]] = [
        ("part0/.DS_Store", b"\x00\x00\x00\x01junk"),
        ("part0/Thumbs.db", b"junkdb"),
        ("part0/trim.txt", trim_bytes),
    ]
    for i in range(10):
        entries.append((f"part0/{i:05d}.png", frame_bytes))
    entries.reverse()
    entries.append(("desc.txt", desc_bytes))

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)

    return path


def test_repairer_normalizes_crlf_and_repacks_stored(tmp_path: Path) -> None:
    bad_desc = "960 2142 60\r\np 1 0 part0 #000000 -1\r\n"
    bad_zip = tmp_path / "crlf_deflated.zip"
    _create_test_zip(
        bad_zip,
        bad_desc,
        {"part0": [("00000.png", (960, 2142))]},
        compression=zipfile.ZIP_DEFLATED,
    )

    out_zip = tmp_path / "crlf_repaired.zip"
    repairer = AnimationRepairer()

    report = repairer.repair(bad_zip, out_zip)

    assert report.is_success
    assert isinstance(report, RepairReport)
    assert any("line endings normalized" in f.lower() for f in report.fixes_applied)
    assert any("zip_stored" in f.lower() for f in report.fixes_applied)
    assert report.verification.is_valid

    with zipfile.ZipFile(out_zip, "r") as zf:
        desc_data = zf.read("desc.txt")
        assert b"\r\n" not in desc_data
        assert desc_data.endswith(b"\n")
        for entry in zf.infolist():
            assert entry.compress_type == zipfile.ZIP_STORED


def test_repairer_adjusts_canvas_width_to_multiple_of_16(tmp_path: Path) -> None:
    bad_desc = "1080 1920 60\np 1 0 part0 #000000 -1\n"
    bad_zip = tmp_path / "width1080.zip"
    _create_test_zip(
        bad_zip,
        bad_desc,
        {"part0": [("00000.png", (1080, 1920))]},
    )

    out_zip = tmp_path / "width1080_repaired.zip"
    repairer = AnimationRepairer()

    report = repairer.repair(bad_zip, out_zip)

    assert report.is_success
    assert any("1088" in f for f in report.fixes_applied)
    assert report.verification.is_valid

    with zipfile.ZipFile(out_zip, "r") as zf:
        desc_text = zf.read("desc.txt").decode("utf-8")
        assert desc_text.startswith("1088 1920 60\n")
        trim_content = zf.read("part0/trim.txt").decode("utf-8")
        assert "+4+0" in trim_content


def test_repairer_reindexes_unsorted_frames(tmp_path: Path) -> None:
    desc = "960 2142 60\np 1 0 part0 #000000 -1\n"
    bad_zip = tmp_path / "unsorted.zip"
    parts = {
        "part0": [
            ("frame_02.png", (200, 200)),
            ("frame_10.png", (200, 200)),
            ("frame_01.png", (200, 200)),
        ]
    }
    order = ["part0/frame_10.png", "part0/frame_02.png", "part0/frame_01.png"]
    _create_test_zip(bad_zip, desc, parts, order=order)

    out_zip = tmp_path / "unsorted_repaired.zip"
    repairer = AnimationRepairer()

    report = repairer.repair(bad_zip, out_zip)

    assert report.is_success
    assert any("re-index" in f.lower() or "sequential" in f.lower() for f in report.fixes_applied)
    assert report.verification.is_valid

    with zipfile.ZipFile(out_zip, "r") as zf:
        pngs = [n for n in zf.namelist() if n.endswith(".png")]
        assert pngs == ["part0/00000.png", "part0/00001.png", "part0/00002.png"]


def test_repairer_preserves_hazardous_looping_vram_without_splitting(tmp_path: Path) -> None:
    desc = "1088 1920 60\np 0 0 part0 #000000 -1\n"
    bad_zip = tmp_path / "high_vram.zip"
    frames = [(f"{i:05d}.png", (1088, 1920)) for i in range(20)]
    _create_test_zip(bad_zip, desc, {"part0": frames})

    out_zip = tmp_path / "high_vram_repaired.zip"
    repairer = AnimationRepairer()

    report = repairer.repair(bad_zip, out_zip)

    assert report.is_success
    assert not any("hold-last" in f.lower() for f in report.fixes_applied)
    assert any("Decimated looping part 'part0' by 2x" in f for f in report.fixes_applied)
    assert report.verification.is_valid

    with zipfile.ZipFile(out_zip, "r") as zf:
        desc_lines = [
            line.strip()
            for line in zf.read("desc.txt").decode("utf-8").splitlines()
            if line.strip()
        ]
        assert desc_lines[1].startswith("p 0 0 part0")
        assert "part0_last" not in zf.namelist()
        png_names = [n for n in zf.namelist() if n.startswith("part0/") and n.endswith(".png")]
        assert len(png_names) == 10


def test_repairer_normalizes_type_f_fade_part(tmp_path: Path) -> None:
    desc = "960 2142 60\nf 0 0 part0 10 #000000 -1\n"
    in_zip = tmp_path / "fade_input.zip"
    _create_test_zip(in_zip, desc, {"part0": [("00000.png", (960, 2142))]})

    out_zip = tmp_path / "fade_repaired.zip"
    repairer = AnimationRepairer()

    report = repairer.repair(in_zip, out_zip)

    assert report.is_success is True
    assert report.verification.is_valid is True
    assert any("Normalized 'f' (fade) partition" in fix for fix in report.fixes_applied)
    with zipfile.ZipFile(out_zip, "r") as zf:
        repaired_desc = zf.read("desc.txt").decode("utf-8")
        lines = [line.strip() for line in repaired_desc.splitlines() if line.strip()]
        assert lines[1].startswith("p 0 0 part0")


def test_repairer_pads_odd_dimension_with_bg_color(tmp_path: Path) -> None:
    desc = "960 2142 30\np 1 0 part0 #FF0000 -1\n"
    in_zip = tmp_path / "odd_bg.zip"
    in_zip.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (101, 101), (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw_img_bytes = buf.getvalue()

    with zipfile.ZipFile(in_zip, "w") as zf:
        zf.writestr("desc.txt", desc.encode("utf-8"))
        zf.writestr("part0/trim.txt", "101x101+0+0\n".encode("utf-8"))
        zf.writestr("part0/00000.png", raw_img_bytes)

    out_zip = tmp_path / "odd_bg_repaired.zip"
    repairer = AnimationRepairer()

    report = repairer.repair(in_zip, out_zip)

    assert report.is_success is True
    assert report.verification.is_valid is True

    with zipfile.ZipFile(out_zip, "r") as zf:
        padded_bytes = zf.read("part0/00000.png")
        padded_img = Image.open(io.BytesIO(padded_bytes)).convert("RGB")
        assert padded_img.size == (102, 102)
        assert padded_img.getpixel((101, 50)) == (255, 0, 0)


def test_repairer_preserves_audio_wav(tmp_path: Path) -> None:
    desc = "960 2142 60\np 1 0 part0 #000000 -1\n"
    in_zip = tmp_path / "with_audio.zip"
    _create_test_zip(in_zip, desc, {"part0": [("00000.png", (960, 2142))]})

    audio_bytes = b"RIFF....WAVEfmt ...."
    with zipfile.ZipFile(in_zip, "a") as zf:
        zf.writestr("part0/audio.wav", audio_bytes)

    out_zip = tmp_path / "with_audio_repaired.zip"
    repairer = AnimationRepairer()
    report = repairer.repair(in_zip, out_zip)

    assert report.is_success
    assert report.verification.is_valid
    with zipfile.ZipFile(out_zip, "r") as zf:
        assert "part0/audio.wav" in zf.namelist()
        assert zf.read("part0/audio.wav") == audio_bytes


def test_repair_synthetic_defective_archive_passes_all_verifications(
    tmp_path: Path,
) -> None:
    defective_path = _create_synthetic_defective_archive(tmp_path / "defective_input.zip")
    out_zip = tmp_path / "defective_repaired.zip"
    repairer = AnimationRepairer()

    report = repairer.repair(defective_path, out_zip)

    assert report.is_success
    assert report.verification.is_valid
    assert len(report.verification.errors) == 0
    assert len(report.verification.warnings) == 0
