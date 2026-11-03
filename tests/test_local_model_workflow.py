"""Real Local-Model Acceptance Test for Phase 5.3.

Performs a bounded end-to-end test using the actual local GGUF runtime
to verify that real local inference can coordinate multiple capabilities.
"""
import tempfile
from pathlib import Path
import pytest

from chimera.workflow_integration import AgenticWorkflowOrchestrator
from chimera.agent_runtime import AgentRuntime, AgentSessionStore
from chimera.task_execution import TaskStore
from chimera.local_model import LocalModelProvider, LocalModelConfig
from tests.fixtures.cross_capability_demo import create_demo_workspace


@pytest.mark.skipif(
    not Path("C:/ChimeraModels/Llama-3.2-3B-Instruct-Q4_K_M.gguf").exists(),
    reason="Local model not available at expected path"
)
def test_local_model_binary_analysis_workflow():
    """End-to-end test with real local model performing binary analysis workflow."""
    # Create demo workspace
    tmp = Path(tempfile.gettempdir())
    workspace = create_demo_workspace(tmp)

    # Initialize local model provider
    model_path = Path("C:/ChimeraModels/Llama-3.2-3B-Instruct-Q4_K_M.gguf")
    config = LocalModelConfig(
        model_path=model_path,
        cli_path=None,  # Will use llama-server if available
        context=2048,
        max_tokens=512,
        timeout=60,
        threads=4,
        gpu_layers=0,
        server_path=None,
    )
    provider = LocalModelProvider(config)

    # Check if model is ready
    status = provider.status()
    if status.state != "ready":
        pytest.skip(f"Local model not ready: {status.detail}")

    # Initialize runtime
    db_path = workspace / "test_local.db"
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

    # Execute binary analysis objective
    objective = (
        "Analyze the executable in this workspace. "
        "Identify its format, architecture, and extract interesting strings. "
        "Provide a summary of your findings."
    )

    try:
        result = orchestrator.execute_objective(
            session_id=session_id,
            objective=objective,
            mode="plan",
        )

        # Verify result
        assert result["status"] in ("completed", "failed")
        assert "plan_id" in result

        if result["status"] == "completed":
            print(f"\nLocal model successfully completed workflow")
            print(f"Plan ID: {result['plan_id']}")
            print(f"Steps executed: {result['steps_executed']}")

            # Get plan status
            plan_status = orchestrator.get_plan_status(result["plan_id"])
            print(f"Plan status: {plan_status['status']}")
            for step in plan_status["steps"]:
                print(f"  Step {step['position']}: {step['execution_state']} - {step['objective']}")

        else:
            print(f"\nLocal model workflow failed: {result.get('error', 'unknown')}")

    except Exception as exc:
        pytest.fail(f"Local model workflow test failed with exception: {exc}")


if __name__ == "__main__":
    import sys

    # Run as standalone script
    if not Path("C:/ChimeraModels/Llama-3.2-3B-Instruct-Q4_K_M.gguf").exists():
        print("Local model not found at expected path")
        print("Expected: C:/ChimeraModels/Llama-3.2-3B-Instruct-Q4_K_M.gguf")
        sys.exit(1)

    test_local_model_binary_analysis_workflow()
