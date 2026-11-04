import json
import subprocess
from pathlib import Path

from mkboota.exceptions import (
    VideoExtractionError,
    VideoNotFoundError,
    VideoProbeError,
)
from mkboota.models import CanvasResolution, CropRect, VideoMetadata
from mkboota.protocols import VideoExtractorProtocol, VideoInspectorProtocol


def calculate_fit_scale(
    source_width: int,
    source_height: int,
    canvas: CanvasResolution,
) -> tuple[int, int]:
    """Calculate canvas-bounded dimensions with even integers for hardware texture compatibility"""
    if source_width <= 0 or source_height <= 0:
        raise ValueError(f"Source dimensions must be positive: {source_width}x{source_height}")

    if source_width <= canvas.width and source_height <= canvas.height:
        w, h = source_width, source_height
    else:
        scale_factor = min(canvas.width / source_width, canvas.height / source_height)
        w = round(source_width * scale_factor)
        h = round(source_height * scale_factor)

    w_even = max(2, (w // 2) * 2)
    h_even = max(2, (h // 2) * 2)
    return (w_even, h_even)


class FFprobeInspector(VideoInspectorProtocol):
    def __init__(self, ffprobe_bin: str = "ffprobe") -> None:
        self.ffprobe_bin = ffprobe_bin

    def _execute_ffprobe(self, video_path: Path) -> str:
        cmd = [
            self.ffprobe_bin,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(video_path),
        ]
        try:
            proc = subprocess.run(cmd, timeout=30, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise VideoProbeError(f"ffprobe executable not found: '{self.ffprobe_bin}'") from exc
        except subprocess.TimeoutExpired as exc:
            raise VideoProbeError(f"ffprobe timed out: {video_path}") from exc
        except Exception as exc:
            raise VideoProbeError(f"ffprobe error: {exc}") from exc

        if proc.returncode != 0:
            raise VideoProbeError(
                f"ffprobe failed (exit code {proc.returncode}): {proc.stderr.strip()}"
            )
        return proc.stdout

    def _parse_dimensions(
        self, video_stream: dict[str, object], video_path: Path
    ) -> tuple[int, int]:
        try:
            width_raw = video_stream.get("width")
            height_raw = video_stream.get("height")
            if width_raw is None or height_raw is None:
                raise ValueError("Missing width or height in video stream")

            width = int(str(width_raw))
            height = int(str(height_raw))
            if width <= 0 or height <= 0:
                raise ValueError(f"Dimensions must be positive: {width}x{height}")

            return (width, height)
        except (TypeError, ValueError, KeyError) as exc:
            raise VideoProbeError(f"Invalid video dimensions in {video_path}: {exc}") from exc

    @staticmethod
    def _parse_fps_value(fps_raw: str) -> float:
        if "/" not in fps_raw:
            return float(fps_raw)

        num, den = fps_raw.split("/", 1)
        den_f = float(den)
        if den_f == 0.0:
            raise ZeroDivisionError("Frame rate denominator is zero")

        return float(num) / den_f

    def _parse_fps(self, video_stream: dict[str, object], video_path: Path) -> float:
        fps_raw = str(video_stream.get("r_frame_rate") or video_stream.get("avg_frame_rate") or "")
        if not fps_raw or fps_raw == "0/0":
            raise VideoProbeError(f"Invalid frame rate in {video_path}: '{fps_raw}'")

        try:
            fps = self._parse_fps_value(fps_raw)
            if fps <= 0.0:
                raise ValueError(f"Frame rate must be positive: {fps}")

            return fps
        except (TypeError, ValueError, KeyError, ZeroDivisionError) as exc:
            raise VideoProbeError(f"Invalid frame rate in {video_path}: {exc}") from exc

    def _parse_duration(
        self,
        video_stream: dict[str, object],
        format_info: dict[str, object],
        video_path: Path,
    ) -> float:
        try:
            duration_raw = video_stream.get("duration")
            if not duration_raw or duration_raw == "N/A":
                duration_raw = format_info.get("duration")

            if not duration_raw or duration_raw == "N/A":
                raise ValueError("Missing stream and format duration")

            duration = float(str(duration_raw))
            if duration <= 0.0:
                raise ValueError(f"Duration must be positive: {duration}")

            return duration
        except (TypeError, ValueError, KeyError) as exc:
            raise VideoProbeError(f"Invalid duration in {video_path}: {exc}") from exc

    def _parse_frame_count(
        self,
        video_stream: dict[str, object],
        duration: float,
        fps: float,
        video_path: Path,
    ) -> int:
        nb_frames_raw = video_stream.get("nb_frames")
        if not nb_frames_raw or nb_frames_raw == "N/A":
            return int(duration * fps)

        try:
            return int(str(nb_frames_raw))
        except (
            TypeError,
            ValueError,
        ):
            return int(duration * fps)
        except Exception as exc:
            raise VideoProbeError(f"Invalid frame count in {video_path}: {exc}") from exc

    def probe(self, video_path: Path) -> VideoMetadata:
        if not video_path.exists():
            raise VideoNotFoundError(f"Video file not found: {video_path}")

        stdout = self._execute_ffprobe(video_path)
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise VideoProbeError(f"Invalid ffprobe JSON output: {exc}") from exc

        streams = data.get("streams", [])
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        if video_stream is None:
            raise VideoProbeError(f"No video stream found in {video_path}")

        width, height = self._parse_dimensions(video_stream, video_path)
        fps = self._parse_fps(video_stream, video_path)
        format_info = data.get("format", {})
        duration = self._parse_duration(video_stream, format_info, video_path)
        frame_count = self._parse_frame_count(video_stream, duration, fps, video_path)

        return VideoMetadata(
            duration=duration,
            width=width,
            height=height,
            fps=fps,
            frame_count=frame_count,
        )


class FFmpegExtractor(VideoExtractorProtocol):
    def __init__(self, ffmpeg_bin: str = "ffmpeg") -> None:
        self.ffmpeg_bin = ffmpeg_bin

    def build_filter_string(
        self,
        target_fps: int = 60,
        crop: CropRect | None = None,
        scale: CanvasResolution | tuple[int, int] | None = None,
    ) -> str:
        filters: list[str] = []

        if crop is not None:
            filters.append(f"crop={crop.width}:{crop.height}:{crop.x}:{crop.y}")

        if scale is not None:
            scale_w = scale.width if isinstance(scale, CanvasResolution) else scale[0]
            scale_h = scale.height if isinstance(scale, CanvasResolution) else scale[1]
            filters.append(f"scale={scale_w}:{scale_h}")

        filters.append(f"fps={target_fps}")
        return ",".join(filters)

    def _build_ffmpeg_cmd(
        self,
        video_path: Path,
        output_dir: Path,
        vf_filter: str,
        start_time: float,
        duration: float | None,
        max_duration: float | None,
    ) -> list[str]:
        output_pattern = str(output_dir / "frame_%05d.png")
        cmd = [self.ffmpeg_bin, "-y"]

        if start_time > 0.0:
            cmd.extend(["-ss", f"{start_time:.3f}"])

        cmd.extend(["-i", str(video_path), "-vf", vf_filter])

        if duration is not None:
            cmd.extend(["-t", f"{duration:.3f}"])
        elif max_duration is not None:
            cmd.extend(["-t", str(max_duration)])

        # Disable audio stream (-an) and disable frame dropping/duplication (-vsync 0)
        cmd.extend(["-an", "-vsync", "0", output_pattern])
        return cmd

    @staticmethod
    def _decode_process_stderr(stderr: bytes | str | None) -> str:
        if not stderr:
            return ""

        if isinstance(stderr, bytes):
            return stderr.decode(errors="replace").strip()

        return str(stderr).strip()

    def _execute_ffmpeg(self, cmd: list[str], video_path: Path) -> None:
        try:
            subprocess.run(cmd, timeout=120, check=True, capture_output=True)
        except FileNotFoundError as exc:
            raise VideoExtractionError(f"ffmpeg executable not found: '{self.ffmpeg_bin}'") from exc
        except subprocess.TimeoutExpired as exc:
            raise VideoExtractionError(f"FFmpeg extraction timed out: {video_path}") from exc
        except subprocess.CalledProcessError as exc:
            err_msg = self._decode_process_stderr(exc.stderr)
            raise VideoExtractionError(
                f"FFmpeg failed (exit code {exc.returncode}): {err_msg}"
            ) from exc
        except Exception as exc:
            raise VideoExtractionError(f"FFmpeg error: {exc}") from exc

    def extract_frames(
        self,
        video_path: Path,
        output_dir: Path,
        target_fps: int = 60,
        crop: CropRect | None = None,
        scale: CanvasResolution | tuple[int, int] | None = None,
        max_duration: float | None = None,
        start_time: float = 0.0,
        duration: float | None = None,
    ) -> list[Path]:
        if not video_path.exists():
            raise VideoNotFoundError(f"Video file not found: {video_path}")

        if target_fps <= 0:
            raise ValueError(f"target_fps must be positive: {target_fps}")

        if start_time < 0.0:
            raise ValueError(f"start_time must be non-negative: {start_time}")

        if max_duration is not None and max_duration <= 0:
            raise ValueError(f"max_duration must be positive: {max_duration}")

        if duration is not None and duration <= 0:
            raise ValueError(f"duration must be positive: {duration}")

        output_dir.mkdir(parents=True, exist_ok=True)
        vf_filter = self.build_filter_string(target_fps=target_fps, crop=crop, scale=scale)
        cmd = self._build_ffmpeg_cmd(
            video_path=video_path,
            output_dir=output_dir,
            vf_filter=vf_filter,
            start_time=start_time,
            duration=duration,
            max_duration=max_duration,
        )
        self._execute_ffmpeg(cmd, video_path)

        frames = sorted(output_dir.glob("frame_*.png"))
        if not frames:
            raise VideoExtractionError(f"No frames generated in {output_dir} from {video_path}")

        return frames


__all__ = [
    "FFmpegExtractor",
    "FFprobeInspector",
    "VideoExtractorProtocol",
    "VideoInspectorProtocol",
    "calculate_fit_scale",
]
