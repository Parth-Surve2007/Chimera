from chimera import ui


def test_dark_emperor_theme_uses_full_black_surfaces():
    assert ui.BG == "#000000"
    assert ui.SURFACE == "#0B0B0B"
    assert ui.ELEVATED == "#141414"
    assert ui.BORDER == "#2B2B2B"
    assert ui.TEXT == "#F3F3F3"
    assert ui.FOCUS == "#E5B35A"
    assert ui.ACCENT == "#F17857"
