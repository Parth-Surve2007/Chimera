"""Integration tests for AgentRuntime-governed multi-capability workflows."""
from __future__ import annotations

from pathlib import Path

import pytest

from chimera.agent_runtime import AgentRuntime, AgentSessionStore
from chimera.task_execution import TaskStore
from chimera.workflow_integration import AgenticWorkflowOrchestrator
from tests.fixtures.cross_capability_demo import create_demo_workspace


class NoInferenceProvider:
    def __init__(self):
        self.config = type("Config", (), {"model_path": None})()


@pytest.fixture
def workflow_env(tmp_path):
    workspace = create_demo_workspace(tmp_path)
    db_path = tmp_path / "chimera.db"
    store = AgentSessionStore(db_path)
    runtime = AgentRuntime(store, NoInferenceProvider())
    task_store = TaskStore(db_path)
    orchestrator = AgenticWorkflowOrchestrator(runtime, task_store, workspace)
    session_id = runtime.create_session(workspace=workspace, mode="build")
    return {
        "workspace": workspace, "store": store, "runtime": runtime,
        "task_store": task_store, "orchestrator": orchestrator,
        "session_id": session_id,
    }


def report_path(workspace: Path, session_id: str) -> Path:
    return workspace / ".chimera-agent-artifacts" / session_id / "binary_analysis_report.md"


def test_workflow_runs_real_workspace_and_rea_steps_then_pauses_for_write_approval(workflow_env):
    env = workflow_env
    result = env["orchestrator"].execute_objective(
        env["session_id"],
        "Analyze the executable in this workspace, identify its format, architecture, sections, imports, exports and interesting strings, then generate a report.",
    )
    assert result["status"] == "awaiting_approval", result.get("step_summary", result)
    plan = env["orchestrator"].plan_store.get_plan(result["plan_id"])
    completed = [step for step in plan.steps if step.execution_state.value == "completed"]
    assert {"list_files", "detect_format", "extract_strings", "analyze_artifact"} <= {step.tool for step in completed}
    assert all(step.verification_status == "verified" for step in completed)
    assert not report_path(env["workspace"], env["session_id"]).exists()

    resumed = env["orchestrator"].approve_pending(env["session_id"], True)
    assert resumed["status"] == "completed"
    report = report_path(env["workspace"], env["session_id"])
    assert report.is_file()
    body = report.read_text(encoding="utf-8")
    assert "Binary Analysis Report" in body
    assert "format_type" in body
    assert "ChimeraTestArtifact" in body


def test_rejecting_workflow_write_prevents_artifact_creation(workflow_env):
    env = workflow_env
    result = env["orchestrator"].execute_objective(
        env["session_id"], "Analyze the executable and produce a technical report."
    )
    assert result["status"] == "awaiting_approval", result.get("step_summary", result)
    assert env["store"].pending_tool(env["session_id"])["tool"] == "workspace.create_artifact"
    rejected = env["orchestrator"].approve_pending(env["session_id"], False)
    assert rejected["status"] == "blocked"
    assert not report_path(env["workspace"], env["session_id"]).exists()


def test_ambiguous_binary_discovery_fails_closed(workflow_env):
    env = workflow_env
    (env["workspace"] / "second.dll").write_bytes(b"MZ" + b"\x00" * 128)
    result = env["orchestrator"].execute_objective(
        env["session_id"], "Analyze the executable and produce a report."
    )
    assert result["status"] == "failed", result
    plan = env["orchestrator"].plan_store.get_plan(result["plan_id"])
    assert not any(step.tool == "analyze_artifact" and step.execution_state.value == "completed" for step in plan.steps)
    failed = next(step for step in plan.steps if step.execution_state.value == "failed")
    assert "exactly one" in failed.error


def test_plan_mode_blocks_report_write_without_creating_pending_approval(tmp_path):
    workspace = create_demo_workspace(tmp_path)
    db_path = tmp_path / "plan-mode.db"
    store = AgentSessionStore(db_path)
    runtime = AgentRuntime(store, NoInferenceProvider())
    task_store = TaskStore(db_path)
    orchestrator = AgenticWorkflowOrchestrator(runtime, task_store, workspace)
    session_id = runtime.create_session(workspace=workspace, mode="plan")
    result = orchestrator.execute_objective(session_id, "Analyze the executable and produce a report.")
    assert result["status"] == "blocked"
    assert store.pending_tool(session_id) is None
    assert not report_path(workspace, session_id).exists()


def test_workflow_rejects_unregistered_application_action(workflow_env):
    env = workflow_env
    with pytest.raises(ValueError, match="unregistered"):
        env["runtime"].execute_workflow_tool(
            env["session_id"], "application.rea.not_registered", {},
            plan_id="plan123", invocation_id="invocation123",
        )
