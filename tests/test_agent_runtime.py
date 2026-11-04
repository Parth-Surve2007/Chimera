from types import SimpleNamespace

from chimera.agent_runtime import AgentRuntime, AgentSessionStore


class Provider:
    config = SimpleNamespace(model_path="local.gguf")

    def converse(self, messages, session=None):
        return SimpleNamespace(text="Hello — I am Chimera.", metrics={"reasoning_source": "fake_local"})


def test_conversation_is_persisted_and_replayable(tmp_path):
    store = AgentSessionStore(tmp_path / "chimera.db")
    runtime = AgentRuntime(store, Provider())
    session_id = runtime.create_session(tmp_path, mode="plan")
    result = runtime.send(session_id, "hello")

    assert result["status"] == "completed"
    assert [(m.role, m.content) for m in store.messages(session_id)] == [
        ("user", "hello"),
        ("assistant", "Hello — I am Chimera."),
    ]
    events = store.events(session_id)
    assert [event.event_type for event in events] == [
        "session.created", "turn.started", "assistant.message.completed", "turn.completed"
    ]
    reopened = AgentSessionStore(tmp_path / "chimera.db")
    assert reopened.session(session_id)["state"] == "idle"
    assert len(reopened.list_sessions(tmp_path)) == 1


def test_provider_failure_is_truthful_and_durable(tmp_path):
    class Failing(Provider):
        def converse(self, messages, session=None):
            raise RuntimeError("runtime unavailable")

    store = AgentSessionStore(tmp_path / "chimera.db")
    runtime = AgentRuntime(store, Failing())
    session_id = runtime.create_session()
    result = runtime.send(session_id, "hello")
    assert result == {"status": "failed", "error": "runtime unavailable"}
    assert store.session(session_id)["state"] == "failed"
    assert store.events(session_id)[-1].event_type == "turn.failed"

def test_deciding_provider_can_answer_without_workspace(tmp_path):
    provider = DecidingProvider([{"type": "message", "content": "Hello from the configured model."}])
    store = AgentSessionStore(tmp_path / "chimera.db")
    runtime = AgentRuntime(store, provider)
    session_id = runtime.create_session()
    result = runtime.send(session_id, "hello")
    assert result["status"] == "completed"
    assert store.messages(session_id)[-1].content == "Hello from the configured model."


class DecidingProvider(Provider):
    def __init__(self, decisions): self.decisions = iter(decisions)
    def decide(self, messages, catalog, session=None): return next(self.decisions)


def test_agent_observes_real_tool_result_before_answer(tmp_path):
    (tmp_path / "main.py").write_text("print('ok')", encoding="utf-8")
    provider = DecidingProvider([
        {"version": 1, "type": "tool_call", "tool": "workspace.list_files", "arguments": {"relative": ".", "recursive": True}, "invocation_id": "i1"},
        {"version": 1, "type": "message", "content": "I found main.py.", "invocation_id": "unused"},
    ])
    store = AgentSessionStore(tmp_path / "chimera.db"); runtime = AgentRuntime(store, provider); sid = runtime.create_session(tmp_path)
    result = runtime.send(sid, "Inspect this project")
    assert result["status"] == "completed"
    assert any(m.role == "tool" and "main.py" in m.content for m in store.messages(sid))
    assert any(event.event_type == "tool.completed" for event in store.events(sid))


def test_write_tool_waits_for_specific_approval_and_resumes(tmp_path):
    provider = DecidingProvider([
        {"version": 1, "type": "tool_call", "tool": "workspace.create_artifact", "arguments": {"name": "report.md", "content": "report"}, "invocation_id": "write-1"},
        {"version": 1, "type": "message", "content": "The report is ready.", "invocation_id": "unused"},
    ])
    store = AgentSessionStore(tmp_path / "chimera.db"); runtime = AgentRuntime(store, provider); sid = runtime.create_session(tmp_path, mode="build")
    pending = runtime.send(sid, "Create a report")
    assert pending["status"] == "awaiting_approval"
    assert not (tmp_path / ".chimera-agent-artifacts" / sid / "report.md").exists()
    done = runtime.approve_pending(sid, True)
    assert done["status"] == "completed"
    assert (tmp_path / ".chimera-agent-artifacts" / sid / "report.md").read_text(encoding="utf-8") == "report"

def test_plan_mode_blocks_write_at_runtime_boundary(tmp_path):
    provider = DecidingProvider([
        {"type": "tool_call", "tool": "workspace.create_artifact", "arguments": {"name": "blocked.md", "content": "no"}, "invocation_id": "plan-write"},
        {"type": "message", "content": "I cannot write in Plan Mode."},
    ])
    store = AgentSessionStore(tmp_path / "chimera.db"); runtime = AgentRuntime(store, provider); sid = runtime.create_session(tmp_path, mode="plan")
    result = runtime.send(sid, "create a report")
    assert result["status"] == "completed"
    assert not (tmp_path / ".chimera-agent-artifacts" / sid / "blocked.md").exists()
    assert any(e.event_type == "tool.failed" and e.payload["status"] == "blocked" for e in store.events(sid))
    task = runtime.task_store.latest_for_session(sid)
    assert task.mode == "plan" and task.status == "completed"
    assert runtime.task_store.steps(task.task_id)[0]["status"] == "blocked"

def test_approval_continuation_updates_persisted_task(tmp_path):
    provider = DecidingProvider([
        {"type": "tool_call", "tool": "workspace.create_artifact", "arguments": {"name": "approved.md", "content": "yes"}, "invocation_id": "build-write"},
        {"type": "message", "content": "The report is ready."},
    ])
    store = AgentSessionStore(tmp_path / "chimera.db"); runtime = AgentRuntime(store, provider); sid = runtime.create_session(tmp_path, mode="build")
    assert runtime.send(sid, "create a report")["status"] == "awaiting_approval"
    task = runtime.task_store.latest_for_session(sid)
    assert runtime.approve_pending(sid, True)["status"] == "completed"
    assert runtime.task_store.get(task.task_id).status == "completed"
    assert runtime.task_store.steps(task.task_id)[0]["status"] == "completed"

def test_session_mode_round_trip_is_persisted(tmp_path):
    store = AgentSessionStore(tmp_path / "chimera.db")
    sid = store.create_session(tmp_path, "local.gguf", mode="plan")
    store.set_mode(sid, "build")
    assert AgentSessionStore(tmp_path / "chimera.db").session(sid)["mode"] == "build"
    store.set_mode(sid, "plan")
    assert AgentSessionStore(tmp_path / "chimera.db").session(sid)["mode"] == "plan"


def test_unknown_or_malformed_tool_never_executes(tmp_path):
    provider = DecidingProvider([{ "version": 1, "type": "tool_call", "tool": "workspace.shell", "arguments": {}, "invocation_id": "bad" }])
    store = AgentSessionStore(tmp_path / "chimera.db"); runtime = AgentRuntime(store, provider); sid = runtime.create_session(tmp_path)
    result = runtime.send(sid, "run something")
    assert result["status"] == "failed"
    assert "unregistered tool" in result["error"]
    assert not list(tmp_path.rglob("*.exe"))


def test_runtime_delivers_ordered_events_to_subscriber(tmp_path):
    provider = DecidingProvider([{"version": 1, "type": "message", "content": "done"}])
    store = AgentSessionStore(tmp_path / "chimera.db"); runtime = AgentRuntime(store, provider); sid = runtime.create_session(tmp_path)
    seen = []; runtime.subscribe(sid, lambda event: seen.append((event.sequence, event.event_type)))
    assert runtime.send(sid, "hello")["status"] == "completed"
    assert [kind for _, kind in seen] == ["turn.started", "model.request.started", "model.request.completed", "turn.completed"]
    assert [seq for seq, _ in seen] == sorted(seq for seq, _ in seen)


def test_cancellation_is_persisted_and_prevents_next_decision(tmp_path):
    import sqlite3
    provider = DecidingProvider([{"version": 1, "type": "message", "content": "unused"}])
    store = AgentSessionStore(tmp_path / "chimera.db"); runtime = AgentRuntime(store, provider); sid = runtime.create_session(tmp_path)
    with sqlite3.connect(store.db_path) as db: db.execute("UPDATE agent_sessions SET state='reasoning' WHERE session_id=?", (sid,))
    assert runtime.cancel(sid) is True
    assert store.session(sid)["state"] == "cancelled"
    assert store.events(sid)[-1].event_type == "turn.cancelled"


def test_invalid_agent_envelope_reports_received_shape():
    try:
        AgentRuntime._decision({"type": "text", "text": "hello"})
    except ValueError as exc:
        assert "type='text'" in str(exc)
        assert "fields=text, type" in str(exc)
    else:
        raise AssertionError("unsupported provider envelope was accepted")



def test_agent_tool_catalog_exposes_live_app_discovery_and_approval_gated_launch(tmp_path):
    store = AgentSessionStore(tmp_path / "chimera.db")
    runtime = AgentRuntime(store, DecidingProvider([]))
    session_id = runtime.create_session(tmp_path, mode="build")
    session = store.session(session_id)

    tool_ids = {
        item["id"]
        for item in runtime._protocol_catalog(session)["tools"]
    }

    assert "computer.list_applications" in tool_ids
    assert "computer.launch" in tool_ids
    assert "computer.inspect_window" not in tool_ids


def test_word_launch_is_resolved_before_exact_approval_is_persisted(tmp_path, monkeypatch):
    from chimera import agent_runtime as runtime_module

    resolved = r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE"

    def fake_launch(self, executable, approved=False):
        assert executable == "Word"
        assert approved is False
        return {
            "status": "awaiting_approval",
            "application": resolved,
            "permission": "computer.launch",
        }

    monkeypatch.setattr(runtime_module.WindowsComputerUse, "launch", fake_launch)
    provider = DecidingProvider([{
        "type": "tool_call",
        "tool": "computer.launch",
        "arguments": {"executable": "Word"},
    }])
    store = AgentSessionStore(tmp_path / "chimera.db")
    runtime = AgentRuntime(store, provider)
    session_id = runtime.create_session(tmp_path, mode="build")

    result = runtime.send(session_id, "Open Word")

    assert result["status"] == "awaiting_approval"
    pending = store.pending_tool(session_id)
    assert pending["arguments"]["executable"] == resolved



def test_invalid_tool_identifier_error_includes_the_returned_value():
    try:
        AgentRuntime._decision({"type": "tool_call", "tool": "python", "arguments": {}})
    except ValueError as exc:
        assert "'python'" in str(exc)
        assert "registered workspace.*, computer.*, or application.* ID" in str(exc)
    else:
        raise AssertionError("an invalid tool identifier was accepted")



def test_local_agent_can_manage_persistent_todos_in_plan_mode(tmp_path):
    class TodoProvider:
        config = SimpleNamespace(model_path="local.gguf")

        def __init__(self):
            self.calls = 0
            self.catalogs = []

        def decide(self, messages, catalog, session=None):
            self.catalogs.append(catalog)
            self.calls += 1
            if self.calls == 1:
                return {
                    "type": "tool_call",
                    "tool": "agent.todo_write",
                    "arguments": {"todos": [
                        {"content": "Inspect the available workspace", "status": "in_progress"},
                        {"content": "Verify the requested outcome", "status": "pending"},
                    ]},
                }
            if self.calls == 2:
                return {"type": "tool_call", "tool": "agent.todo_read", "arguments": {}}
            return {"type": "message", "content": "The task checklist is saved."}

    provider = TodoProvider()
    store = AgentSessionStore(tmp_path / "chimera.db")
    runtime = AgentRuntime(store, provider)
    session_id = runtime.create_session(mode="plan")
    result = runtime.send(session_id, "Plan a multi-step workspace task.")
    assert result["status"] == "completed"
    assert provider.calls == 3
    offered = {item["id"] for item in provider.catalogs[0]["tools"]}
    assert {"agent.todo_read", "agent.todo_write"} <= offered
    task = runtime.task_store.latest_for_session(session_id)
    assert [item["status"] for item in runtime.task_store.todos(task.task_id)] == ["in_progress", "pending"]



def test_agent_runtime_executes_uia_action_only_after_exact_approval(tmp_path, monkeypatch):
    import time
    from chimera.computer_runtime import (
        ApplicationIdentity, ComputerObservation, ElementReference,
    )

    class FakeUIA:
        def __init__(self):
            self.calls = []

        def action(self, hwnd, runtime_id, operation, value=None, expected_pid=None, options=None):
            self.calls.append((hwnd, runtime_id, operation, value, expected_pid, options))
            return {"status": "completed", "verified": True, "backend": "uia", "value": value}

    provider = DecidingProvider([
        {
            "type": "tool_call",
            "tool": "computer.set_value",
            "arguments": {
                "session_id": "authorized-window",
                "reference_id": "fresh-control",
                "value": "Chimera",
            },
            "invocation_id": "uia-write-1",
        },
        {"type": "message", "content": "The application field was updated and verified."},
    ])
    runtime = AgentRuntime(AgentSessionStore(tmp_path / "agent.db"), provider)
    session_id = runtime.create_session(tmp_path, mode="build")
    identity = ApplicationIdentity(
        pid=4321, executable=r"C:\\Program Files\\Example\\app.exe",
        process_create_time=1.0, owner_session_id=session_id,
        session_id="authorized-window",
    )
    reference = ElementReference(
        reference_id="fresh-control", session_id="authorized-window", hwnd=99,
        runtime_id="42", generation=3, expires_at=time.time() + 60,
    )
    observation = ComputerObservation(
        observation_id="observation-1", session_id="authorized-window", generation=3,
        created_at=time.time(), backend="uia", application={},
        windows=[{"hwnd": 99}], elements=[],
    )
    runtime.computer.sessions["authorized-window"] = identity
    runtime.computer.references[reference.reference_id] = reference
    runtime.computer.observations["authorized-window"] = (3, observation)
    monkeypatch.setattr(runtime.computer, "_identity_is_current", lambda _identity: True)
    monkeypatch.setattr(runtime.computer.backend, "window_process_id", lambda _hwnd: 4321)
    fake = FakeUIA()
    runtime.computer.uia = fake

    pending = runtime.send(session_id, "Set the selected application field to Chimera.")
    assert pending["status"] == "awaiting_approval"
    assert pending["tool"] == "computer.set_value"
    assert fake.calls == []

    completed = runtime.approve_pending(session_id, approve=True)
    assert completed["status"] == "completed"
    assert fake.calls == [(99, "42", "set_value", "Chimera", 4321, {})]
    assert "updated and verified" in completed["message"].content
    assert any(event.event_type == "tool.permission.granted" for event in runtime.store.events(session_id))
    assert any(event.event_type == "tool.completed" for event in runtime.store.events(session_id))


def test_agent_runtime_plan_mode_blocks_uia_mutation_before_approval(tmp_path, monkeypatch):
    import time
    from chimera.computer_runtime import (
        ApplicationIdentity, ComputerObservation, ElementReference,
    )

    class FakeUIA:
        def __init__(self):
            self.calls = []

        def action(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return {"status": "completed", "verified": True, "backend": "uia"}

    provider = DecidingProvider([
        {
            "type": "tool_call",
            "tool": "computer.set_value",
            "arguments": {
                "session_id": "authorized-window",
                "reference_id": "fresh-control",
                "value": "forbidden",
            },
        },
        {"type": "message", "content": "Plan Mode cannot change the application."},
    ])
    runtime = AgentRuntime(AgentSessionStore(tmp_path / "agent.db"), provider)
    session_id = runtime.create_session(tmp_path, mode="plan")
    identity = ApplicationIdentity(
        pid=4321, executable=r"C:\\Program Files\\Example\\app.exe",
        process_create_time=1.0, owner_session_id=session_id,
        session_id="authorized-window",
    )
    reference = ElementReference(
        reference_id="fresh-control", session_id="authorized-window", hwnd=99,
        runtime_id="42", generation=3, expires_at=time.time() + 60,
    )
    observation = ComputerObservation(
        observation_id="observation-1", session_id="authorized-window", generation=3,
        created_at=time.time(), backend="uia", application={},
        windows=[{"hwnd": 99}], elements=[],
    )
    runtime.computer.sessions["authorized-window"] = identity
    runtime.computer.references[reference.reference_id] = reference
    runtime.computer.observations["authorized-window"] = (3, observation)
    monkeypatch.setattr(runtime.computer, "_identity_is_current", lambda _identity: True)
    monkeypatch.setattr(runtime.computer.backend, "window_process_id", lambda _hwnd: 4321)
    fake = FakeUIA()
    runtime.computer.uia = fake

    result = runtime.send(session_id, "Change the selected application field.")
    assert result["status"] == "completed"
    assert result["message"].content == "Plan Mode cannot change the application."
    assert fake.calls == []
    assert any(
        event.event_type == "tool.failed" and event.payload.get("status") == "blocked"
        for event in runtime.store.events(session_id)
    )
