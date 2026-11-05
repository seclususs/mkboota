import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from mkboota.archive import ZipPackerProtocol, ZipStorePacker
from mkboota.compiler import AnimationCompiler, DurationAdvisor
from mkboota.editor import CropperUIProtocol
from mkboota.exceptions import CompilationError
from mkboota.imaging import (
    BoundingBoxTrimmer,
    FrameQuantizerProtocol,
    FrameTrimmerProtocol,
    PaletteQuantizer,
)
from mkboota.models import (
    CanvasResolution,
    FrameTrim,
    LoopMode,
    SegmentSpec,
    VerificationReport,
    VideoMetadata,
)
from mkboota.verifier import BootAnimationVerifier, VerifierProtocol
from mkboota.video import (
    VideoExtractorProtocol,
    VideoInspectorProtocol,
)


def _create_mock_compiler() -> tuple[AnimationCompiler, dict[str, MagicMock]]:
    mocks = {
        "inspector": MagicMock(spec=VideoInspectorProtocol),
        "extractor": MagicMock(spec=VideoExtractorProtocol),
        "interactive_cropper": MagicMock(spec=CropperUIProtocol),
        "headless_cropper": MagicMock(spec=CropperUIProtocol),
        "trimmer": MagicMock(spec=FrameTrimmerProtocol),
        "quantizer": MagicMock(spec=FrameQuantizerProtocol),
        "packer": MagicMock(spec=ZipPackerProtocol),
        "verifier": MagicMock(spec=VerifierProtocol),
        "advisor": MagicMock(spec=DurationAdvisor),
    }
    mocks["trimmer"].calculate_uniform_trim.return_value = (
        None,
        FrameTrim(width=960, height=2142, offset_x=0, offset_y=0),
    )

    compiler = AnimationCompiler(
        video_inspector=mocks["inspector"],
        video_extractor=mocks["extractor"],
        interactive_cropper=mocks["interactive_cropper"],
        headless_cropper=mocks["headless_cropper"],
        frame_trimmer=mocks["trimmer"],
        frame_quantizer=mocks["quantizer"],
        zip_packer=mocks["packer"],
        verifier=mocks["verifier"],
        duration_advisor=mocks["advisor"],
    )
    return compiler, mocks


def _make_dummy_frame(path: Path, width: int = 960, height: int = 2142) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), color=(200, 100, 50))
    img.save(path, format="PNG")
    return path


def _create_synthetic_frames(directory: Path, count: int) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    sample_frame = directory / "seed.png"
    img = Image.new("RGB", (10, 10), color=(128, 128, 128))
    img.save(sample_frame, format="PNG")
    raw_bytes = sample_frame.read_bytes()

    paths: list[Path] = []
    for idx in range(count):
        frame_path = directory / f"src_{idx:05d}.png"
        frame_path.write_bytes(raw_bytes)
        paths.append(frame_path)
    return paths


def test_compile_loop_mode_loop(tmp_path: Path) -> None:
    compiler, mocks = _create_mock_compiler()

    video_path = tmp_path / "input.mp4"
    video_path.touch()
    out_zip = tmp_path / "output.zip"

    canvas = CanvasResolution(960, 2142)
    mocks["inspector"].probe.return_value = VideoMetadata(
        duration=2.0,
        width=960,
        height=2142,
        fps=60.0,
        frame_count=2,
    )

    frame0 = _make_dummy_frame(tmp_path / "raw" / "frame_00000.png")
    frame1 = _make_dummy_frame(tmp_path / "raw" / "frame_00001.png")
    mocks["extractor"].extract_frames.return_value = [frame0, frame1]

    mocks["trimmer"].calculate_trim.return_value = FrameTrim(
        width=960, height=2142, offset_x=0, offset_y=0
    )

    captured_desc = ""
    captured_trim_part0 = ""
    captured_part0_files: list[str] = []

    def mock_pack(build_dir: Path, target_zip: Path) -> Path:
        nonlocal captured_desc, captured_trim_part0, captured_part0_files
        desc_file = build_dir / "desc.txt"
        assert desc_file.exists()
        captured_desc = desc_file.read_text(encoding="utf-8")

        part0 = build_dir / "part0"
        assert part0.is_dir()
        trim_file = part0 / "trim.txt"
        assert trim_file.exists()
        captured_trim_part0 = trim_file.read_text(encoding="utf-8")
        captured_part0_files = [p.name for p in part0.iterdir()]

        target_zip.touch()
        return target_zip

    mocks["packer"].pack.side_effect = mock_pack
    mocks["verifier"].verify.return_value = VerificationReport(is_valid=True)

    result = compiler.compile_from_video(
        video_path=video_path,
        output_zip=out_zip,
        canvas=canvas,
        loop_mode=LoopMode.LOOP,
        fps=60,
        interactive_crop=False,
    )

    assert result == out_zip
    assert captured_desc == "960 2142 60\np 0 0 part0 #000000\n"
    assert captured_trim_part0 == "960x2142+0+0\n960x2142+0+0\n"
    assert "trim.txt" in captured_part0_files
    mocks["verifier"].verify.assert_called_once_with(out_zip)


def test_compile_loop_mode_hold_last(tmp_path: Path) -> None:
    compiler, mocks = _create_mock_compiler()

    video_path = tmp_path / "input.mp4"
    video_path.touch()
    out_zip = tmp_path / "output.zip"

    canvas = CanvasResolution(960, 2142)
    mocks["inspector"].probe.return_value = VideoMetadata(
        duration=3.0,
        width=960,
        height=2142,
        fps=60.0,
        frame_count=3,
    )

    frames = [_make_dummy_frame(tmp_path / "raw" / f"frame_{i:05d}.png") for i in range(3)]
    mocks["extractor"].extract_frames.return_value = frames
    mocks["trimmer"].calculate_trim.return_value = FrameTrim(
        width=960, height=2142, offset_x=0, offset_y=0
    )

    captured_desc = ""
    captured_trim_part0 = ""
    captured_trim_part1 = ""
    part1_exists = False

    def mock_pack(build_dir: Path, target_zip: Path) -> Path:
        nonlocal captured_desc, captured_trim_part0, captured_trim_part1, part1_exists
        captured_desc = (build_dir / "desc.txt").read_text(encoding="utf-8")
        captured_trim_part0 = (build_dir / "part0" / "trim.txt").read_text(encoding="utf-8")
        part1_dir = build_dir / "part1"
        part1_exists = part1_dir.is_dir()
        if part1_exists:
            captured_trim_part1 = (part1_dir / "trim.txt").read_text(encoding="utf-8")
        target_zip.touch()
        return target_zip

    mocks["packer"].pack.side_effect = mock_pack
    mocks["verifier"].verify.return_value = VerificationReport(is_valid=True)

    result = compiler.compile_from_video(
        video_path=video_path,
        output_zip=out_zip,
        canvas=canvas,
        loop_mode=LoopMode.HOLD_LAST,
        fps=60,
        interactive_crop=False,
    )

    assert result == out_zip
    expected_desc = "960 2142 60\nc 1 0 part0 #000000\np 0 0 part1 #000000\n"
    assert captured_desc == expected_desc
    assert part1_exists is True
    assert len(captured_trim_part0.strip().splitlines()) == 2
    assert len(captured_trim_part1.strip().splitlines()) == 1


def test_compile_from_segments_fail_fast_when_looping_vram_exceeds_budget(
    tmp_path: Path,
) -> None:
    compiler, mocks = _create_mock_compiler()
    video_path = tmp_path / "long_loop.mp4"
    video_path.touch()
    out_zip = tmp_path / "out.zip"

    mocks["inspector"].probe.return_value = VideoMetadata(
        duration=1.0,
        width=960,
        height=2142,
        fps=30.0,
        frame_count=30,
    )

    segments = [SegmentSpec(start_time=0.0, end_time=1.0, count=0)]
    canvas = CanvasResolution(960, 2142)

    with pytest.raises(CompilationError):
        compiler.compile_from_segments(
            video_path=video_path,
            output_zip=out_zip,
            segments=segments,
            canvas=canvas,
            fps=30,
            max_vram_mb=150.0,
        )

    mocks["extractor"].extract_frames.assert_not_called()


def test_compile_from_segments_auto_repair_vram(tmp_path: Path) -> None:
    compiler, mocks = _create_mock_compiler()
    video_path = tmp_path / "long_loop.mp4"
    video_path.touch()
    out_zip = tmp_path / "bootanimation.zip"
    canvas = CanvasResolution(1088, 1920)

    mocks["inspector"].probe.return_value = VideoMetadata(
        duration=10.0,
        width=1088,
        height=1920,
        fps=10.0,
        frame_count=100,
    )
    raw_frames = _create_synthetic_frames(tmp_path / "raw_frames", 100)
    mocks["extractor"].extract_frames.return_value = raw_frames
    mocks["quantizer"].quantize_png8.side_effect = lambda _img, out_path, **_kw: out_path.touch()

    captured_frames: list[str] = []

    def capture_pack(build_dir: Path, destination: Path) -> Path:
        nonlocal captured_frames
        captured_frames = sorted(p.name for p in (build_dir / "part0").glob("*.png"))
        return destination

    mocks["packer"].pack.side_effect = capture_pack
    mocks["verifier"].verify.return_value = VerificationReport(is_valid=True)

    segments = [SegmentSpec(start_time=0.0, end_time=10.0, count=0, folder_name="part0")]

    result = compiler.compile_from_segments(
        video_path=video_path,
        output_zip=out_zip,
        segments=segments,
        canvas=canvas,
        fps=10,
        max_vram_mb=150.0,
        auto_repair_vram=True,
    )

    assert result == out_zip
    assert len(captured_frames) == 17
    assert captured_frames[0] == "00000.png"
    assert captured_frames[-1] == "00016.png"
    mocks["verifier"].verify.assert_called_once_with(out_zip, max_vram_mb=150.0)


def test_compiler_generates_matching_trim_txt_and_png_sizes(tmp_path: Path) -> None:
    mock_inspector = MagicMock(spec=VideoInspectorProtocol)
    mock_extractor = MagicMock(spec=VideoExtractorProtocol)

    compiler = AnimationCompiler(
        video_inspector=mock_inspector,
        video_extractor=mock_extractor,
        frame_trimmer=BoundingBoxTrimmer(),
        frame_quantizer=PaletteQuantizer(),
        zip_packer=ZipStorePacker(),
        verifier=BootAnimationVerifier(),
    )

    video_path = tmp_path / "trim_test.mp4"
    video_path.touch()
    out_zip = tmp_path / "bootanimation.zip"

    canvas = CanvasResolution(960, 2142)
    mock_inspector.probe.return_value = VideoMetadata(
        duration=1.0,
        width=540,
        height=960,
        fps=30.0,
        frame_count=2,
    )

    f0 = tmp_path / "f0.png"
    f1 = tmp_path / "f1.png"
    img0 = Image.new("RGB", (540, 960), color=(0, 0, 0))
    img0.paste(Image.new("RGB", (200, 100), (255, 255, 255)), (170, 380))
    img0.save(f0, format="PNG")

    img1 = Image.new("RGB", (540, 960), color=(0, 0, 0))
    img1.paste(Image.new("RGB", (100, 100), (255, 255, 255)), (170, 480))
    img1.save(f1, format="PNG")

    mock_extractor.extract_frames.return_value = [f0, f1]

    compiler.compile_from_video(
        video_path=video_path,
        output_zip=out_zip,
        canvas=canvas,
        loop_mode=LoopMode.LOOP,
        fps=30,
        interactive_crop=False,
    )

    assert out_zip.exists()

    report = BootAnimationVerifier().verify(out_zip)
    assert report.is_valid, f"Verification failed: {report.errors}"
    assert len(report.errors) == 0

    with zipfile.ZipFile(out_zip, "r") as zf:
        trim_lines = zf.read("part0/trim.txt").decode("utf-8").strip().splitlines()
        assert len(trim_lines) == 2
        assert trim_lines[0] == trim_lines[1]
        assert trim_lines[0].startswith("200x200+")
        f0_bytes = zf.read("part0/00000.png")
        f1_bytes = zf.read("part0/00001.png")

    for raw in (f0_bytes, f1_bytes):
        with Image.open(io.BytesIO(raw)) as img:
            assert img.size == (200, 200)
