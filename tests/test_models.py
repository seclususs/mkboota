from mkboota.advisor import DurationAdvisor, DurationCategory
from mkboota.models import (
    CanvasResolution,
    DescBuilder,
    DescParser,
    FrameTrim,
    LoopMode,
    PartConfig,
    calculate_frame_vram_bytes,
    calculate_looping_vram_bytes,
    calculate_partition_vram_bytes,
)


def test_desc_parser_valid_standard() -> None:
    desc = (
        "# AOSP bootanimation desc header\n"
        "1088 1920 60\n"
        "# Part 0: completed intro\n"
        "c 1 0 part0 #000000 -1\n"
        "# Part 1: infinite loop with clock\n"
        "p 0 0 part1 #123456 1\n"
    )

    canvas, fps, parts = DescParser.parse(desc)

    assert canvas == CanvasResolution(1088, 1920)
    assert fps == 60
    assert len(parts) == 2
    assert parts[0] == PartConfig(
        type_code="c",
        count=1,
        pause=0,
        folder_name="part0",
        bg_color="#000000",
        clock_pos=-1,
    )
    assert parts[1] == PartConfig(
        type_code="p",
        count=0,
        pause=0,
        folder_name="part1",
        bg_color="#123456",
        clock_pos=1,
    )


def test_desc_parser_auto_align() -> None:
    desc = "1080 1920 60\np 0 0 part0\n"

    canvas, fps, parts = DescParser.parse(desc, auto_align=True)

    assert canvas.width == 1088
    assert canvas.height == 1920
    assert fps == 60
    assert len(parts) == 1
    assert parts[0].folder_name == "part0"
    assert parts[0].bg_color == "#000000"
    assert parts[0].clock_pos == -1


def test_desc_builder_generates_valid_lf_with_trailing_newline() -> None:
    canvas = CanvasResolution()
    parts = [
        PartConfig(type_code="c", count=1, pause=90, folder_name="part0"),
        PartConfig(type_code="p", count=0, pause=0, folder_name="part1"),
    ]

    content = DescBuilder.build(canvas=canvas, fps=60, parts=parts)

    assert "\r\n" not in content
    assert content.endswith("\n")

    lines = content.strip().split("\n")
    assert len(lines) == 3
    assert lines[0] == "960 2142 60"
    assert lines[1] == "c 1 90 part0 #000000"
    assert lines[2] == "p 0 0 part1 #000000"


def test_vram_calculations() -> None:
    assert calculate_frame_vram_bytes(960, 2142) == 960 * 2142 * 4
    assert calculate_looping_vram_bytes(10, 960, 2142) == 10 * 960 * 2142 * 4
    assert calculate_looping_vram_bytes(0, 960, 2142) == 0

    canvas = CanvasResolution(960, 2142)
    assert calculate_partition_vram_bytes(10, canvas) == 10 * 960 * 2142 * 4

    trims = [FrameTrim(100, 100, 0, 0), FrameTrim(200, 150, 10, 10)]
    assert calculate_partition_vram_bytes(2, canvas, trims) == (100 * 100 * 4) + (200 * 150 * 4)


def test_duration_advisor() -> None:
    advisor = DurationAdvisor()

    short_advice = advisor.evaluate(3.0)
    assert short_advice.category == DurationCategory.SHORT
    assert short_advice.recommended_loop_mode == LoopMode.LOOP

    standard_advice = advisor.evaluate(10.0)
    assert standard_advice.category == DurationCategory.STANDARD
    assert standard_advice.recommended_loop_mode == LoopMode.LOOP

    long_advice = advisor.evaluate(20.0)
    assert long_advice.category == DurationCategory.LONG
    assert long_advice.recommended_loop_mode == LoopMode.HOLD_LAST
