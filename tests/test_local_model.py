from pathlib import Path
import pytest
from chimera.local_model import LocalModelConfig, LocalModelProvider, check_status, config_from_env

def test_missing_local_model_is_honest():
    result = LocalModelProvider(LocalModelConfig(None, None)).plan("make something")
    assert result.status == "missing" and result.task is None

def test_fake_local_runner_validates_generic_task(tmp_path):
    model = tmp_path / "model.gguf"; model.write_bytes(b"fixture")
    cli = tmp_path / "llama.exe"; cli.write_bytes(b"fixture")
    provider = LocalModelProvider(LocalModelConfig(model, str(cli)), lambda prompt, config: '{"outcome":"organize files","permissions":[],"acceptance_criteria":["verified"]}')
    assert provider.status().state == "ready"; result = provider.plan("organize files")
    assert result.status == "ready" and result.task.outcome == "organize files"

def test_malformed_and_unsupported_runner_output(tmp_path):
    model = tmp_path / "m.gguf"; model.write_bytes(b"x"); cli = tmp_path / "l"; cli.write_bytes(b"x")
    p = LocalModelProvider(LocalModelConfig(model, str(cli)), lambda *_: "not json")
    assert p.plan("x").status == "malformed"
    p = LocalModelProvider(LocalModelConfig(model, str(cli)), lambda *_: '{"outcome":""}')
    assert p.plan("x").status == "malformed"

def test_configuration_limits_and_no_cloud_fallback():
    with pytest.raises(ValueError): config_from_env({"CHIMERA_MODEL_CONTEXT":"0"})
    assert LocalModelProvider(LocalModelConfig(None, None)).status().state == "missing"
