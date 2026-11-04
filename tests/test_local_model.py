from pathlib import Path
import json
from types import SimpleNamespace
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



def test_explicit_server_failure_never_falls_back_to_cli(tmp_path, monkeypatch):
    from chimera.local_model import ServerUnavailable
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    cli = tmp_path / "llama-cli.exe"
    cli.write_bytes(b"fixture")
    server_exe = tmp_path / "llama-server.exe"
    server_exe.write_bytes(b"fixture")

    provider = LocalModelProvider(
        LocalModelConfig(model, str(cli), server_path=str(server_exe)),
    )

    class BrokenServer:
        def status(self):
            return {"state": "ready", "url": "http://127.0.0.1:1234", "pid": 10}

        def complete(self, *args, **kwargs):
            raise ServerUnavailable("server crashed")

        def stop(self):
            pass

    provider.server = BrokenServer()
    calls = []
    monkeypatch.setattr("chimera.local_model._run_streaming", lambda *a, **k: calls.append(a))
    with pytest.raises(RuntimeError, match="no CLI or hosted fallback was attempted"):
        provider.converse([{"role": "user", "content": "hello"}])
    assert calls == []


def test_invalid_explicit_server_does_not_select_valid_cli(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    cli = tmp_path / "llama-cli.exe"
    cli.write_bytes(b"fixture")
    missing_server = tmp_path / "missing-llama-server.exe"

    status = check_status(
        LocalModelConfig(model, str(cli), server_path=str(missing_server))
    )

    assert status.state == "missing"
    assert "no alternate runtime" in status.detail


def test_persisted_runtime_and_inference_tuning_round_trip(tmp_path, monkeypatch):
    from chimera.local_model import save_persisted_config

    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    cli = tmp_path / "llama-cli.exe"
    cli.write_bytes(b"fixture")
    server = tmp_path / "llama-server.exe"
    server.write_bytes(b"fixture")
    monkeypatch.setenv("CHIMERA_DATA_DIR", str(tmp_path))

    save_persisted_config(
        model,
        str(cli),
        server_path=str(server),
        context=4096,
        max_tokens=512,
        threads=6,
        gpu_layers=20,
    )
    loaded = config_from_env({})

    assert loaded.model_path == model.resolve()
    assert loaded.cli_path == str(cli.resolve())
    assert loaded.server_path == str(server.resolve())
    assert loaded.context == 4096
    assert loaded.max_tokens == 512
    assert loaded.threads == 6
    assert loaded.gpu_layers == 20


def test_agent_decision_envelope_is_strict_and_includes_session_state(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    cli = tmp_path / "llama-cli.exe"
    cli.write_bytes(b"fixture")
    prompts = []

    provider = LocalModelProvider(
        LocalModelConfig(model, str(cli)),
        lambda prompt, config: (
            prompts.append(prompt)
            or '{"type":"message","content":"Attach Desktop and switch to Build Mode first."}'
        ),
    )
    from types import SimpleNamespace

    decision = provider.decide(
        [SimpleNamespace(role="user", content="make a file on desktop")],
        {"version": 1, "tools": []},
        session={"mode": "plan", "workspace": None},
    )

    assert decision["type"] == "message"
    assert "SESSION: {\"mode\":\"plan\",\"workspace_attached\":false}" in prompts[0]
    assert "do not call workspace tools" in prompts[0]
    from chimera.local_model import AGENT_DECISION_SCHEMA
    assert len(AGENT_DECISION_SCHEMA["oneOf"]) == 2
    assert AGENT_DECISION_SCHEMA["oneOf"][0]["properties"]["type"]["enum"] == ["message"]
    assert AGENT_DECISION_SCHEMA["oneOf"][1]["properties"]["type"]["enum"] == ["tool_call"]


def test_local_agent_decision_rejects_unsupported_envelopes(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    cli = tmp_path / "llama-cli.exe"
    cli.write_bytes(b"fixture")
    provider = LocalModelProvider(
        LocalModelConfig(model, str(cli)),
        lambda *_: '{"type":"text","text":"This is not a Chimera decision."}',
    )

    with pytest.raises(ValueError, match="unsupported decision envelope"):
        provider.decide([], {"version": 1, "tools": []})


def test_local_agent_tool_call_requires_tool_and_arguments(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    cli = tmp_path / "llama-cli.exe"
    cli.write_bytes(b"fixture")
    provider = LocalModelProvider(
        LocalModelConfig(model, str(cli)),
        lambda *_: '{"type":"tool_call","tool":"workspace.create_artifact","arguments":{"name":"note.txt","content":"hello"}}',
    )

    decision = provider.decide(
        [],
        {
            "version": 1,
            "tools": [{
                "id": "workspace.create_artifact",
                "description": "Create a bounded artifact",
                "arguments": {"name": "string", "content": "string"},
            }],
        },
    )

    assert decision["type"] == "tool_call"
    assert decision["tool"] == "workspace.create_artifact"
    assert decision["arguments"]["name"] == "note.txt"


def test_persistent_server_uses_json_schema_for_agent_decisions(tmp_path):
    from chimera.local_model import AGENT_DECISION_SCHEMA

    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    server_exe = tmp_path / "llama-server.exe"
    server_exe.write_bytes(b"fixture")
    provider = LocalModelProvider(LocalModelConfig(model, None, server_path=str(server_exe)))

    class FakeServer:
        def __init__(self):
            self.received = None

        def status(self):
            return {"state": "ready", "url": "http://127.0.0.1:1234", "pid": 10}

        def complete(self, *args, **kwargs):
            self.received = kwargs
            return (
                '{"type":"message","content":"Hello from the local server."}',
                {"provider": "llama-server"},
            )

    fake = FakeServer()
    provider.server = fake
    parsed, metrics = provider.structured_with_metrics(
        "Return a message decision.",
        json_schema=AGENT_DECISION_SCHEMA,
    )

    assert parsed["type"] == "message"
    assert parsed["content"] == "Hello from the local server."
    assert fake.received["json_schema"] == AGENT_DECISION_SCHEMA
    assert fake.received["grammar"] is None
    assert metrics["structured_output"] == "json_schema"


def test_local_agent_decision_rejects_malformed_json_from_runner(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    cli = tmp_path / "llama-cli.exe"
    cli.write_bytes(b"fixture")
    provider = LocalModelProvider(
        LocalModelConfig(model, str(cli)),
        lambda *_: '{"type":"message","content":',
    )

    with pytest.raises(ValueError, match="structured model output was invalid"):
        provider.decide([], {"version": 1, "tools": []})


def test_structured_server_failure_does_not_retry_with_cli(tmp_path, monkeypatch):
    from chimera.local_model import ServerUnavailable

    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    cli = tmp_path / "llama-cli.exe"
    cli.write_bytes(b"fixture")
    server_exe = tmp_path / "llama-server.exe"
    server_exe.write_bytes(b"fixture")
    provider = LocalModelProvider(
        LocalModelConfig(model, str(cli), server_path=str(server_exe)),
    )

    class BrokenServer:
        def status(self):
            return {"state": "ready", "url": "http://127.0.0.1:1234", "pid": 10}

        def complete(self, *args, **kwargs):
            raise ServerUnavailable("HTTP 400: invalid schema")

    provider.server = BrokenServer()
    calls = []
    monkeypatch.setattr(
        "chimera.local_model._run_streaming",
        lambda *args, **kwargs: calls.append(args),
    )

    with pytest.raises(RuntimeError, match="no CLI or hosted fallback was attempted"):
        provider.decide([], {"version": 1, "tools": []})
    assert calls == []



def test_agent_echo_guard_retries_and_prioritizes_desktop_tools(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    cli = tmp_path / "llama-cli.exe"
    cli.write_bytes(b"fixture")
    prompts = []
    request = "open Word create new document with a cake recipe and save it on Desktop"
    outputs = iter([
        json.dumps({"type": "message", "content": request}),
        json.dumps({
            "type": "tool_call",
            "tool": "computer.launch",
            "arguments": {"executable": "Word"},
        }),
    ])

    def runner(prompt, _config):
        prompts.append(prompt)
        return next(outputs)

    provider = LocalModelProvider(LocalModelConfig(model, str(cli)), runner)
    decision = provider.decide(
        [SimpleNamespace(role="user", content=request)],
        {"version": 1, "tools": [
            {"id": "workspace.list_files", "description": "List files", "arguments": {}},
            {"id": "computer.launch", "description": "Launch an app", "arguments": {"executable": "string"}},
            {"id": "computer.list_applications", "description": "Find visible apps", "arguments": {}},
            {"id": "workspace.create_file", "description": "Create file", "arguments": {"path": "string", "content": "string"}},
            {"id": "workspace.read_text", "description": "Read a text file", "arguments": {"path": "string"}},
        ]},
        session={"mode": "build", "workspace": str(tmp_path)},
    )

    assert decision["type"] == "tool_call"
    assert decision["tool"] == "computer.launch"
    assert decision["arguments"] == {"executable": "Word"}
    assert len(prompts) == 2
    app_list_position = prompts[0].index('"id":"computer.list_applications"')
    app_launch_position = prompts[0].index('"id":"computer.launch"')
    workspace_list_position = prompts[0].index('"id":"workspace.list_files"')
    assert app_list_position < app_launch_position < workspace_list_position
    assert "Never echo" in prompts[0]
    assert "RECOVERY" in prompts[1]


def test_agent_echo_guard_fails_closed_after_second_echo(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    cli = tmp_path / "llama-cli.exe"
    cli.write_bytes(b"fixture")
    request = "open Word create new document with a cake recipe and save it on Desktop"
    provider = LocalModelProvider(
        LocalModelConfig(model, str(cli)),
        lambda *_: json.dumps({"type": "message", "content": request}),
    )

    with pytest.raises(ValueError, match="repeated the task"):
        provider.decide(
            [SimpleNamespace(role="user", content=request)],
            {"version": 1, "tools": []},
            session={"mode": "build", "workspace": str(tmp_path)},
        )



def test_local_agent_retries_an_unregistered_tool_id_as_a_chat_answer(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    cli = tmp_path / "llama-cli.exe"
    cli.write_bytes(b"fixture")
    request = "Can you write code for palindrome check in Python?"
    prompts = []
    schemas = []
    outputs = iter([
        json.dumps({"type": "tool_call", "tool": "python", "arguments": {"code": "print('x')"}}),
        json.dumps({
            "type": "message",
            "content": "def is_palindrome(value):\n    cleaned = ''.join(ch.casefold() for ch in value if ch.isalnum())\n    return cleaned == cleaned[::-1]",
        }),
    ])
    provider = LocalModelProvider(
        LocalModelConfig(model, str(cli)),
        lambda prompt, _config: prompts.append(prompt) or next(outputs),
    )
    original = provider.structured_with_metrics

    def capture_schema(prompt, **kwargs):
        schemas.append(kwargs["json_schema"])
        return original(prompt, **kwargs)

    provider.structured_with_metrics = capture_schema
    catalog = {
        "version": 1,
        "tools": [
            {"id": "workspace.create_file", "description": "Create a file", "arguments": {"path": "string", "content": "string"}},
            {"id": "computer.launch", "description": "Launch an app", "arguments": {"executable": "string"}},
        ],
    }

    decision = provider.decide(
        [SimpleNamespace(role="user", content=request)],
        catalog,
        session={"mode": "plan", "workspace": str(tmp_path)},
    )

    assert decision["type"] == "message"
    assert "is_palindrome" in decision["content"]
    assert len(prompts) == 2
    assert "unregistered tool ID: 'python'" in prompts[1]
    assert "return the code directly" in prompts[1]
    assert schemas[0]["oneOf"][1]["properties"]["tool"]["enum"] == [
        "computer.launch", "workspace.create_file"
    ]
    assert schemas[1] == schemas[0]


def test_local_agent_without_tools_uses_message_only_decision_schema(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    cli = tmp_path / "llama-cli.exe"
    cli.write_bytes(b"fixture")
    schemas = []
    provider = LocalModelProvider(
        LocalModelConfig(model, str(cli)),
        lambda *_: json.dumps({"type": "message", "content": "Here is the code."}),
    )
    original = provider.structured_with_metrics

    def capture_schema(prompt, **kwargs):
        schemas.append(kwargs["json_schema"])
        return original(prompt, **kwargs)

    provider.structured_with_metrics = capture_schema
    decision = provider.decide(
        [SimpleNamespace(role="user", content="Write a palindrome function.")],
        {"version": 1, "tools": []},
    )

    assert decision["type"] == "message"
    assert len(schemas[0]["oneOf"]) == 1
    assert schemas[0]["oneOf"][0]["properties"]["type"]["enum"] == ["message"]


def test_local_agent_fails_closed_after_repeated_invalid_tool_choice(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    cli = tmp_path / "llama-cli.exe"
    cli.write_bytes(b"fixture")
    provider = LocalModelProvider(
        LocalModelConfig(model, str(cli)),
        lambda *_: json.dumps({"type": "tool_call", "tool": "python", "arguments": {}}),
    )

    with pytest.raises(ValueError, match="unregistered tool ID 'python'.*no tool was executed"):
        provider.decide(
            [SimpleNamespace(role="user", content="Write a palindrome function.")],
            {"version": 1, "tools": [{"id": "workspace.create_file", "arguments": {}}]},
        )


def test_new_local_model_defaults_enable_more_reasoning_budget_and_adaptive_gpu(tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    monkeypatch.setenv("CHIMERA_DATA_DIR", str(tmp_path / "config"))

    config = config_from_env({"CHIMERA_MODEL_PATH": str(model)})

    assert config.context == 8192
    assert config.max_tokens == 768
    assert config.threads == 8
    assert config.gpu_layers == "auto"


def test_legacy_default_model_config_migrates_to_adaptive_gpu_and_higher_budget(tmp_path, monkeypatch):
    from chimera.local_model import config_data_dir

    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    data_dir = tmp_path / "config"
    data_dir.mkdir()
    (data_dir / "model_config.json").write_text(json.dumps({
        "model_path": str(model),
        "cli_path": None,
        "server_path": None,
        "context": 4096,
        "max_tokens": 256,
        "threads": 4,
        "gpu_layers": 0,
    }), encoding="utf-8")
    monkeypatch.setenv("CHIMERA_DATA_DIR", str(data_dir))

    config = config_from_env({"CHIMERA_MODEL_PATH": str(model)})

    assert config.context == 8192
    assert config.max_tokens == 768
    assert config.threads == 8
    assert config.gpu_layers == "auto"


def test_saved_adaptive_gpu_setting_round_trips(tmp_path, monkeypatch):
    from chimera.local_model import save_persisted_config

    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    save_persisted_config(
        model,
        None,
        data_dir=tmp_path,
        context=8192,
        max_tokens=768,
        threads=8,
        gpu_layers="auto",
    )
    monkeypatch.setenv("CHIMERA_DATA_DIR", str(tmp_path))

    config = config_from_env({"CHIMERA_MODEL_PATH": str(model)})

    assert config.gpu_layers == "auto"
    assert json.loads((tmp_path / "model_config.json").read_text())["config_version"] == 2


def test_agent_blocks_repeated_previous_assistant_answer_after_one_recovery(tmp_path):
    stale_answer = "Can you create a new document in the workspace?"
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    cli = tmp_path / "llama-cli.exe"
    cli.write_bytes(b"fixture")
    outputs = iter([
        json.dumps({"type": "message", "content": stale_answer}),
        json.dumps({"type": "message", "content": stale_answer}),
    ])
    prompts = []
    provider = LocalModelProvider(
        LocalModelConfig(model, str(cli)),
        lambda prompt, _config: prompts.append(prompt) or next(outputs),
    )

    decision = provider.decide(
        [
            SimpleNamespace(role="assistant", content=stale_answer),
            SimpleNamespace(role="user", content="Open Paint and draw a portrait."),
        ],
        {"version": 1, "tools": [
            {"id": "computer.list_installed_applications", "arguments": {}},
            {"id": "computer.launch", "arguments": {"executable": "string"}},
        ]},
        session={"mode": "build", "workspace": str(tmp_path)},
    )

    assert decision["type"] == "message"
    assert "repeated an earlier response" in decision["content"]
    assert decision["_metrics"]["decision_recovery_attempted"] is True
    assert decision["_metrics"]["repeated_assistant_response_blocked"] is True
    assert len(prompts) == 2
    assert "RECOVERY" in prompts[1]


def test_repeated_assistant_response_fallback_uses_latest_real_tool_error(tmp_path):
    stale_answer = "Can you create a new document in the workspace?"
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    cli = tmp_path / "llama-cli.exe"
    cli.write_bytes(b"fixture")
    failure = "could not resolve an installed executable or Start-menu app for 'mspaint.exe'"
    outputs = iter([
        json.dumps({"type": "message", "content": stale_answer}),
        json.dumps({"type": "message", "content": stale_answer}),
    ])
    provider = LocalModelProvider(
        LocalModelConfig(model, str(cli)),
        lambda *_: next(outputs),
    )

    decision = provider.decide(
        [
            SimpleNamespace(role="assistant", content=stale_answer),
            SimpleNamespace(role="user", content="Open Paint and draw a portrait."),
            SimpleNamespace(role="tool", content=json.dumps({
                "tool": "computer.launch",
                "arguments": {"executable": "mspaint.exe"},
                "status": "blocked",
                "error": failure,
            })),
        ],
        {"version": 1, "tools": [
            {"id": "computer.list_installed_applications", "arguments": {}},
            {"id": "computer.launch", "arguments": {"executable": "string"}},
        ]},
        session={"mode": "build", "workspace": str(tmp_path)},
    )

    assert decision["type"] == "message"
    assert failure in decision["content"]
    assert "did not complete" in decision["content"]


def test_user_can_explicitly_ask_to_repeat_an_earlier_assistant_response(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    cli = tmp_path / "llama-cli.exe"
    cli.write_bytes(b"fixture")
    previous = "The file was created successfully."
    provider = LocalModelProvider(
        LocalModelConfig(model, str(cli)),
        lambda *_: json.dumps({"type": "message", "content": previous}),
    )

    decision = provider.decide(
        [
            SimpleNamespace(role="assistant", content=previous),
            SimpleNamespace(role="user", content="Repeat your previous answer exactly."),
        ],
        {"version": 1, "tools": []},
    )

    assert decision["content"] == previous
    assert decision["_metrics"].get("repeated_assistant_response_blocked") is not True


def test_agent_decision_uses_role_separated_chat_messages_for_local_server(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    server = tmp_path / "llama-server.exe"
    server.write_bytes(b"fixture")
    provider = LocalModelProvider(
        LocalModelConfig(model, None, server_path=str(server)),
    )
    captured = {}

    def fake_complete(prompt, max_tokens, **kwargs):
        captured["prompt"] = prompt
        captured["max_tokens"] = max_tokens
        captured.update(kwargs)
        return json.dumps({
            "type": "message",
            "content": "I will inspect the installed applications first.",
        }), {"duration_ms": 5}

    provider.server.complete = fake_complete
    decision = provider.decide(
        [
            SimpleNamespace(role="assistant", content="Previous answer."),
            SimpleNamespace(role="user", content="Open Paint."),
            SimpleNamespace(role="tool", content=json.dumps({
                "tool": "computer.launch",
                "arguments": {"executable": "mspaint.exe"},
                "status": "blocked",
                "error": "executable was not found",
            })),
        ],
        {"version": 1, "tools": [
            {"id": "computer.list_installed_applications", "arguments": {}},
            {"id": "computer.launch", "arguments": {"executable": "string"}},
        ]},
        session={"mode": "build", "workspace": str(tmp_path)},
    )

    chat_messages = captured["messages"]
    assert chat_messages[0]["role"] == "system"
    assert "TOOLS:" in chat_messages[0]["content"]
    assert [item["role"] for item in chat_messages[1:]] == [
        "assistant", "user", "user",
    ]
    assert "RUNTIME TOOL RESULT" in chat_messages[-1]["content"]
    assert "executable was not found" in chat_messages[-1]["content"]
    assert captured["json_schema"] is not None
    assert decision["type"] == "message"


def test_conversation_uses_role_separated_messages_for_local_server(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    server = tmp_path / "llama-server.exe"
    server.write_bytes(b"fixture")
    provider = LocalModelProvider(
        LocalModelConfig(model, None, server_path=str(server)),
    )
    captured = {}

    def fake_complete(prompt, max_tokens, **kwargs):
        captured["messages"] = kwargs["messages"]
        return "Hello. Ready when you are.", {"duration_ms": 3}

    provider.server.complete = fake_complete
    result = provider.converse([
        SimpleNamespace(role="assistant", content="Previous assistant answer."),
        SimpleNamespace(role="user", content="Hello, Chimera."),
    ])

    assert result.text == "Hello. Ready when you are."
    assert [item["role"] for item in captured["messages"]] == [
        "system", "assistant", "user",
    ]


def test_short_confirmation_continues_prior_goal_instead_of_echoing_earlier_request(tmp_path):
    earlier_request = "open paint and draw monalisa in it"
    stale_answer = earlier_request
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    cli = tmp_path / "llama-cli.exe"
    cli.write_bytes(b"fixture")
    outputs = iter([
        json.dumps({"type": "message", "content": stale_answer}),
        json.dumps({"type": "message", "content": stale_answer}),
    ])
    prompts = []
    provider = LocalModelProvider(
        LocalModelConfig(model, str(cli)),
        lambda prompt, _config: prompts.append(prompt) or next(outputs),
    )

    decision = provider.decide(
        [
            SimpleNamespace(role="user", content=earlier_request),
            SimpleNamespace(role="assistant", content="Can you create a new document in the workspace?"),
            SimpleNamespace(role="user", content="yes"),
        ],
        {"version": 1, "tools": [
            {"id": "computer.list_installed_applications", "arguments": {}},
            {"id": "computer.launch", "arguments": {"executable": "string"}},
        ]},
        session={"mode": "build", "workspace": str(tmp_path)},
    )

    assert decision["type"] == "message"
    assert decision["content"] != stale_answer
    assert "confirmation to continue the earlier task" in decision["content"]
    assert "task remains incomplete" in decision["content"]
    assert decision["_metrics"]["decision_recovery_attempted"] is True
    assert decision["_metrics"]["stale_user_request_echo_blocked"] is True
    assert len(prompts) == 2
    assert "short confirmation such as yes" in prompts[0]
    assert "continue the unresolved goal" in prompts[1]
