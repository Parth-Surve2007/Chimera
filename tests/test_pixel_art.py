import pytest
from chimera.pixel_art import validate_pixel_art, render_pixel_art, verify_pixel_art, plan_pixel_art
from chimera.local_model import LocalModelConfig, LocalModelProvider

def spec():
    rows = ["ab" * 16 for _ in range(32)]
    return validate_pixel_art({"version": 1, "width": 32, "height": 32, "palette": {"a": "white", "b": "red"}, "rows": rows})

def test_pixel_art_validates_grid_and_renders_nearest_neighbor(tmp_path):
    s = spec(); out = tmp_path / "pixel.bmp"
    render_pixel_art(out, s, 2)
    assert verify_pixel_art(out, s, 2)["valid"]
    assert out.read_bytes()[:2] == b"BM"

def test_pixel_art_rejects_wrong_rows_and_symbols():
    with pytest.raises(ValueError): validate_pixel_art({"version": 1, "width": 32, "height": 32, "palette": {"a": "white"}, "rows": ["a" * 32]})
    rows = ["a" * 31 + "x"] + ["a" * 32 for _ in range(31)]
    with pytest.raises(ValueError): validate_pixel_art({"version": 1, "width": 32, "height": 32, "palette": {"a": "white"}, "rows": rows})

def test_observed_sequence_completion_is_rejected_without_dict_conversion():
    model = type("M", (), {"status": lambda self: type("S", (), {"state": "ready"})()})()
    model.structured_with_metrics = lambda prompt: (["not", "an", "object"], {})
    with pytest.raises(ValueError, match="JSON object"):
        plan_pixel_art("apple", model)

def test_canonical_pixel_art_contract_has_no_ambiguous_symbol_map():
    rows = ["." * 32 for _ in range(32)]
    with pytest.raises(ValueError, match="version"):
        validate_pixel_art({"width": 32, "height": 32, "palette": {".": "white"}, "rows": rows})
