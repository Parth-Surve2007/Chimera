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
