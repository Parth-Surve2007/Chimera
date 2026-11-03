"""Multi-Capability Agentic Workflow Engine for Chimera Phase 5.3.

Implements structured planning, dependency management, capability selection,
execution orchestration, result verification, and failure recovery for multi-step
objectives that combine different capability domains.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StepState(Enum):
    """Explicit states for workflow step execution."""
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class RiskLevel(Enum):
    """Risk classification for workflow steps."""
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class StepDependency:
    """Dependency relationship between workflow steps."""
    step_id: str
    dependency_type: str  # "data", "order", "exclusive"


@dataclass
class WorkflowStep:
    """A single step in a multi-capability workflow."""
    step_id: str
    position: int
    objective: str
    capability: str  # e.g., "workspace", "rea", "ghidra", "computer"
    tool: str | None  # Specific tool within capability
    arguments: dict[str, Any]
    dependencies: list[StepDependency]
    expected_output: str | None
    risk_level: RiskLevel
    required_permissions: list[str]
    approval_required: bool
    execution_state: StepState = StepState.PENDING
    result_reference: str | None = None
    result_data: dict[str, Any] | None = None
    error: str | None = None
    verification_status: str = "unknown"  # "verified", "failed", "unknown"
    started_at: str | None = None
    completed_at: str | None = None
    retry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "position": self.position,
            "objective": self.objective,
            "capability": self.capability,
            "tool": self.tool,
            "arguments": self.arguments,
            "dependencies": [
                {"step_id": d.step_id, "dependency_type": d.dependency_type}
                for d in self.dependencies
            ],
            "expected_output": self.expected_output,
            "risk_level": self.risk_level.value,
            "required_permissions": self.required_permissions,
            "approval_required": self.approval_required,
            "execution_state": self.execution_state.value,
            "result_reference": self.result_reference,
            "result_data": self.result_data,
            "error": self.error,
            "verification_status": self.verification_status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "retry_count": self.retry_count,
        }


@dataclass
class WorkflowPlan:
    """A structured execution plan for a multi-step objective."""
    plan_id: str
    task_id: str
    objective: str
    steps: list[WorkflowStep]
    created_at: str
    status: str = "pending"  # "pending", "running", "completed", "failed", "cancelled"

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "task_id": self.task_id,
            "objective": self.objective,
            "steps": [step.to_dict() for step in self.steps],
            "created_at": self.created_at,
            "status": self.status,
        }


class WorkflowPlanStore:
    """Persistent storage for workflow plans and step state."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflow_plans (
                    plan_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    plan_data TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workflow_step_events (
                    event_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_workflow_plans_task ON workflow_plans(task_id);
                CREATE INDEX IF NOT EXISTS idx_workflow_step_events_plan ON workflow_step_events(plan_id, step_id);
                """
            )

    def save_plan(self, plan: WorkflowPlan) -> None:
        with sqlite3.connect(self.db_path) as db:
            plan_data = json.dumps(plan.to_dict(), separators=(",", ":"))
            now = _now()
            db.execute(
                """
                INSERT OR REPLACE INTO workflow_plans
                (plan_id, task_id, objective, plan_data, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (plan.plan_id, plan.task_id, plan.objective, plan_data, plan.status, plan.created_at, now),
            )

    def get_plan(self, plan_id: str) -> WorkflowPlan | None:
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT plan_data FROM workflow_plans WHERE plan_id=?",
                (plan_id,),
            ).fetchone()
        if not row:
            return None
        data = json.loads(row[0])
        steps = [
            WorkflowStep(
                step_id=s["step_id"],
                position=s["position"],
                objective=s["objective"],
                capability=s["capability"],
                tool=s.get("tool"),
                arguments=s["arguments"],
                dependencies=[
                    StepDependency(d["step_id"], d["dependency_type"])
                    for d in s["dependencies"]
                ],
                expected_output=s.get("expected_output"),
                risk_level=RiskLevel(s["risk_level"]),
                required_permissions=s["required_permissions"],
                approval_required=s["approval_required"],
                execution_state=StepState(s["execution_state"]),
                result_reference=s.get("result_reference"),
                result_data=s.get("result_data"),
                error=s.get("error"),
                verification_status=s.get("verification_status", "unknown"),
                started_at=s.get("started_at"),
                completed_at=s.get("completed_at"),
                retry_count=s.get("retry_count", 0),
            )
            for s in data["steps"]
        ]
        return WorkflowPlan(
            plan_id=data["plan_id"],
            task_id=data["task_id"],
            objective=data["objective"],
            steps=steps,
            created_at=data["created_at"],
            status=data["status"],
        )

    def update_plan_status(self, plan_id: str, status: str) -> None:
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "UPDATE workflow_plans SET status=?, updated_at=? WHERE plan_id=?",
                (status, _now(), plan_id),
            )

    def update_step_state(
        self,
        plan_id: str,
        step_id: str,
        state: StepState,
        result_data: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        plan = self.get_plan(plan_id)
        if not plan:
            raise KeyError("plan not found")
        step = next((s for s in plan.steps if s.step_id == step_id), None)
        if not step:
            raise KeyError("step not found")

        # Update step
        step.execution_state = state
        step.result_data = result_data
        step.error = error
        if state == StepState.RUNNING:
            step.started_at = _now()
        elif state in (StepState.COMPLETED, StepState.FAILED, StepState.CANCELLED):
            step.completed_at = _now()

        # Save updated plan
        self.save_plan(plan)

        # Record event
        self._record_event(plan_id, step_id, f"step.{state.value}", {
            "result": result_data,
            "error": error,
        })

    def _record_event(self, plan_id: str, step_id: str, event_type: str, payload: dict[str, Any]) -> None:
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO workflow_step_events
                (event_id, plan_id, step_id, event_type, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, plan_id, step_id, event_type, json.dumps(payload, separators=(",", ":")), _now()),
            )

    def get_step_events(self, plan_id: str, step_id: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute(
                """
                SELECT event_id, event_type, payload, created_at
                FROM workflow_step_events
                WHERE plan_id=? AND step_id=?
                ORDER BY created_at
                """,
                (plan_id, step_id),
            ).fetchall()
        return [
            {
                "event_id": row[0],
                "event_type": row[1],
                "payload": json.loads(row[2]),
                "created_at": row[3],
            }
            for row in rows
        ]


class CapabilitySelector:
    """Discovers and selects appropriate capabilities for planned operations."""

    def __init__(self):
        self._capabilities = {}
        self._initialize_capabilities()

    def _initialize_capabilities(self):
        """Register known capability domains."""
        # Workspace capabilities
        self._capabilities["workspace"] = {
            "operations": ["list_files", "inspect_file", "read_text", "search_files", "search_text", "create_artifact"],
            "permissions": ["workspace.read", "execution.write"],
        }
        # REA capabilities
        self._capabilities["rea"] = {
            "operations": ["analyze_artifact", "extract_strings", "detect_format", "analyze_unknown"],
            "permissions": ["rea.read"],
        }
        # Ghidra capabilities
        self._capabilities["ghidra"] = {
            "operations": ["extract_strings", "enumerate_functions", "analyze_headless"],
            "permissions": ["ghidra.read", "ghidra.write"],
        }
        # Computer capabilities
        self._capabilities["computer"] = {
            "operations": ["list_applications", "list_windows", "inspect_controls", "screenshot"],
            "permissions": ["computer.observe", "computer.input", "computer.screenshot"],
        }

    def discover_capability_for_operation(self, operation: str) -> str | None:
        """Identify which capability domain can perform an operation."""
        for capability, data in self._capabilities.items():
            if operation in data["operations"]:
                return capability
        return None

    def verify_capability_available(self, capability: str) -> bool:
        """Check if a capability is currently available."""
        if capability not in self._capabilities:
            return False
        # For now, assume all registered capabilities are available
        # In production, this would check actual connector status
        return True

    def get_required_permissions(self, capability: str, operation: str) -> list[str]:
        """Get permissions required for a specific operation."""
        if capability not in self._capabilities:
            return []
        data = self._capabilities[capability]
        if operation not in data["operations"]:
            return []
        return data["permissions"]


class WorkflowExecutor:
    """Orchestrates execution of multi-step workflow plans."""

    def __init__(
        self,
        plan_store: WorkflowPlanStore,
        capability_selector: CapabilitySelector,
        agent_runtime,
        approval_callback: Callable[[str, dict[str, Any]], bool] | None = None,
    ):
        self.plan_store = plan_store
        self.capability_selector = capability_selector
        self.agent_runtime = agent_runtime
        self.approval_callback = approval_callback
        self._executing_plans: dict[str, threading.Thread] = {}
        self._cancel_events: dict[str, threading.Event] = {}

    def execute_plan(self, plan: WorkflowPlan) -> str:
        """Execute a workflow plan asynchronously."""
        if plan.plan_id in self._executing_plans:
            raise ValueError("plan is already executing")

        cancel_event = threading.Event()
        self._cancel_events[plan.plan_id] = cancel_event

        def _execute():
            try:
                self._execute_plan_impl(plan, cancel_event)
            except Exception as exc:
                plan.status = "failed"
                self.plan_store.save_plan(plan)
                raise
            finally:
                self._executing_plans.pop(plan.plan_id, None)
                self._cancel_events.pop(plan.plan_id, None)

        thread = threading.Thread(target=_execute, daemon=True)
        thread.start()
        self._executing_plans[plan.plan_id] = thread

        return plan.plan_id

    def cancel_plan(self, plan_id: str) -> bool:
        """Cancel an executing workflow plan."""
        if plan_id not in self._cancel_events:
            return False
        self._cancel_events[plan_id].set()
        plan = self.plan_store.get_plan(plan_id)
        if plan:
            plan.status = "cancelled"
            self.plan_store.save_plan(plan)
        return True

    def _execute_plan_impl(self, plan: WorkflowPlan, cancel_event: threading.Event) -> None:
        """Synchronous implementation of plan execution."""
        plan.status = "running"
        self.plan_store.save_plan(plan)

        # Execute steps in dependency order
        while True:
            if cancel_event.is_set():
                plan.status = "cancelled"
                self.plan_store.save_plan(plan)
                return

            # Find next ready step
            ready_step = self._find_next_ready_step(plan)
            if not ready_step:
                # Check if all steps are completed
                states = {s.execution_state for s in plan.steps}
                if states and states.issubset({StepState.COMPLETED}):
                    plan.status = "completed"
                    self.plan_store.save_plan(plan)
                    return
                if StepState.AWAITING_APPROVAL in states:
                    plan.status = "awaiting_approval"
                    self.plan_store.save_plan(plan)
                    return
                if StepState.FAILED in states:
                    plan.status = "failed"
                    self.plan_store.save_plan(plan)
                    return
                if StepState.CANCELLED in states:
                    plan.status = "cancelled"
                    self.plan_store.save_plan(plan)
                    return
                # No ready steps but not all done - blocked
                plan.status = "blocked"
                self.plan_store.save_plan(plan)
                return

            # Execute the step
            self._execute_step(plan, ready_step, cancel_event)

    def _find_next_ready_step(self, plan: WorkflowPlan) -> WorkflowStep | None:
        """Find a step whose dependencies are satisfied and is ready to execute."""
        for step in plan.steps:
            if step.execution_state != StepState.PENDING:
                continue

            # Check dependencies
            dependencies_satisfied = True
            for dep in step.dependencies:
                dep_step = next((s for s in plan.steps if s.step_id == dep.step_id), None)
                if not dep_step or dep_step.execution_state != StepState.COMPLETED:
                    dependencies_satisfied = False
                    break

            if dependencies_satisfied:
                step.execution_state = StepState.READY
                self.plan_store.save_plan(plan)
                return step

        return None

    def _execute_step(self, plan: WorkflowPlan, step: WorkflowStep, cancel_event: threading.Event) -> None:
        """Execute a single workflow step."""
        # Update state to running
        step.execution_state = StepState.RUNNING
        self.plan_store.save_plan(plan)

        try:
            # Check approval requirement
            if step.approval_required:
                if self.approval_callback:
                    approved = self.approval_callback(step.step_id, step.to_dict())
                    if not approved:
                        step.execution_state = StepState.FAILED
                        step.error = "approval denied"
                        self.plan_store.save_plan(plan)
                        return
                else:
                    step.execution_state = StepState.AWAITING_APPROVAL
                    self.plan_store.save_plan(plan)
                    # In real implementation, this would wait for external approval
                    # For now, we mark as failed
                    step.execution_state = StepState.FAILED
                    step.error = "approval required but no callback available"
                    self.plan_store.save_plan(plan)
                    return

            # Execute the operation through AgentRuntime
            result = self._execute_operation(step)

            # Update step with result
            step.execution_state = StepState.COMPLETED
            step.result_data = result
            step.verification_status = self._verify_result(step, result)
            self.plan_store.save_plan(plan)

        except Exception as exc:
            step.execution_state = StepState.FAILED
            step.error = str(exc)
            self.plan_store.save_plan(plan)

    def _execute_operation(self, step: WorkflowStep) -> dict[str, Any]:
        """Fail closed when no real AgentRuntime dispatcher is configured."""
        raise NotImplementedError(
            "No direct workflow execution backend exists. "
            "Use AgenticWorkflowOrchestrator and AgentRuntime."
        )

    def _verify_result(self, step: WorkflowStep, result: dict[str, Any]) -> str:
        """Verify that the result meets expectations."""
        if not result or result.get("status") != "completed":
            return "failed"

        # Basic verification based on capability
        if step.capability == "workspace" and step.tool == "create_artifact":
            # Verify file was created
            if "artifact" in result.get("data", {}):
                return "verified"
            return "failed"

        if step.capability == "rea" and step.tool == "analyze_artifact":
            # Verify analysis data is present
            if "findings" in result.get("data", {}):
                return "verified"
            return "failed"

        # Default to unknown if no specific verification
        return "unknown"
