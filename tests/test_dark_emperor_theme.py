import re
from pathlib import Path

from chimera import ui


def test_dark_emperor_theme_uses_full_black_graphite_surfaces():
    assert ui.BG == "#000000"
    assert ui.SURFACE == "#0A0A0A"
    assert ui.ELEVATED == "#151515"
    assert ui.BORDER == "#303030"
    assert ui.TEXT == "#F2F2F2"
    assert ui.FOCUS == "#E0E0E0"
    assert ui.ACCENT == "#C5C5C5"
    assert ui.THEME_SEED == "#BDBDBD"
    assert ui.PANEL_SURFACE == "#101010"


def test_every_ui_hex_color_is_neutral_grey_or_black():
    source = (Path(__file__).parents[1] / "chimera" / "ui.py").read_text(encoding="utf-8")
    colors = re.findall(r"#[0-9A-Fa-f]{6}\b", source)
    assert colors
    for color in colors:
        red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
        assert red == green == blue, f"Non-monochrome UI color remains: {color}"
