from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import re
import shutil

from PIL import Image

from mkboota.imaging import BoundingBoxTrimmer, PaletteQuantizer
from mkboota.models import CanvasResolution, CropRect, FrameTrim

ProgressCallback = Callable[[str, int, int], None]


class FrameSanitizer:
    def __init__(
        self,
        quantizer: PaletteQuantizer | None = None,
        trimmer: BoundingBoxTrimmer | None = None,
    ) -> None:
        self._quantizer = quantizer or PaletteQuantizer()
        self._trimmer = trimmer or BoundingBoxTrimmer()

    def process_single_frame(
        self,
        src_path: Path,
        dest_path: Path,
        folder_name: str,
        fixes: list[str],
        crop_box: tuple[int, int, int, int] | None = None,
        bg_color: str = "#000000",
    ) -> tuple[int, int]:
        with Image.open(src_path) as img:
            w, h = img.size

            # Even dimensions are required for OpenGL texture alignment
            is_pristine_png8 = img.mode == "P" and w % 2 == 0 and h % 2 == 0 and crop_box is None
            if is_pristine_png8:
                shutil.copy2(src_path, dest_path)
                return 0, 0

            padded, pad_w, pad_h = self.pad_image_if_odd(img, bg_color)
            if pad_w > 0 or pad_h > 0:
                fixes.append(
                    f"Part '{folder_name}' frame {src_path.name} padded to {padded.size[0]}x{padded.size[1]}"
                )

        final_frame = padded.crop(crop_box) if crop_box is not None else padded
        self._quantizer.quantize_png8(final_frame, dest_path)
        if final_frame is not padded:
            final_frame.close()

        padded.close()
        return pad_w, pad_h

    def process_part_with_existing_trim(
        self,
        temp_dir: Path,
        folder_name: str,
        image_paths: list[Path],
        trim_file: Path,
        canvas_w: int,
        orig_w: int,
        fixes: list[str],
        progress_callback: ProgressCallback | None = None,
        offset: int = 0,
        total_frames: int = 0,
        needs_reindex: bool = False,
        bg_color: str = "#000000",
    ) -> None:
        trim_lines = [
            line.strip()
            for line in trim_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        shift_x = max(0, (canvas_w - orig_w) // 2)
        new_trim_lines: list[str] = []

        for idx, src_path in enumerate(image_paths):
            dest_name = f"{idx:05d}.png" if needs_reindex else src_path.name
            dest_path = temp_dir / dest_name

            pad_w, pad_h = self.process_single_frame(
                src_path, dest_path, folder_name, fixes, bg_color=bg_color
            )
            updated_trim = self._adjust_trim_entry(trim_lines[idx], pad_w, pad_h, shift_x)
            new_trim_lines.append(updated_trim)
            if progress_callback:
                progress_callback("sanitize", offset + idx + 1, total_frames)

        (temp_dir / "trim.txt").write_text("\n".join(new_trim_lines) + "\n", encoding="utf-8")

    def process_part_without_existing_trim(
        self,
        temp_dir: Path,
        folder_name: str,
        image_paths: list[Path],
        canvas_w: int,
        canvas_h: int,
        fixes: list[str],
        progress_callback: ProgressCallback | None = None,
        offset: int = 0,
        total_frames: int = 0,
        needs_reindex: bool = False,
        bg_color: str = "#000000",
    ) -> None:
        sample_paths = self._sample_frame_paths(image_paths)
        crop_rect, frame_trim = self.calculate_sample_trim(
            sample_paths, canvas_w, canvas_h, bg_color=bg_color
        )
        trim_line = self.determine_trim_policy(crop_rect, frame_trim, folder_name, fixes)

        self._stream_and_quantize_frames(
            temp_dir,
            folder_name,
            image_paths,
            crop_rect,
            fixes,
            progress_callback,
            offset,
            total_frames,
            needs_reindex=needs_reindex,
            bg_color=bg_color,
        )

        if trim_line is not None:
            content = "\n".join([trim_line] * len(image_paths)) + "\n"
            (temp_dir / "trim.txt").write_text(content, encoding="utf-8")

    def calculate_sample_trim(
        self,
        sample_paths: list[Path],
        canvas_w: int,
        canvas_h: int,
        bg_color: str = "#000000",
    ) -> tuple[CropRect | None, FrameTrim]:
        sample_images: list[Image.Image] = []
        for path in sample_paths:
            with Image.open(path) as img:
                padded, _, _ = self.pad_image_if_odd(img, bg_color)
                sample_images.append(padded)

        canvas = CanvasResolution(width=canvas_w, height=canvas_h)
        try:
            return self._trimmer.calculate_uniform_trim(sample_images, canvas)
        finally:
            for sample_img in sample_images:
                sample_img.close()

    def determine_trim_policy(
        self,
        crop_rect: CropRect | None,
        frame_trim: FrameTrim,
        folder_name: str,
        fixes: list[str],
    ) -> str | None:
        if crop_rect is not None:
            fixes.append(
                f"Generated uniform trim for part '{folder_name}' ({frame_trim.to_trim_entry()})"
            )
            return frame_trim.to_trim_entry()

        if frame_trim.offset_x > 0 or frame_trim.offset_y > 0:
            fixes.append(
                f"Generated centering trim for part '{folder_name}' ({frame_trim.to_trim_entry()})"
            )
            return frame_trim.to_trim_entry()

        return None

    def _adjust_trim_entry(self, line: str, pad_w: int, pad_h: int, shift_x: int) -> str:
        m = re.match(r"^(\d+)x(\d+)\+(\d+)\+(\d+)$", line)
        if not m:
            return line

        w = int(m.group(1)) + pad_w
        h = int(m.group(2)) + pad_h
        x = int(m.group(3)) + shift_x
        y = int(m.group(4))
        return f"{w}x{h}+{x}+{y}"

    def _sample_frame_paths(self, paths: list[Path]) -> list[Path]:
        if len(paths) <= 20:
            return paths

        step = len(paths) / 20
        indices = [min(int(i * step), len(paths) - 1) for i in range(20)]
        indices.append(len(paths) - 1)
        seen: set[int] = set()
        sampled: list[Path] = []

        for idx in indices:
            if idx not in seen:
                seen.add(idx)
                sampled.append(paths[idx])

        return sampled

    def _stream_and_quantize_frames(
        self,
        temp_dir: Path,
        folder_name: str,
        image_paths: list[Path],
        crop_rect: CropRect | None,
        fixes: list[str],
        progress_callback: ProgressCallback | None = None,
        offset: int = 0,
        total_frames: int = 0,
        needs_reindex: bool = False,
        bg_color: str = "#000000",
    ) -> None:
        crop_box = (
            (
                crop_rect.x,
                crop_rect.y,
                crop_rect.x + crop_rect.width,
                crop_rect.y + crop_rect.height,
            )
            if crop_rect is not None
            else None
        )

        for idx, src_path in enumerate(image_paths):
            dest_name = f"{idx:05d}.png" if needs_reindex else src_path.name
            dest_path = temp_dir / dest_name
            self.process_single_frame(
                src_path,
                dest_path,
                folder_name,
                fixes,
                crop_box=crop_box,
                bg_color=bg_color,
            )
            if progress_callback:
                progress_callback("sanitize", offset + idx + 1, total_frames)

    def pad_image_if_odd(
        self, img: Image.Image, bg_color: str = "#000000"
    ) -> tuple[Image.Image, int, int]:
        w, h = img.size

        pad_w = 1 if w % 2 != 0 else 0
        pad_h = 1 if h % 2 != 0 else 0
        if pad_w == 0 and pad_h == 0:
            return img.copy(), 0, 0

        new_w = w + pad_w
        new_h = h + pad_h
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            padded = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))
        else:
            bg_rgb = self._hex_to_rgb(bg_color)
            padded = Image.new("RGB", (new_w, new_h), bg_rgb)

        padded.paste(img, (0, 0))
        return padded, pad_w, pad_h

    @staticmethod
    def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
        clean = hex_str.lstrip("#")
        if len(clean) == 6:
            try:
                r = int(clean[0:2], 16)
                g = int(clean[2:4], 16)
                b = int(clean[4:6], 16)
                return r, g, b
            except ValueError:
                return 0, 0, 0
        return 0, 0, 0


__all__ = ["FrameSanitizer"]
