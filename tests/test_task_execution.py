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
