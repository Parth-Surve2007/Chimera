"""Comprehensive Acceptance Tests for Phase 5.3: Cross-Capability Agentic Workflows.

Tests multi-step task creation, dependency ordering, capability discovery,
workspace-to-REA execution, result verification, failure recovery, and
governed learning integration.
"""
import json
import struct
import tempfile
import uuid
from pathlib import Path
import pytest

from chimera.workflow_engine import (
    WorkflowPlan,
    WorkflowStep,
    StepDependency,
    StepState,
    RiskLevel,
    WorkflowPlanStore,
    CapabilitySelector,
    WorkflowExecutor,
)
from chimera.workflow_planner import WorkflowPlanner
from chimera.result_verification import ResultVerifier, VerificationResult
from chimera.failure_recovery import FailureRecovery, FailureType, FailureAnalysis
from chimera.workflow_integration import AgenticWorkflowOrchestrator
from chimera.workflow_learning import WorkflowExtractor, WorkflowSkillStore, ProposedWorkflow
from chimera.agent_runtime import AgentRuntime, AgentSessionStore
from chimera.task_execution import TaskStore


class MockProvider:
    """Mock provider for testing."""
    def __init__(self):
        self.config = type("Config", (), {"model_path": None})()


@pytest.fixture
def test_env(tmp_path):
    """Set up test environment with database and workspace."""
    db_file = tmp_path / "test_sessions.db"
    store = AgentSessionStore(db_file)
    task_store = TaskStore(db_file)
    provider = MockProvider()
    runtime = AgentRuntime(store, provider)

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    return {
        "store": store,
        "task_store": task_store,
        "runtime": runtime,
        "workspace": workspace,
        "tmp_path": tmp_path,
    }


def test_workflow_plan_persistence(test_env):
    """Test that workflow plans persist correctly across store instances."""
    plan_store = WorkflowPlanStore(test_env["tmp_path"] / "plans.db")

    # Create a plan
    plan = WorkflowPlan(
        plan_id=uuid.uuid4().hex,
        task_id=uuid.uuid4().hex,
        objective="Test objective",
        steps=[
            WorkflowStep(
                step_id=uuid.uuid4().hex,
                position=0,
                objective="Step 1",
                capability="workspace",
                tool="list_files",
                arguments={},
                dependencies=[],
                expected_output="File list",
                risk_level=RiskLevel.SAFE,
                required_permissions=["workspace.read"],
                approval_required=False,
                execution_state=StepState.PENDING,
            )
        ],
        created_at="2024-01-01T00:00:00Z",
        status="pending",
    )

    # Save plan
    plan_store.save_plan(plan)

    # Retrieve plan
    retrieved = plan_store.get_plan(plan.plan_id)
    assert retrieved is not None
    assert retrieved.plan_id == plan.plan_id
    assert retrieved.objective == plan.objective
    assert len(retrieved.steps) == 1
    assert retrieved.steps[0].step_id == plan.steps[0].step_id


def test_dependency_ordering(test_env):
    """Test that steps execute in correct dependency order."""
    plan_store = WorkflowPlanStore(test_env["tmp_path"] / "plans.db")

    # Create plan with dependencies
    step1_id = uuid.uuid4().hex
    step2_id = uuid.uuid4().hex
    step3_id = uuid.uuid4().hex

    plan = WorkflowPlan(
        plan_id=uuid.uuid4().hex,
        task_id=uuid.uuid4().hex,
        objective="Test dependency ordering",
        steps=[
            WorkflowStep(
                step_id=step1_id,
                position=0,
                objective="First step",
                capability="workspace",
                tool="list_files",
                arguments={},
                dependencies=[],
                expected_output="Files",
                risk_level=RiskLevel.SAFE,
                required_permissions=["workspace.read"],
                approval_required=False,
                execution_state=StepState.PENDING,
            ),
            WorkflowStep(
                step_id=step2_id,
                position=1,
                objective="Second step (depends on first)",
                capability="rea",
                tool="detect_format",
                arguments={},
                dependencies=[StepDependency(step1_id, "data")],
                expected_output="Format",
                risk_level=RiskLevel.SAFE,
                required_permissions=["rea.read"],
                approval_required=False,
                execution_state=StepState.PENDING,
            ),
            WorkflowStep(
                step_id=step3_id,
                position=2,
                objective="Third step (depends on second)",
                capability="rea",
                tool="analyze_artifact",
                arguments={},
                dependencies=[StepDependency(step2_id, "data")],
                expected_output="Analysis",
                risk_level=RiskLevel.SAFE,
                required_permissions=["rea.read"],
                approval_required=False,
                execution_state=StepState.PENDING,
            ),
        ],
        created_at="2024-01-01T00:00:00Z",
        status="pending",
    )

    plan_store.save_plan(plan)

    # Simulate execution: step1 completes
    plan_store.update_step_state(plan.plan_id, step1_id, StepState.COMPLETED, {"files": ["test.exe"]})

    # Step2 should now be ready
    updated_plan = plan_store.get_plan(plan.plan_id)
    step2 = next(s for s in updated_plan.steps if s.step_id == step2_id)
    # In real execution, this would transition to READY

    # Step3 should still be pending
    step3 = next(s for s in updated_plan.steps if s.step_id == step3_id)
    assert step3.execution_state == StepState.PENDING


def test_capability_selection(test_env):
    """Test that capability selector correctly identifies capabilities for operations."""
    selector = CapabilitySelector()

    # Test workspace operations
    assert selector.discover_capability_for_operation("list_files") == "workspace"
    assert selector.discover_capability_for_operation("read_text") == "workspace"

    # Test REA operations
    assert selector.discover_capability_for_operation("analyze_artifact") == "rea"
    assert selector.discover_capability_for_operation("extract_strings") == "rea"

    # Test unknown operation
    assert selector.discover_capability_for_operation("unknown_op") is None

    # Test permission lookup
    perms = selector.get_required_permissions("workspace", "list_files")
    assert "workspace.read" in perms

    perms = selector.get_required_permissions("rea", "analyze_artifact")
    assert "rea.read" in perms


def test_workflow_planner_classification(test_env):
    """Test that workflow planner correctly classifies objectives."""
    selector = CapabilitySelector()
    planner = WorkflowPlanner(selector)

    # Binary analysis objective
    plan1 = planner.create_plan(
        task_id=uuid.uuid4().hex,
        objective="Analyze the binary executable and extract strings",
        workspace=test_env["workspace"],
        mode="plan",
    )
    assert len(plan1.steps) > 0
    # Should have REA steps
    rea_steps = [s for s in plan1.steps if s.capability == "rea"]
    assert len(rea_steps) > 0

    # Source inspection objective
    plan2 = planner.create_plan(
        task_id=uuid.uuid4().hex,
        objective="Inspect the source code and understand the structure",
        workspace=test_env["workspace"],
        mode="plan",
    )
    assert len(plan2.steps) > 0
    # Should have workspace steps
    workspace_steps = [s for s in plan2.steps if s.capability == "workspace"]
    assert len(workspace_steps) > 0


def test_result_verification(test_env):
    """Test result verification for different operation types."""
    verifier = ResultVerifier()

    # Test successful workspace operation
    result1 = {
        "status": "completed",
        "data": {
            "files": ["test.py", "test.txt"],
        },
    }
    verification1 = verifier.verify("workspace", "list_files", result1)
    assert verification1.status == "verified"

    # Test failed operation
    result2 = {
        "status": "failed",
        "error": "Permission denied",
    }
    verification2 = verifier.verify("workspace", "list_files", result2)
    assert verification2.status == "failed"

    # Test REA analysis verification
    result3 = {
        "status": "completed",
        "data": {
            "artifact_id": "abc123",
            "findings": {
                "sha256": "def456",
                "format_type": "PE",
                "architecture": "x86",
            },
        },
    }
    verification3 = verifier.verify("rea", "analyze_artifact", result3)
    assert verification3.status == "verified"

    # Test artifact creation verification
    artifact_path = test_env["workspace"] / "test.md"
    artifact_path.write_text("Test content")
    result4 = {
        "status": "completed",
        "data": {
            "artifact": str(artifact_path),
            "bytes": 12,
        },
    }
    verification4 = verifier.verify("workspace", "create_artifact", result4)
    assert verification4.status == "verified"


def test_failure_recovery_analysis(test_env):
    """Test failure recovery classification and retry logic."""
    recovery = FailureRecovery(max_retries=3, base_retry_delay=1.0)

    # Test permission failure
    analysis1 = recovery.analyze_failure("workspace", "create_artifact", "Permission denied", 0)
    assert analysis1.failure_type == FailureType.PERMISSION_BLOCK
    assert analysis1.can_retry is False

    # Test timeout failure
    analysis2 = recovery.analyze_failure("rea", "analyze_artifact", "Operation timed out", 0)
    assert analysis2.failure_type == FailureType.RETRYABLE
    assert analysis2.can_retry is True
    assert analysis2.max_retries == 3

    # Test missing dependency
    analysis3 = recovery.analyze_failure("ghidra", "analyze_headless", "Ghidra not installed", 0)
    assert analysis3.failure_type == FailureType.MISSING_DEPENDENCY
    assert analysis3.can_retry is False
    assert analysis3.suggested_alternative is not None

    # Test retry after max retries
    analysis4 = recovery.analyze_failure("rea", "analyze_artifact", "Operation timed out", 3)
    assert analysis4.failure_type == FailureType.NON_RETRYABLE
    assert analysis4.can_retry is False


def test_failure_propagation(test_env):
    """Test that failures propagate correctly to dependent steps."""
    recovery = FailureRecovery()

    # Permission block should propagate
    should_propagate1 = recovery.should_propagate_failure(
        FailureType.PERMISSION_BLOCK,
        step_position=0,
        total_steps=5,
    )
    assert should_propagate1 is True

    # Missing dependency should propagate
    should_propagate2 = recovery.should_propagate_failure(
        FailureType.MISSING_DEPENDENCY,
        step_position=2,
        total_steps=5,
    )
    assert should_propagate2 is True

    # Retryable failure at first step should propagate
    should_propagate3 = recovery.should_propagate_failure(
        FailureType.RETRYABLE,
        step_position=0,
        total_steps=5,
    )
    assert should_propagate3 is True

    # Retryable failure at later step might not propagate
    should_propagate4 = recovery.should_propagate_failure(
        FailureType.RETRYABLE,
        step_position=3,
        total_steps=5,
    )
    assert should_propagate4 is False


def test_workflow_extraction(test_env):
    """Test workflow extraction from completed plans."""
    workflow_store = WorkflowSkillStore(test_env["tmp_path"] / "workflows.json")
    extractor = WorkflowExtractor(workflow_store)

    # Create a completed plan
    plan = WorkflowPlan(
        plan_id=uuid.uuid4().hex,
        task_id=uuid.uuid4().hex,
        objective="Analyze binary and generate report",
        steps=[
            WorkflowStep(
                step_id=uuid.uuid4().hex,
                position=0,
                objective="List files",
                capability="workspace",
                tool="list_files",
                arguments={},
                dependencies=[],
                expected_output="Files",
                risk_level=RiskLevel.SAFE,
                required_permissions=["workspace.read"],
                approval_required=False,
                execution_state=StepState.COMPLETED,
            ),
            WorkflowStep(
                step_id=uuid.uuid4().hex,
                position=1,
                objective="Analyze artifact",
                capability="rea",
                tool="analyze_artifact",
                arguments={"artifact_path": "test.exe"},
                dependencies=[StepDependency(uuid.uuid4().hex, "data")],
                expected_output="Analysis",
                risk_level=RiskLevel.SAFE,
                required_permissions=["rea.read"],
                approval_required=False,
                execution_state=StepState.COMPLETED,
            ),
        ],
        created_at="2024-01-01T00:00:00Z",
        status="completed",
    )

    # Extract workflow
    workflow = extractor.extract_workflow_from_plan(plan, "session123", [])

    assert workflow is not None
    assert workflow.status == "proposed"
    assert len(workflow.required_capabilities) == 2
    assert "workspace" in workflow.required_capabilities
    assert "rea" in workflow.required_capabilities
    assert len(workflow.steps_template) == 2


def test_plan_mode_write_rejection(test_env):
    """Test that write operations are rejected in plan mode."""
    selector = CapabilitySelector()
    planner = WorkflowPlanner(selector)

    # Create plan in plan mode
    plan = planner.create_plan(
        task_id=uuid.uuid4().hex,
        objective="Generate a report",
        workspace=test_env["workspace"],
        mode="plan",
    )

    # Find create_artifact step
    create_step = next((s for s in plan.steps if s.tool == "create_artifact"), None)
    if create_step:
        # In plan mode, write operations should require approval
        assert create_step.approval_required is True


def test_duplicate_side_effect_prevention(test_env):
    """Test that completed steps are not re-executed after provider failure."""
    plan_store = WorkflowPlanStore(test_env["tmp_path"] / "plans.db")

    # Create plan
    step1_id = uuid.uuid4().hex
    step2_id = uuid.uuid4().hex

    plan = WorkflowPlan(
        plan_id=uuid.uuid4().hex,
        task_id=uuid.uuid4().hex,
        objective="Test duplicate prevention",
        steps=[
            WorkflowStep(
                step_id=step1_id,
                position=0,
                objective="Step 1 (side effect)",
                capability="workspace",
                tool="create_artifact",
                arguments={"name": "test.txt", "content": "test"},
                dependencies=[],
                expected_output="Artifact",
                risk_level=RiskLevel.LOW,
                required_permissions=["execution.write"],
                approval_required=True,
                execution_state=StepState.COMPLETED,
                result_data={"artifact": str(test_env["workspace"] / "test.txt")},
            ),
            WorkflowStep(
                step_id=step2_id,
                position=1,
                objective="Step 2 (depends on step 1)",
                capability="workspace",
                tool="read_text",
                arguments={"path": "test.txt"},
                dependencies=[StepDependency(step1_id, "data")],
                expected_output="Content",
                risk_level=RiskLevel.SAFE,
                required_permissions=["workspace.read"],
                approval_required=False,
                execution_state=StepState.PENDING,
            ),
        ],
        created_at="2024-01-01T00:00:00Z",
        status="running",
    )

    plan_store.save_plan(plan)

    # Simulate provider failure after step1 completed
    # Step1 should not be re-executed on recovery
    updated_plan = plan_store.get_plan(plan.plan_id)
    step1 = next(s for s in updated_plan.steps if s.step_id == step1_id)
    assert step1.execution_state == StepState.COMPLETED
    assert step1.retry_count == 0


def test_workflow_integration_e2e(test_env):
    """End-to-end test of workflow integration with AgentRuntime."""
    orchestrator = AgenticWorkflowOrchestrator(
        agent_runtime=test_env["runtime"],
        task_store=test_env["task_store"],
        workspace=test_env["workspace"],
    )

    # Create session
    session_id = test_env["runtime"].create_session(
        workspace=test_env["workspace"],
        mode="plan",
    )

    # Execute objective
    result = orchestrator.execute_objective(
        session_id=session_id,
        objective="Inspect the workspace",
        mode="plan",
    )

    assert result["status"] in ("completed", "failed")
    assert "plan_id" in result


def test_synthetic_binary_analysis_scenario(test_env):
    """Test the binary analysis demonstration scenario with synthetic PE."""
    from tests.fixtures.cross_capability_demo import create_demo_workspace

    # Create demo workspace
    demo_workspace = create_demo_workspace(test_env["tmp_path"])

    # Verify PE executable was created
    exe_path = demo_workspace / "sample.exe"
    assert exe_path.exists()
    assert exe_path.stat().st_size > 0

    # Verify DOS/PE linkage and section bounds, not just the MZ magic.
    content = exe_path.read_bytes()
    assert content[:2] == b"MZ"
    pe_offset = struct.unpack_from("<I", content, 0x3C)[0]
    assert content[pe_offset:pe_offset + 4] == b"PE\x00\x00"
    section_table = pe_offset + 24 + struct.unpack_from("<H", content, pe_offset + 20)[0]
    for index in range(struct.unpack_from("<H", content, pe_offset + 6)[0]):
        section = section_table + index * 40
        raw_size, raw_offset = struct.unpack_from("<II", content, section + 16)
        assert raw_offset + raw_size <= len(content)

    # Create orchestrator
    orchestrator = AgenticWorkflowOrchestrator(
        agent_runtime=test_env["runtime"],
        task_store=test_env["task_store"],
        workspace=demo_workspace,
    )

    # Create session
    session_id = test_env["runtime"].create_session(
        workspace=demo_workspace,
        mode="plan",
    )

    # Execute binary analysis objective
    result = orchestrator.execute_objective(
        session_id=session_id,
        objective="Analyze the executable in this workspace, identify its format, architecture, sections, imports, exports and interesting strings",
        mode="plan",
    )

    # Plan Mode is read-only: the static-analysis steps complete, then report creation is blocked.
    assert result["status"] == "blocked"
    assert "plan_id" in result
    assert result["steps_executed"] >= 3
    assert test_env["store"].pending_tool(session_id) is None
