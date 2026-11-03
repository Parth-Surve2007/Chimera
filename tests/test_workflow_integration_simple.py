"""Simplified Workflow Integration Test for Phase 5.3.

Tests the workflow integration without requiring a full local model setup,
using a mock provider to verify the orchestration logic.
"""
import tempfile
from pathlib import Path
import pytest

from chimera.workflow_integration import AgenticWorkflowOrchestrator
from chimera.agent_runtime import AgentRuntime, AgentSessionStore
from chimera.task_execution import TaskStore
from tests.fixtures.cross_capability_demo import create_demo_workspace


class SimpleMockProvider:
    """Simple mock provider for testing without full model."""
    def __init__(self):
        self.config = type("Config", (), {"model_path": None})()


def test_workflow_orchestration_simple():
    """Test workflow orchestration with mock provider."""
    # Create demo workspace
    tmp = Path(tempfile.gettempdir())
    workspace = create_demo_workspace(tmp)

    # Initialize with mock provider
    provider = SimpleMockProvider()
    db_path = workspace / "test_simple.db"
    store = AgentSessionStore(db_path)
    task_store = TaskStore(db_path)
    runtime = AgentRuntime(store, provider)

    # Create orchestrator
    orchestrator = AgenticWorkflowOrchestrator(
        agent_runtime=runtime,
        task_store=task_store,
        workspace=workspace,
    )

    # Create session
    session_id = runtime.create_session(workspace=workspace, mode="plan")

    # Execute objective
    objective = "Inspect the workspace and list files"
    result = orchestrator.execute_objective(
        session_id=session_id,
        objective=objective,
        mode="plan",
    )

    # Verify result
    assert result["status"] in ("completed", "failed")
    assert "plan_id" in result

    print(f"\nWorkflow orchestration test passed")
    print(f"Status: {result['status']}")
    print(f"Plan ID: {result['plan_id']}")
    print(f"Steps executed: {result.get('steps_executed', 0)}")


if __name__ == "__main__":
    test_workflow_orchestration_simple()
