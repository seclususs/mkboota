from __future__ import annotations

from pathlib import Path

from mkboota.exceptions import DescFormatError
from mkboota.models import PartConfig, is_valid_hex_color, normalize_hex_color


class DescSanitizer:
    def parse_and_normalize(
        self, desc_path: Path, fixes: list[str]
    ) -> tuple[int, int, int, int, list[PartConfig]]:
        content = desc_path.read_bytes()

        # AOSP BootAnimation parser fails on carriage returns (CR)
        if b"\r\n" in content or b"\r" in content:
            fixes.append("Line endings normalized to LF")

        if content.startswith(b"\xef\xbb\xbf"):
            fixes.append("Removed UTF-8 BOM from desc.txt")

        if b"\t" in content:
            fixes.append("Normalized tabs in desc.txt to spaces")

        text = content.decode("utf-8-sig", errors="replace")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            raise DescFormatError("desc.txt is empty")

        canvas_w, canvas_h, fps, orig_w = self._parse_header_line(lines[0], fixes)
        parts = self._parse_part_lines(lines[1:], fixes)
        return canvas_w, canvas_h, fps, orig_w, parts

    def _parse_header_line(self, header: str, fixes: list[str]) -> tuple[int, int, int, int]:
        tokens = header.split()
        if len(tokens) < 3:
            raise DescFormatError(f"Invalid desc.txt header: {header}")

        try:
            orig_w = int(tokens[0])
            h = int(tokens[1])
            fps = int(tokens[2])
        except ValueError as exc:
            raise DescFormatError(f"Invalid integer in desc.txt header: {header}") from exc

        if orig_w <= 0 or h <= 0 or fps <= 0:
            raise DescFormatError(
                f"Dimensions and fps must be positive in desc.txt header: {header}"
            )

        w = orig_w
        # Hardware texture stride alignment: width must be divisible by 16
        if w % 16 != 0:
            w = ((w + 15) // 16) * 16
            fixes.append(f"Canvas width adjusted from {orig_w} to {w}")

        return w, h, fps, orig_w

    def _parse_part_lines(
        self, lines: list[str], fixes: list[str] | None = None
    ) -> list[PartConfig]:
        parts: list[PartConfig] = []

        for line in lines:
            parsed = self._parse_single_part_line(line, fixes)
            if parsed is None:
                continue

            parts.append(parsed)
            self._record_fade_part_fix(line, parsed.folder_name, fixes)

        return parts

    @staticmethod
    def _record_fade_part_fix(line: str, folder_name: str, fixes: list[str] | None) -> None:
        tokens = line.split()
        if fixes is not None and tokens and tokens[0] == "f":
            fixes.append(f"Normalized 'f' (fade) partition '{folder_name}'")

    def _parse_single_part_line(
        self, line: str, fixes: list[str] | None = None
    ) -> PartConfig | None:
        tokens = line.split()
        if len(tokens) < 4:
            return None

        p_type = tokens[0]
        if p_type not in ("c", "p", "f"):
            return None

        try:
            count = int(tokens[1])
            pause = int(tokens[2])
        except ValueError:
            return None

        folder = tokens[3]
        # Legacy non-standard 'f' partition normalized to completion 'c' or play 'p'
        type_code = ("c" if count == 1 else "p") if p_type == "f" else p_type
        bg, clock = self._extract_part_attributes(tokens, folder, fixes)
        return PartConfig(
            type_code=type_code,
            count=count,
            pause=pause,
            folder_name=folder,
            bg_color=bg,
            clock_pos=clock,
        )

    @staticmethod
    def _parse_clock_token(token: str) -> int:
        try:
            return int(token)
        except ValueError:
            return -1

    @classmethod
    def _extract_part_attributes(
        cls, tokens: list[str], folder: str = "", fixes: list[str] | None = None
    ) -> tuple[str, int]:
        bg = "#000000"
        clock = -1
        for idx in range(4, len(tokens)):
            token = tokens[idx]
            if not token.startswith("#"):
                continue

            bg = cls._normalize_hex_token(token, folder, fixes)
            if idx + 1 < len(tokens):
                clock = cls._parse_clock_token(tokens[idx + 1])

            break
        return bg, clock

    @staticmethod
    def _normalize_hex_token(token: str, folder: str, fixes: list[str] | None) -> str:
        if is_valid_hex_color(token):
            normalized = normalize_hex_color(token)
            if normalized != token and fixes is not None:
                folder_suffix = f" in part '{folder}'" if folder else ""
                fixes.append(f"Normalized hex color '{token}' to {normalized}{folder_suffix}")
            return normalized

        if fixes is not None:
            folder_suffix = f" in part '{folder}'" if folder else ""
            fixes.append(f"Normalized invalid hex color '{token}' to #000000{folder_suffix}")
        return "#000000"

    def write_final_desc(
        self,
        desc_path: Path,
        canvas_w: int,
        canvas_h: int,
        fps: int,
        parts: list[PartConfig],
    ) -> None:
        lines = [f"{canvas_w} {canvas_h} {fps}"]
        for p in parts:
            lines.append(p.to_desc_line())

        content = "\n".join(lines) + "\n"
        desc_path.write_bytes(content.encode("utf-8"))


__all__ = ["DescSanitizer"]
