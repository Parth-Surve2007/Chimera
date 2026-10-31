import json
from pathlib import Path

import pytest

from chimera.local_model import LocalModelConfig, LocalModelProvider, _json_completion
from chimera.scene import plan_scene


def test_json_completion_discards_llama_banner_and_trailing_telemetry():
    raw = "Loading model...\n> prompt\n{" + '"width": 32, "height": 32, "layers": []}' + "\n[ Generation: 70.0 t/s ]"
    value, completion = _json_completion(raw)
    assert value["width"] == 32
    assert completion.startswith("{") and "Loading model" not in completion


def test_truncated_completion_is_rejected():
    with pytest.raises(ValueError, match="complete JSON"):
        _json_completion("Loading model...\n{\"width\": 128, \"layers\": [")


def test_scene_prompt_uses_requested_dimensions_and_model_provenance(tmp_path):
    model = tmp_path / "model.gguf"; model.write_bytes(b"x")
    cli = tmp_path / "llama"; cli.write_bytes(b"x")
    captured = []
    def runner(prompt, config):
        captured.append(prompt)
        return json.dumps({"width": 128, "height": 128, "background": "black", "layers": []})
    provider = LocalModelProvider(LocalModelConfig(model, str(cli)), runner)
    scene, _ = plan_scene("Create pixel art 128x128", provider)
    assert (scene.width, scene.height, scene.source) == (128, 128, "local_model")
    assert "width 128 and height 128" in captured[0]


def test_structured_runner_rejects_malformed_output(tmp_path):
    model = tmp_path / "model.gguf"; model.write_bytes(b"x")
    cli = tmp_path / "llama"; cli.write_bytes(b"x")
    provider = LocalModelProvider(LocalModelConfig(model, str(cli)), lambda *_: "Loading model... [{ never closes")
    with pytest.raises(ValueError, match="invalid|complete JSON"):
        provider.structured_with_metrics("return JSON")
