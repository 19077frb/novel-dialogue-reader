import re
from pathlib import Path

from ndr.characters.colors import BASE_COLORS, DARK_COLORS, allocate_colors, color_css


def test_colors_do_not_repeat_at_eight_or_sixteen_and_are_deterministic():
    values = [color_css(index) for index in range(1000)]
    assert len(set(values)) == 1000
    assert values[0] != values[8] != values[16]
    assert color_css(99) == color_css(99)
    assert color_css(99, dark=True) != color_css(99)


def test_allocation_reserves_manual_choices_and_isolates_identities():
    entries = [("early", None), ("target", 0), ("other", None), ("target", 0)]
    assert allocate_colors(entries) == {"early": 1, "target": 0, "other": 2}
    assert allocate_colors([("a", 9), ("b", 9)]) == {"a": 9, "b": 0}


def test_reader_and_export_base_tokens_and_generation_match():
    root = Path(__file__).resolve().parents[3]
    css = (root / "frontend/src/styles/global.css").read_text(encoding="utf-8")
    tokens = re.findall(r"--ndr-speaker-(\d+):\s*(#[0-9a-f]+)", css)
    assert tuple(value for _, value in tokens[:16]) == BASE_COLORS
    assert tuple(value for _, value in tokens[16:]) == DARK_COLORS
    palette = (root / "frontend/src/styles/palette.ts").read_text(encoding="utf-8")
    assert "137.508" in palette and "* 1000" in palette and "38%" in css and "72%" in css
