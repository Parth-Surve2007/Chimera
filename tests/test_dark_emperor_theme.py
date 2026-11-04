from chimera import ui


def test_dark_emperor_theme_uses_full_black_surfaces():
    assert ui.BG == "#000000"
    assert ui.SURFACE == "#090C12"
    assert ui.ELEVATED == "#111722"
    assert ui.BORDER == "#252D3B"
    assert ui.TEXT == "#F2F5FA"
    assert ui.FOCUS == "#C6D1E2"
    assert ui.ACCENT == "#6F9FFF"
