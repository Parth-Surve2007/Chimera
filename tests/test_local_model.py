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

def test_readiness_requests_only_missing_proposal_prerequisites():
    from chimera.local_model import assess_readiness
    from chimera.contracts import TaskSpec
    assessment = assess_readiness(TaskSpec("make a tool"))
    assert assessment.status == "needs_clarification"
    assert {question.question_id for question in assessment.questions} == {"acceptance", "deliverable"}
    ready = assess_readiness(TaskSpec("make a tool", deliverables=["tool"], acceptance_criteria=["tool works"]))
    assert ready.status == "ready" and not ready.questions

def test_configuration_limits_and_no_cloud_fallback():
    with pytest.raises(ValueError): config_from_env({"CHIMERA_MODEL_CONTEXT":"0"})
    assert LocalModelProvider(LocalModelConfig(None, None)).status().state == "missing"

def test_streaming_process_can_be_cancelled_without_waiting_for_exit():
    import sys, threading, time
    from chimera.local_model import _run_streaming
    cancelled = threading.Event()
    command = [sys.executable, "-c", "import time; print('partial', flush=True); time.sleep(30)"]
    timer = threading.Timer(0.2, cancelled.set); timer.start()
    started = time.monotonic()
    result = _run_streaming(command, 10, cancelled)
    timer.cancel()
    assert result.cancelled is True
    assert time.monotonic() - started < 4
    assert "partial" in result.stdout


def test_server_only_configuration_is_a_valid_runtime(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    server = tmp_path / "llama-server.exe"
    server.write_bytes(b"fixture")
    from chimera.local_model import LocalModelConfig
    status = check_status(LocalModelConfig(model, None, server_path=str(server)))
    assert status.state == "ready"
    assert status.cli_path == str(server)


def test_local_agent_protocol_verification_uses_structured_decision(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    cli = tmp_path / "llama-cli.exe"
    cli.write_bytes(b"fixture")
    provider = LocalModelProvider(
        LocalModelConfig(model, str(cli)),
        lambda prompt, config: '{"type":"message","content":"Local agent protocol is working."}',
    )
    result = provider.verify_agent_protocol()
    assert result["state"] == "verified"
    assert result["response_preview"] == "Local agent protocol is working."
    assert result["duration_ms"] >= 0


def test_local_provider_adapter_exposes_agent_protocol_verification():
    from types import SimpleNamespace
    from chimera.providers.adapters.local_adapter import LocalModelAdapter

    expected = {"state": "verified", "response_preview": "active conversation model"}
    adapter = object.__new__(LocalModelAdapter)
    adapter.local_provider = SimpleNamespace(verify_agent_protocol=lambda: expected)
    assert adapter.verify_agent_protocol() == expected
