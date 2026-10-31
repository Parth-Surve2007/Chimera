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
