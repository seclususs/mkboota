from __future__ import annotations

from typing import TYPE_CHECKING

from mkboota.exceptions import DescFormatError, InvalidCanvasError

if TYPE_CHECKING:
    from mkboota.models import CanvasResolution, PartConfig


class DescBuilder:
    @classmethod
    def build(cls, canvas: CanvasResolution, fps: int, parts: list[PartConfig]) -> str:
        if fps <= 0:
            raise DescFormatError(f"Framerate must be positive, got {fps}")

        if not parts:
            raise DescFormatError("At least one PartConfig required")

        part_lines = [part.to_desc_line() for part in parts]
        lines = [f"{canvas.width} {canvas.height} {fps}", *part_lines]
        # AOSP BootAnimation.cpp expects Unix LF line endings with a trailing newline
        return "\n".join(lines) + "\n"


class DescParser:
    @classmethod
    def _parse_header(cls, header_line: str, auto_align: bool) -> tuple[CanvasResolution, int]:
        from mkboota.models import CanvasResolution

        tokens = header_line.split()
        if len(tokens) < 3:
            raise DescFormatError(f"Invalid desc.txt header: '{header_line}'")
        try:
            width = int(tokens[0])
            height = int(tokens[1])
            fps = int(tokens[2])
        except ValueError as exc:
            raise DescFormatError(f"Invalid integer in desc.txt header: '{header_line}'") from exc

        if width <= 0 or height <= 0 or fps <= 0:
            raise DescFormatError(f"Non-positive dimensions or fps in header: '{header_line}'")
        try:
            canvas = CanvasResolution.from_dimensions(
                width=width, height=height, auto_align=auto_align
            )
        except InvalidCanvasError as exc:
            raise DescFormatError(str(exc)) from exc

        return canvas, fps

    @classmethod
    def _parse_part_line(cls, line: str) -> PartConfig:
        from mkboota.models import PartConfig

        tokens = line.split()
        if len(tokens) < 4:
            raise DescFormatError(f"Invalid part definition: '{line}'")

        type_code = tokens[0]
        if type_code not in ("c", "p"):
            raise DescFormatError(
                f"Part type_code must be 'c' or 'p', got {type_code!r} in '{line}'"
            )

        try:
            count = int(tokens[1])
            pause = int(tokens[2])
        except ValueError as exc:
            raise DescFormatError(f"Invalid integer in part definition: '{line}'") from exc

        if count < 0 or pause < 0:
            raise DescFormatError(f"Count and pause must be non-negative in '{line}'")

        folder = tokens[3]
        bg = "#000000"
        clock = -1

        if len(tokens) >= 5:
            bg = tokens[4]

        if len(tokens) >= 6:
            try:
                clock = int(tokens[5])
            except ValueError as exc:
                raise DescFormatError(f"Invalid clock position in '{line}'") from exc

        if len(tokens) > 6:
            raise DescFormatError(f"Unexpected extra tokens: '{line}'")

        return PartConfig(
            type_code=type_code,
            count=count,
            pause=pause,
            folder_name=folder,
            bg_color=bg,
            clock_pos=clock,
        )

    @classmethod
    def parse(
        cls, desc_text: str, auto_align: bool = True
    ) -> tuple[CanvasResolution, int, tuple[PartConfig, ...]]:
        # Strip UTF-8 BOM if present to avoid parser failure on legacy archives
        if desc_text.startswith("\ufeff"):
            desc_text = desc_text[len("\ufeff") :]
        elif desc_text.startswith("\xef\xbb\xbf"):
            desc_text = desc_text[len("\xef\xbb\xbf") :]

        lines = [
            line.strip()
            for line in desc_text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if not lines:
            raise DescFormatError("desc.txt is empty")

        canvas, fps = cls._parse_header(lines[0], auto_align=auto_align)
        parts = tuple(cls._parse_part_line(line) for line in lines[1:])
        return canvas, fps, parts


__all__ = ["DescBuilder", "DescParser"]
