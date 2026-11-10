import pytest
from chimera.task_execution import TaskStore, TaskPolicy

def test_task_and_step_state_persist(tmp_path):
    store=TaskStore(tmp_path/"tasks.db"); task=store.create("session","inspect project","plan", "local", "model"); step=store.add_step(task,"List files","workspace.list_files",{"relative":"."})
    store.transition(task,"ready"); store.step_transition(step,"completed",{"items":["a.py"]},invocation_id="i1")
    reopened=TaskStore(tmp_path/"tasks.db"); assert reopened.get(task).status=="ready"; assert reopened.steps(task)[0]["status"]=="completed"

def test_plan_mode_rejects_side_effects():
    with pytest.raises(PermissionError): TaskPolicy.validate("plan","workspace.create_artifact","workspace.write")

def test_build_mode_allows_policy_checked_operation():
    assert TaskPolicy.validate("build","workspace.create_artifact","workspace.write")



def test_task_todos_persist_and_retain_identity_when_reordered(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task = store.create("session", "prepare and verify a report")
    original = store.write_todos(task, [
        {"content": "Inspect the source material", "status": "in_progress"},
        {"content": "Create the report", "status": "pending"},
    ])
    first_id, second_id = original[0]["todo_id"], original[1]["todo_id"]
    updated = store.write_todos(task, [
        {"content": "Create the report", "status": "in_progress"},
        {"content": "Inspect the source material", "status": "completed"},
    ])
    assert [item["todo_id"] for item in updated] == [second_id, first_id]
    assert [item["status"] for item in updated] == ["in_progress", "completed"]
    assert TaskStore(tmp_path / "tasks.db").todos(task) == updated


def test_task_todos_reject_duplicates_multiple_active_and_oversized_lists(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task = store.create("session", "prepare a report")
    with pytest.raises(ValueError, match="unique"):
        store.write_todos(task, [{"content": "Inspect files"}, {"content": " inspect  files "}])
    with pytest.raises(ValueError, match="only one"):
        store.write_todos(task, [
            {"content": "Inspect files", "status": "in_progress"},
            {"content": "Create report", "status": "in_progress"},
        ])
    with pytest.raises(ValueError, match="at most"):
        store.write_todos(task, [{"content": f"Task {i}"} for i in range(17)])


def test_active_task_is_reused_and_terminal_tasks_are_not_resumed(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task_id = store.create("session-1", "Create and verify a report", mode="build")
    assert store.active_for_session("session-1").task_id == task_id
    store.transition(task_id, "running")
    store.transition(task_id, "ready")
    assert store.active_for_session("session-1").task_id == task_id
    store.transition(task_id, "completed")
    assert store.active_for_session("session-1") is None
    with pytest.raises(ValueError, match="terminal tasks"):
        store.resume(task_id)


def test_completion_is_blocked_by_unfinished_todos_and_steps(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task_id = store.create("session-1", "Open Notepad and export a PDF", mode="build")
    store.transition(task_id, "running")
    store.write_todos(task_id, [
        {"content": "Read the source document", "status": "completed"},
        {"content": "Create and verify the PDF", "status": "pending"},
    ])
    step_id = store.add_step(task_id, "Read source document", tool="computer.read_text")
    store.step_transition(step_id, "completed", result={"text": "source text"})
    assert store.reconcile_completion(task_id, "completed") == "ready"
    assert store.get(task_id).status == "ready"

    store.write_todos(task_id, [
        {"content": "Read the source document", "status": "completed"},
        {"content": "Create and verify the PDF", "status": "completed"},
    ])
    assert store.reconcile_completion(task_id, "completed") == "completed"
    assert store.can_complete(task_id)


def test_blocked_step_prevents_false_task_completion(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task_id = store.create("session-1", "Operate an application", mode="build")
    store.transition(task_id, "running")
    step_id = store.add_step(task_id, "Invoke unsupported control", tool="computer.invoke_element")
    store.step_transition(step_id, "blocked", error="control is not exposed")
    assert store.reconcile_completion(task_id, "completed") == "blocked"
    assert store.get(task_id).status == "blocked"



def test_resume_blocks_in_flight_side_effect_instead_of_replaying_it(tmp_path):
    database = tmp_path / "tasks.db"
    store = TaskStore(database)
    task_id = store.create("session-1", "Create a verified file", mode="build")
    store.transition(task_id, "running")
    step_id = store.add_step(
        task_id,
        "Create the requested PDF",
        tool="workspace.create_pdf",
        arguments={"path": "Desktop/Report.pdf"},
    )
    store.step_transition(
        step_id,
        "running",
        invocation_id="pdf-side-effect-started",
    )

    # A new TaskStore simulates a fresh desktop-app process after a crash.
    restarted = TaskStore(database)
    restarted.resume(task_id)
    task = restarted.get(task_id)
    step = restarted.steps(task_id)[0]

    assert task.status == "running"
    assert step["status"] == "blocked"
    assert step["invocation_id"] == "pdf-side-effect-started"
    assert "outcome is unknown" in step["error"]
    assert "do not replay blindly" in step["error"]
    assert any(
        item["kind"] == "step" and item["id"] == step_id
        for item in restarted.completion_blockers(task_id)
    )


def test_tool_approval_lifecycle_is_persisted_until_execution_starts(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task_id = store.create("session-approval", "Create a document", mode="build")
    store.transition(task_id, "running")

    store.apply_runtime_event(
        task_id,
        "event-requested",
        "tool.call.requested",
        {
            "invocation_id": "invocation-1",
            "tool": "workspace.create_document",
            "arguments": {"path": "report.docx"},
        },
    )
    step = store.steps(task_id)[0]
    assert step["status"] == "pending"
    assert step["approval_required"] == 0

    store.apply_runtime_event(
        task_id,
        "event-approval-required",
        "tool.permission.required",
        {"invocation_id": "invocation-1", "tool": "workspace.create_document"},
    )
    step = store.steps(task_id)[0]
    assert step["status"] == "waiting_approval"
    assert step["approval_required"] == 1
    assert step["error"] is None

    # A grant is not proof that the side effect has started. The explicit
    # tool.started event is the boundary that moves the step to running.
    store.apply_runtime_event(
        task_id,
        "event-approval-granted",
        "tool.permission.granted",
        {"invocation_id": "invocation-1", "tool": "workspace.create_document"},
    )
    assert store.steps(task_id)[0]["status"] == "waiting_approval"

    store.apply_runtime_event(
        task_id,
        "event-tool-started",
        "tool.started",
        {"invocation_id": "invocation-1", "tool": "workspace.create_document"},
    )
    assert store.steps(task_id)[0]["status"] == "running"

    store.apply_runtime_event(
        task_id,
        "event-tool-completed",
        "tool.completed",
        {
            "invocation_id": "invocation-1",
            "tool": "workspace.create_document",
            "status": "completed",
            "data": {"path": "report.docx", "verified": True},
        },
    )
    step = store.steps(task_id)[0]
    assert step["status"] == "completed"
    assert step["approval_required"] == 1
    assert store.can_complete(task_id)


def test_denied_tool_approval_blocks_the_persisted_step(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task_id = store.create("session-denial", "Delete an item", mode="build")
    store.transition(task_id, "running")

    store.apply_runtime_event(
        task_id,
        "event-requested",
        "tool.call.requested",
        {
            "invocation_id": "invocation-denied",
            "tool": "workspace.delete_file",
            "arguments": {"path": "important.txt"},
        },
    )
    store.apply_runtime_event(
        task_id,
        "event-approval-required",
        "tool.permission.required",
        {"invocation_id": "invocation-denied", "tool": "workspace.delete_file"},
    )
    store.apply_runtime_event(
        task_id,
        "event-approval-denied",
        "tool.permission.denied",
        {"invocation_id": "invocation-denied", "reason": "User declined the action."},
    )

    step = store.steps(task_id)[0]
    assert step["status"] == "blocked"
    assert step["approval_required"] == 1
    assert step["error"] == "User declined the action."
    assert any(
        blocker["kind"] == "step"
        and blocker["id"] == step["step_id"]
        and blocker["status"] == "blocked"
        for blocker in store.completion_blockers(task_id)
    )
