from __future__ import annotations

from collections.abc import Sequence
import logging

from mkboota.models import LoopMode, SegmentSpec

logger = logging.getLogger(__name__)


def parse_time_value(val: str) -> float:
    cleaned = val.strip().rstrip("sS")
    try:
        return float(cleaned)
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp '{val}': numeric seconds required") from exc


def parse_segment_spec(part_str: str) -> SegmentSpec:
    tokens = part_str.split(":")
    if len(tokens) < 4 or len(tokens) > 7:
        raise ValueError(
            f"Invalid segment format: '{part_str}' (expected '<start>-<end>[s]:<type>:<count>:<pause>[:folder][:bgcolor][:clock]')"
        )

    time_range = tokens[0].split("-")
    if len(time_range) != 2:
        raise ValueError(f"Invalid time range '{tokens[0]}' (expected '<start>-<end>[s]')")

    start_time = parse_time_value(time_range[0])
    end_time = parse_time_value(time_range[1])
    if start_time < 0.0:
        raise ValueError(f"start_time must be non-negative, got {start_time}")

    if end_time <= start_time:
        raise ValueError(f"end_time ({end_time}) must exceed start_time ({start_time})")

    type_code = tokens[1].strip().lower()
    if type_code not in ("c", "p"):
        raise ValueError(f"type_code must be 'c' or 'p', got {type_code!r}")

    try:
        count = int(tokens[2])
        pause = int(tokens[3])
    except ValueError as exc:
        raise ValueError("Count and pause must be integers") from exc

    if count < 0:
        raise ValueError(f"Count must be non-negative, got {count}")

    if pause < 0:
        raise ValueError(f"Pause must be non-negative, got {pause}")

    folder_name = tokens[4].strip() if len(tokens) >= 5 and tokens[4].strip() else None
    bg_color = tokens[5].strip() if len(tokens) >= 6 and tokens[5].strip() else "#000000"
    clock_pos = int(tokens[6]) if len(tokens) >= 7 and tokens[6].strip() else -1

    return SegmentSpec(
        start_time=start_time,
        end_time=end_time,
        type_code=type_code,
        count=count,
        pause=pause,
        folder_name=folder_name,
        bg_color=bg_color,
        clock_pos=clock_pos,
    )


def parse_segment_specs(parts_list: Sequence[str]) -> tuple[SegmentSpec, ...]:
    if not parts_list:
        raise ValueError("parts_list cannot be empty")
    return tuple(parse_segment_spec(part) for part in parts_list)


def resolve_canonical_segments(
    video_duration: float,
    segments: Sequence[SegmentSpec] | None = None,
    loop: bool = False,
    loop_start: float | None = None,
    loop_mode: str = "infinite",
    fps: int | None = None,
) -> tuple[SegmentSpec, ...]:
    if video_duration <= 0.0:
        raise ValueError(f"video_duration must be positive, got {video_duration}")

    if segments is not None:
        return resolve_explicit_segments(video_duration, segments)

    return resolve_loop_flags(
        video_duration,
        loop=loop,
        loop_start=loop_start,
        loop_mode=loop_mode,
        fps=fps,
    )


def resolve_explicit_segments(
    video_duration: float,
    segments: Sequence[SegmentSpec],
) -> tuple[SegmentSpec, ...]:
    if not segments:
        raise ValueError("Segments sequence cannot be empty")

    resolved: list[SegmentSpec] = []

    for idx, seg in enumerate(segments):
        if seg.start_time < 0.0:
            raise ValueError(f"Segment {idx} start_time must be non-negative, got {seg.start_time}")

        if seg.start_time >= video_duration:
            raise ValueError(
                f"Segment {idx} start_time ({seg.start_time}) exceeds video duration ({video_duration})"
            )

        resolved_end = video_duration if seg.end_time is None else seg.end_time
        if resolved_end <= seg.start_time:
            raise ValueError(
                f"Segment {idx} end_time ({resolved_end}) must exceed start_time ({seg.start_time})"
            )

        if resolved_end > video_duration:
            raise ValueError(
                f"Segment {idx} end_time ({resolved_end}) exceeds video duration ({video_duration})"
            )

        folder_name = seg.folder_name if seg.folder_name is not None else f"part{idx}"
        resolved.append(
            SegmentSpec(
                start_time=seg.start_time,
                end_time=resolved_end,
                type_code=seg.type_code,
                count=seg.count,
                pause=seg.pause,
                folder_name=folder_name,
                bg_color=seg.bg_color,
                clock_pos=seg.clock_pos,
            )
        )

    for idx in range(len(resolved) - 1):
        curr_end = resolved[idx].end_time
        next_start = resolved[idx + 1].start_time

        if curr_end is not None and curr_end > next_start:
            raise ValueError(
                f"Overlapping segments: segment {idx} ends at {curr_end}s, segment {idx + 1} starts at {next_start}s"
            )

        if curr_end is not None and curr_end < next_start:
            logger.warning(
                "Timeline gap between segments: segment %d ends at %.2fs, segment %d starts at %.2fs",
                idx,
                curr_end,
                idx + 1,
                next_start,
            )

    return tuple(resolved)


def resolve_loop_flags(
    video_duration: float,
    loop: bool = False,
    loop_start: float | None = None,
    loop_mode: str = "infinite",
    fps: int | None = None,
) -> tuple[SegmentSpec, ...]:
    if loop_start is not None and loop_start > 0.0:
        if loop_start >= video_duration:
            raise ValueError(
                f"loop_start ({loop_start}s) must be less than duration ({video_duration:.2f}s)"
            )

        part0 = SegmentSpec(
            start_time=0.0,
            end_time=loop_start,
            type_code="c",
            count=1,
            pause=0,
            folder_name="part0",
        )
        part1 = SegmentSpec(
            start_time=loop_start,
            end_time=video_duration,
            type_code="p",
            count=0,
            pause=0,
            folder_name="part1",
        )

        return (part0, part1)

    mode_str = str(loop_mode).lower()
    if mode_str in ("hold-last", "hold_last", str(LoopMode.HOLD_LAST)):
        effective_fps = fps or 30
        frame_time = 1.0 / effective_fps
        split_time = video_duration - frame_time

        if split_time > 0.0:
            part0 = SegmentSpec(
                start_time=0.0,
                end_time=split_time,
                type_code="c",
                count=1,
                pause=0,
                folder_name="part0",
            )
            part1 = SegmentSpec(
                start_time=split_time,
                end_time=video_duration,
                type_code="p",
                count=0,
                pause=0,
                folder_name="part1",
            )
            return (part0, part1)

    if mode_str in ("once",) and not loop:
        return (
            SegmentSpec(
                start_time=0.0,
                end_time=video_duration,
                type_code="c",
                count=1,
                pause=0,
                folder_name="part0",
            ),
        )

    return (
        SegmentSpec(
            start_time=0.0,
            end_time=video_duration,
            type_code="p",
            count=0,
            pause=0,
            folder_name="part0",
        ),
    )


parse_parts_arg = parse_segment_specs

__all__ = [
    "parse_parts_arg",
    "parse_segment_spec",
    "parse_segment_specs",
    "parse_time_value",
    "resolve_canonical_segments",
    "resolve_explicit_segments",
    "resolve_loop_flags",
]
