import json
def test_flet_ui_uses_warm_design_tokens_and_persistent_service():
    from chimera import ui
    assert ui.BG == "#050505" and ui.ACCENT == "#777777" and not any("blue" in value.lower() for value in (ui.BG, ui.SURFACE, ui.ACCENT))
