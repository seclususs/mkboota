import io
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from mkboota.compiler import AnimationCompiler
from mkboota.models import LoopMode
from mkboota.verifier import BootAnimationVerifier


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="FFmpeg is required for end-to-end integration tests",
)


@pytest.fixture(scope="session")
def synthetic_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    temp_dir = tmp_path_factory.mktemp("synthetic_media")
    video_path = temp_dir / "test_synth_640x360.mp4"
    gen_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=2:size=640x360:rate=30",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(video_path),
    ]
    subprocess.run(gen_cmd, check=True, capture_output=True)
    return video_path


def test_end_to_end_video_compilation(synthetic_video: Path, tmp_path: Path) -> None:
    output_zip = tmp_path / "bootanimation.zip"
    compiler = AnimationCompiler()

    compiled_result = compiler.compile_from_video(
        synthetic_video,
        output_zip,
        interactive_crop=False,
        fps=60,
    )

    assert output_zip.exists()
    assert compiled_result == output_zip

    with zipfile.ZipFile(output_zip, "r") as zf:
        namelist = zf.namelist()

        assert "desc.txt" in namelist

        for info in zf.infolist():
            assert info.compress_type == zipfile.ZIP_STORED, (
                f"Entry {info.filename} uses compression {info.compress_type}, "
                "expected ZIP_STORED (0)"
            )

        part0_frames = sorted(
            name for name in namelist if name.startswith("part0/") and name.lower().endswith(".png")
        )
        assert len(part0_frames) > 0, "No PNG frames found in part0/"
        frame_payloads = [(name, zf.read(name)) for name in part0_frames]

        assert "part0/trim.txt" in namelist
        trim_bytes = zf.read("part0/trim.txt")
        trim_lines = [
            line.strip() for line in trim_bytes.decode("utf-8").splitlines() if line.strip()
        ]
        assert len(trim_lines) == len(part0_frames), (
            f"trim.txt lines ({len(trim_lines)}) do not match frame count ({len(part0_frames)})"
        )

    for frame_name, frame_bytes in frame_payloads:
        with Image.open(io.BytesIO(frame_bytes)) as img:
            assert img.mode == "P", f"Frame {frame_name} is mode '{img.mode}', expected 'P' (PNG-8)"
            palette = img.getpalette()
            assert palette is not None
            assert len(palette) // 3 <= 256

    verifier = BootAnimationVerifier()
    report = verifier.verify(output_zip)

    assert report.is_valid is True, f"Verification failed: {report.errors}"
    assert len(report.errors) == 0


def test_end_to_end_hold_last_mode(synthetic_video: Path, tmp_path: Path) -> None:
    output_zip = tmp_path / "bootanimation_hold.zip"
    compiler = AnimationCompiler()

    compiler.compile_from_video(
        synthetic_video,
        output_zip,
        interactive_crop=False,
        loop_mode=LoopMode.HOLD_LAST,
        fps=60,
    )

    assert output_zip.exists()

    with zipfile.ZipFile(output_zip, "r") as zf:
        namelist = zf.namelist()

        assert any(name.startswith("part0/") for name in namelist)
        assert any(name.startswith("part1/") for name in namelist)

        assert "part1/00000.png" in namelist
        assert "part1/trim.txt" in namelist

        part1_trim_bytes = zf.read("part1/trim.txt")
        part1_trim_lines = [
            line.strip() for line in part1_trim_bytes.decode("utf-8").splitlines() if line.strip()
        ]
        assert len(part1_trim_lines) == 1

        for info in zf.infolist():
            assert info.compress_type == zipfile.ZIP_STORED

    verifier = BootAnimationVerifier()
    report = verifier.verify(output_zip)

    assert report.is_valid is True, f"Hold-last verification failed: {report.errors}"
    assert len(report.errors) == 0
