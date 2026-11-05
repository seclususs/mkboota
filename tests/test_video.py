import shutil
import subprocess
from pathlib import Path

import pytest

from mkboota.models import CanvasResolution, CropRect
from mkboota.video import (
    FFmpegExtractor,
    FFprobeInspector,
    calculate_fit_scale,
)


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg or ffprobe not available in PATH",
)
def test_probe_real_video(tmp_path: Path) -> None:
    video_path = tmp_path / "synthetic_test.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=1:size=320x240:rate=30",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(video_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    inspector = FFprobeInspector()
    meta = inspector.probe(video_path)

    assert meta.width == 320
    assert meta.height == 240
    assert abs(meta.fps - 30.0) < 0.1
    assert abs(meta.duration - 1.0) < 0.2
    assert meta.frame_count >= 28


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg not available in PATH",
)
def test_extract_frames_real_synthetic_video(tmp_path: Path) -> None:
    video_path = tmp_path / "sample_1s.mp4"
    out_dir = tmp_path / "frames"

    gen_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=1:size=320x240:rate=30",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(video_path),
    ]
    subprocess.run(gen_cmd, check=True, capture_output=True)

    extractor = FFmpegExtractor()
    crop = CropRect(x=0, y=0, width=160, height=120)

    frames = extractor.extract_frames(video_path, out_dir, target_fps=30, crop=crop)

    assert len(frames) >= 28
    assert frames[0].name == "frame_00001.png"
    assert all(f.exists() for f in frames)


@pytest.mark.parametrize(
    ("in_w", "in_h", "expected_w", "expected_h"),
    [
        (540, 960, 540, 960),
        (3840, 2160, 960, 540),
        (539, 959, 538, 958),
        (2160, 3840, 960, 1706),
        (1, 1, 2, 2),
    ],
)
def test_calculate_fit_scale(in_w: int, in_h: int, expected_w: int, expected_h: int) -> None:
    canvas = CanvasResolution(width=960, height=2142)

    w, h = calculate_fit_scale(in_w, in_h, canvas)

    assert (w, h) == (expected_w, expected_h)
    assert w % 2 == 0
    assert h % 2 == 0
    assert w <= canvas.width
    assert h <= canvas.height
