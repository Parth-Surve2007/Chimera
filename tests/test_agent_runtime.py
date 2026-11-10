import json
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
    assert task.mode == "plan" and task.status == "blocked"
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


def test_agent_can_inspect_installed_start_menu_applications(tmp_path, monkeypatch):
    from chimera import agent_runtime as runtime_module

    installed = [{
        "name": "Paint",
        "app_id": "Microsoft.Paint_8wekyb3d8bbwe!App",
    }]
    monkeypatch.setattr(
        runtime_module.WindowsComputerUse,
        "list_installed_applications",
        lambda self: installed,
    )
    provider = DecidingProvider([
        {
            "type": "tool_call",
            "tool": "computer.list_installed_applications",
            "arguments": {},
            "invocation_id": "installed-apps-1",
        },
        {"type": "message", "content": "Paint is installed and available to launch."},
    ])
    store = AgentSessionStore(tmp_path / "chimera.db")
    runtime = AgentRuntime(store, provider)
    session_id = runtime.create_session(tmp_path, mode="build")

    result = runtime.send(session_id, "Find the installed Paint application.")

    assert result["status"] == "completed"
    tool_message = next(item for item in store.messages(session_id) if item.role == "tool")
    payload = json.loads(tool_message.content)
    assert payload["tool"] == "computer.list_installed_applications"
    assert payload["status"] == "completed"
    assert payload["result"]["items"] == installed
    assert any(
        item["id"] == "computer.list_installed_applications"
        for item in runtime._protocol_catalog(store.session(session_id))["tools"]
    )


def test_identical_failed_tool_call_is_not_executed_twice_in_one_turn(tmp_path, monkeypatch):
    from chimera import agent_runtime as runtime_module

    calls = []

    def failed_launch(self, executable, approved=False):
        calls.append((executable, approved))
        return {
            "status": "blocked",
            "reason": "could not resolve an installed executable or Start-menu app",
        }

    monkeypatch.setattr(runtime_module.WindowsComputerUse, "launch", failed_launch)
    repeated_call = {
        "type": "tool_call",
        "tool": "computer.launch",
        "arguments": {"executable": "paint"},
    }
    provider = DecidingProvider([
        dict(repeated_call),
        dict(repeated_call),
        {"type": "message", "content": "Paint could not be resolved, so I stopped."},
    ])
    store = AgentSessionStore(tmp_path / "chimera.db")
    runtime = AgentRuntime(store, provider)
    session_id = runtime.create_session(tmp_path, mode="build")

    result = runtime.send(session_id, "Open Paint.")

    assert result["status"] == "completed"
    assert calls == [("paint", False)]
    assert any(event.event_type == "tool.repeat.blocked" for event in store.events(session_id))
    repeated_results = [
        json.loads(item.content)
        for item in store.messages(session_id)
        if item.role == "tool"
    ]
    assert len(repeated_results) == 2
    assert "Identical retry not executed" in repeated_results[1]["error"]



def test_runtime_continues_same_task_after_short_confirmation_and_restart(tmp_path):
    class TodoProvider:
        config = SimpleNamespace(model_path="local.gguf")

        def __init__(self, complete=False):
            self.calls = 0
            self.complete = complete
            self.seen_messages = []

        def decide(self, messages, catalog, session=None):
            self.seen_messages = messages
            self.calls += 1
            if self.calls == 1:
                status = "completed" if self.complete else "in_progress"
                return {
                    "type": "tool_call",
                    "tool": "agent.todo_write",
                    "arguments": {"todos": [
                        {"content": "Read the Notepad document", "status": status},
                        {"content": "Create and verify the PDF", "status": "completed" if self.complete else "pending"},
                    ]},
                }
            return {"type": "message", "content": "The task state has been saved."}

    db_path = tmp_path / "chimera.db"
    store = AgentSessionStore(db_path)
    first_provider = TodoProvider()
    first_runtime = AgentRuntime(store, first_provider)
    session_id = first_runtime.create_session(tmp_path, mode="build")
    assert first_runtime.send(session_id, "Open Notepad and export its text to PDF.")["status"] == "completed"
    original_task = first_runtime.task_store.active_for_session(session_id)
    assert original_task is not None
    assert original_task.status == "ready"

    # Reconstruct the runtime to simulate a desktop-app process restart.
    second_provider = TodoProvider(complete=True)
    second_runtime = AgentRuntime(AgentSessionStore(db_path), second_provider)
    result = second_runtime.send(session_id, "yes, continue")
    assert result["status"] == "completed"
    resumed_task = second_runtime.task_store.latest_for_session(session_id)
    assert resumed_task.task_id == original_task.task_id
    assert resumed_task.status == "completed"
    context_messages = [m.content for m in second_provider.seen_messages if m.role == "system"]
    assert any("Active durable task state" in content for content in context_messages)
    assert any(original_task.task_id in content for content in context_messages)
    assert any(event.event_type == "task.resumed" for event in second_runtime.store.events(session_id))


def test_explicit_new_task_pauses_unfinished_task_and_can_resume_it(tmp_path):
    class PendingTodoProvider:
        config = SimpleNamespace(model_path="local.gguf")

        def __init__(self, decisions):
            self.decisions = iter(decisions)

        def decide(self, messages, catalog, session=None):
            return next(self.decisions)

    store = AgentSessionStore(tmp_path / "chimera.db")
    first = PendingTodoProvider([
        {"type": "tool_call", "tool": "agent.todo_write", "arguments": {"todos": [
            {"content": "Finish the existing research", "status": "pending"},
        ]}},
        {"type": "message", "content": "The research task is saved."},
    ])
    runtime = AgentRuntime(store, first)
    session_id = runtime.create_session(tmp_path, mode="build")
    assert runtime.send(session_id, "Research this topic")["status"] == "completed"
    original = runtime.task_store.active_for_session(session_id)
    assert original is not None

    second = PendingTodoProvider([
        {"type": "message", "content": "Calculator inspection is complete."},
    ])
    runtime.provider = second
    assert runtime.send(session_id, "New task: open Calculator and inspect it")["status"] == "completed"
    new_task = runtime.task_store.latest_for_session(session_id)
    assert new_task.task_id != original.task_id
    assert runtime.task_store.get(original.task_id).status == "paused"
    assert runtime.task_store.active_for_session(session_id) is None

    third = PendingTodoProvider([
        {"type": "message", "content": "I have resumed the previous research task."},
    ])
    runtime.provider = third
    assert runtime.send(session_id, "Resume previous task and continue")["status"] == "completed"
    assert runtime.task_store.active_for_session(session_id).task_id == original.task_id
    assert runtime.task_store.get(original.task_id).status == "ready"



def test_malformed_tool_result_envelope_gets_one_safe_format_repair(tmp_path):
    class RepairProvider:
        config = SimpleNamespace(model_path="local.gguf")

        def __init__(self):
            self.calls = 0
            self.repair_prompt = ""

        def decide(self, messages, catalog, session=None):
            self.calls += 1
            if self.calls == 1:
                return {
                    "tool": "workspace.create_file",
                    "arguments": {"path": "claimed.txt", "content": "not executed"},
                    "status": "completed",
                    "result": {"path": "claimed.txt"},
                    "error": None,
                    "invocation_id": "fabricated-result",
                }
            self.repair_prompt = messages[-1].content
            return {"type": "message", "content": "I can continue with a valid action decision."}

    provider = RepairProvider()
    store = AgentSessionStore(tmp_path / "chimera.db")
    runtime = AgentRuntime(store, provider)
    session_id = runtime.create_session(tmp_path, mode="build")
    result = runtime.send(session_id, "Create a file only after approval.")

    assert result["status"] == "completed"
    assert provider.calls == 2
    assert "FORMAT REPAIR REQUIRED" in provider.repair_prompt
    assert "no action was executed" in provider.repair_prompt
    assert not (tmp_path / "claimed.txt").exists()
    assert not store.pending_tool(session_id)



def test_approved_skills_are_discovered_and_loaded_on_demand(tmp_path):
    from chimera.learning import ProposedSkill

    class SkillProvider:
        config = SimpleNamespace(model_path="local.gguf")

        def __init__(self, skill_id):
            self.skill_id = skill_id
            self.calls = 0
            self.catalogs = []

        def decide(self, messages, catalog, session=None):
            self.catalogs.append(catalog)
            self.calls += 1
            if self.calls == 1:
                return {"type": "tool_call", "tool": "agent.skill_list", "arguments": {}}
            if self.calls == 2:
                return {
                    "type": "tool_call",
                    "tool": "agent.skill_read",
                    "arguments": {"skill_id": self.skill_id},
                }
            return {"type": "message", "content": "I loaded the approved procedure and will verify each step."}

    store = AgentSessionStore(tmp_path / "chimera.db")
    provider = SkillProvider("skill_verified_notepad_export")
    runtime = AgentRuntime(store, provider)
    approved = ProposedSkill(
        skill_id=provider.skill_id,
        name="Verified Notepad export",
        description="Read a Notepad document and create a verified PDF.",
        version="1.0.0",
        source_session_id="prior-session",
        required_permissions=["computer.observe", "workspace.write"],
        parameters_schema={"desktop_relative_path": "string"},
        reusable_steps=[
            {
                "action": "workspace.create_document",
                "sanitized_arguments": {"path": "Desktop/Report.pdf", "content": "<source_text>"},
            }
        ],
        expected_outputs=["valid PDF", "extracted text matches source"],
        status="proposed",
    )
    proposed = ProposedSkill(
        skill_id="skill_unapproved",
        name="Unapproved procedure",
        description="Must not enter the agent context.",
        version="1.0.0",
        source_session_id="prior-session",
        required_permissions=[],
        parameters_schema={},
        reusable_steps=[],
        expected_outputs=[],
        status="proposed",
    )
    runtime.skill_store.save(approved)
    runtime.skill_store.save(proposed)
    assert runtime.skill_store.approve(approved.skill_id)

    session_id = runtime.create_session(tmp_path, mode="build")
    result = runtime.send(session_id, "Create a PDF from the current Notepad document.")
    assert result["status"] == "completed"

    first_tool_ids = {item["id"] for item in provider.catalogs[0]["tools"]}
    assert {"agent.skill_list", "agent.skill_read"} <= first_tool_ids
    tool_results = [
        json.loads(item.content)["result"]
        for item in store.messages(session_id)
        if item.role == "tool"
    ]
    listing = next(item for item in tool_results if "skills" in item)
    assert [item["skill_id"] for item in listing["skills"]] == [approved.skill_id]
    loaded = next(item for item in tool_results if "reusable_steps" in item)
    assert loaded["reusable_steps"][0]["action"] == "workspace.create_document"
    assert "Do not" in loaded["notice"] or "Never" in loaded["notice"]
    assert "Unapproved procedure" not in json.dumps(provider.catalogs[0])



def test_paint_draw_request_is_not_treated_as_a_terminal_launch(tmp_path):
    runtime = AgentRuntime(
        AgentSessionStore(tmp_path / "chimera.db"),
        DecidingProvider([{"type": "message", "content": "unused"}]),
    )
    assert runtime._is_terminal_launch_request("open Paint")
    assert runtime._is_terminal_launch_request("Please launch Notepad.")
    assert not runtime._is_terminal_launch_request("open ms paint and draw a house")
    assert not runtime._is_terminal_launch_request("open Paint, then sketch a tree")



def test_agent_runtime_requires_pdf_approval_then_verifies_the_real_file(tmp_path):
    from pypdf import PdfReader

    source = "Actual source text from the approved task.\nSecond line with café and €."
    provider = DecidingProvider([
        {
            "type": "tool_call",
            "tool": "workspace.create_pdf",
            "arguments": {
                "path": "Notepad-Export.pdf",
                "title": "Notepad Export",
                "content": source,
            },
            "invocation_id": "pdf-export-1",
        },
    ])
    store = AgentSessionStore(tmp_path / "chimera.db")
    runtime = AgentRuntime(store, provider)
    session_id = runtime.create_session(tmp_path, mode="build")

    pending = runtime.send(session_id, "Create a PDF from the Notepad text.")
    assert pending["status"] == "awaiting_approval"
    target = tmp_path / "Notepad-Export.pdf"
    assert not target.exists()

    completed = runtime.approve_pending(session_id, True)
    assert completed["status"] == "completed"
    assert completed["verification"]["path"] == "Notepad-Export.pdf"
    assert completed["verification"]["bytes"] == target.stat().st_size
    assert completed["verification"]["sha256"]
    reader = PdfReader(str(target), strict=True)
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    normalize = lambda value: " ".join(value.replace("\\u00a0", " ").split())
    assert normalize("Notepad Export\n" + source) == normalize(extracted)

    tool_result = next(
        json.loads(item.content)
        for item in store.messages(session_id)
        if item.role == "tool" and '"workspace.create_pdf"' in item.content
    )
    assert tool_result["status"] == "completed"
    assert tool_result["result"]["independent_verification"] is True
    assert tool_result["result"]["text_verified"] is True
    task = runtime.task_store.latest_for_session(session_id)
    assert task.status == "completed"
    assert runtime.task_store.steps(task.task_id)[0]["status"] == "completed"



def test_stale_approval_cannot_replay_an_invocation_already_started(tmp_path):
    provider = DecidingProvider([
        {
            "type": "tool_call",
            "tool": "workspace.create_file",
            "arguments": {"path": "once.txt", "content": "must not be duplicated"},
            "invocation_id": "once-only-invocation",
        },
    ])
    database = tmp_path / "chimera.db"
    store = AgentSessionStore(database)
    runtime = AgentRuntime(store, provider)
    session_id = runtime.create_session(tmp_path, mode="build")
    pending = runtime.send(session_id, "Create once.txt")
    assert pending["status"] == "awaiting_approval"
    assert store.pending_tool(session_id)["invocation_id"] == "once-only-invocation"

    # Simulate a process crash after the durable tool.started event but before
    # the old version removed its pending-approval row.
    task = runtime.task_store.active_for_session(session_id)
    step = next(
        item for item in runtime.task_store.steps(task.task_id)
        if item["invocation_id"] == "once-only-invocation"
    )
    runtime.task_store.step_transition(
        step["step_id"], "running", invocation_id="once-only-invocation"
    )

    restarted = AgentRuntime(AgentSessionStore(database), provider)
    result = restarted.approve_pending(session_id, True)
    assert result["status"] == "blocked"
    assert "will not be replayed" in result["error"]
    assert restarted.store.pending_tool(session_id) is None
    assert not (tmp_path / "once.txt").exists()
    resumed_task = restarted.task_store.latest_for_session(session_id)
    assert resumed_task.status == "blocked"
    assert restarted.task_store.steps(resumed_task.task_id)[0]["status"] == "blocked"


def test_completed_multi_tool_task_creates_pending_skill_but_never_auto_approves(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.py").write_text("print('hello')", encoding="utf-8")

    provider = DecidingProvider([
        {
            "type": "tool_call",
            "tool": "workspace.list_files",
            "arguments": {"relative": ".", "recursive": True},
            "invocation_id": "learn-list",
        },
        {
            "type": "tool_call",
            "tool": "workspace.search_files",
            "arguments": {"pattern": "*.py"},
            "invocation_id": "learn-search",
        },
        {
            "type": "tool_call",
            "tool": "workspace.inspect_file",
            "arguments": {"path": "main.py"},
            "invocation_id": "learn-inspect",
        },
        {"type": "message", "content": "I inspected the workspace and verified the file metadata."},
        {"type": "tool_call", "tool": "agent.skill_list", "arguments": {}},
        {"type": "message", "content": "No approved skills are available yet."},
        {"type": "tool_call", "tool": "agent.skill_list", "arguments": {}},
        {"type": "message", "content": "The approved procedure is now available."},
    ])
    store = AgentSessionStore(tmp_path / "chimera.db")
    runtime = AgentRuntime(store, provider)
    session_id = runtime.create_session(workspace, mode="plan")

    result = runtime.send(session_id, "Inspect this project and identify its Python files.")
    assert result["status"] == "completed"

    proposals = runtime.skill_store.proposals()
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["status"] == "proposed"
    assert len(proposal["reusable_steps"]) == 3
    assert proposal["source_task_id"]
    assert runtime.skill_store.approved() == []

    # A pending proposal is invisible to the executable skill catalog.
    first_listing = runtime.send(session_id, "List my approved reusable skills.")
    assert first_listing["status"] == "completed"
    first_payloads = [
        json.loads(item.content)
        for item in store.messages(session_id)
        if item.role == "tool"
    ]
    first_listing_result = [
        item["result"] for item in first_payloads
        if item.get("tool") == "agent.skill_list"
    ][-1]
    assert first_listing_result["count"] == 0

    assert runtime.skill_store.approve(proposal["skill_id"]) is True
    second_listing = runtime.send(session_id, "List my approved reusable skills again.")
    assert second_listing["status"] == "completed"
    all_payloads = [
        json.loads(item.content)
        for item in store.messages(session_id)
        if item.role == "tool"
    ]
    second_listing_result = [
        item["result"] for item in all_payloads
        if item.get("tool") == "agent.skill_list"
    ][-1]
    assert second_listing_result["count"] == 1
    assert second_listing_result["skills"][0]["skill_id"] == proposal["skill_id"]
