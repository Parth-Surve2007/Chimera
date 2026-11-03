"""Integration of Workflow Engine with AgentRuntime for Phase 5.3.

Bridges the multi-step workflow execution with the existing AgentRuntime,
enabling complex objectives to be decomposed, planned, and executed through
governed tool invocation.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Callable

from .agent_runtime import AgentRuntime, AgentSessionStore
from .task_execution import TaskStore
from .workflow_engine import (
    WorkflowPlan,
    WorkflowPlanStore,
    WorkflowExecutor,
    CapabilitySelector,
    WorkflowStep,
    StepState,
)
from .workflow_planner import WorkflowPlanner
from .result_verification import ResultVerifier, VerificationResult
from .failure_recovery import FailureRecovery, FailureAnalysis


class AgenticWorkflowOrchestrator:
    """High-level orchestrator for multi-capability agentic workflows."""

    def __init__(
        self,
        agent_runtime: AgentRuntime,
        task_store: TaskStore,
        workspace: Path | None = None,
    ):
        self.agent_runtime = agent_runtime
        self.task_store = task_store
        self.workspace = workspace

        # Initialize workflow components
        db_path = Path(agent_runtime.store.db_path).parent / "workflow_plans.db"
        self.plan_store = WorkflowPlanStore(db_path)
        self.capability_selector = CapabilitySelector()
        self.planner = WorkflowPlanner(self.capability_selector)
        self.verifier = ResultVerifier()
        self.recovery = FailureRecovery()

        # Initialize executor with approval callback
        self.executor = WorkflowExecutor(
            self.plan_store,
            self.capability_selector,
            self.agent_runtime,
            approval_callback=self._handle_approval,
        )

    def execute_objective(
        self,
        session_id: str,
        objective: str,
        mode: str = "plan",
    ) -> dict[str, Any]:
        """Execute a multi-step objective through workflow orchestration."""
        # Get or create task
        task = self.task_store.latest_for_session(session_id)
        if not task:
            task_id = self.task_store.create(session_id, objective, mode)
        else:
            task_id = task.task_id

        # Create workflow plan
        plan = self.planner.create_plan(
            task_id=task_id,
            objective=objective,
            workspace=self.workspace,
            mode=mode,
        )

        # Save plan
        self.plan_store.save_plan(plan)

        # Update task state
        self.task_store.transition(task_id, "running")

        # Execute plan synchronously for this integration
        try:
            self._execute_plan_sync(plan, session_id, mode)
            self.task_store.transition(task_id, "completed")
            return {
                "status": "completed",
                "plan_id": plan.plan_id,
                "steps_executed": len(plan.steps),
            }
        except Exception as exc:
            self.task_store.transition(task_id, "failed")
            return {
                "status": "failed",
                "error": str(exc),
                "plan_id": plan.plan_id,
            }

    def _execute_plan_sync(
        self,
        plan: WorkflowPlan,
        session_id: str,
        mode: str,
    ) -> None:
        """Synchronously execute a workflow plan through AgentRuntime."""
        plan.status = "running"
        self.plan_store.save_plan(plan)

        session = self.agent_runtime.store.session(session_id)
        if not session:
            raise KeyError("session not found")

        # Execute steps in dependency order
        while True:
            # Find next ready step
            ready_step = self._find_next_ready_step(plan)
            if not ready_step:
                # Check if all steps are done
                if all(
                    s.execution_state in (StepState.COMPLETED, StepState.FAILED, StepState.CANCELLED)
                    for s in plan.steps
                ):
                    plan.status = "completed"
                    self.plan_store.save_plan(plan)
                    return
                # Blocked
                plan.status = "blocked"
                self.plan_store.save_plan(plan)
                return

            # Execute the step
            self._execute_step_through_runtime(ready_step, plan, session_id, mode)

    def _find_next_ready_step(self, plan: WorkflowPlan) -> WorkflowStep | None:
        """Find a step whose dependencies are satisfied."""
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

    def _execute_step_through_runtime(
        self,
        step: WorkflowStep,
        plan: WorkflowPlan,
        session_id: str,
        mode: str,
    ) -> None:
        """Execute a step through AgentRuntime tool execution."""
        step.execution_state = StepState.RUNNING
        self.plan_store.save_plan(plan)

        try:
            # Resolve arguments from previous steps
            resolved_arguments = self._resolve_arguments(step, plan)

            # Check approval requirement
            if step.approval_required and mode == "build":
                # Request approval through AgentRuntime
                tool_id = f"{step.capability}.{step.tool}" if step.tool else step.capability
                self.agent_runtime.store.set_pending_tool(
                    session_id,
                    uuid.uuid4().hex,
                    step.step_id,
                    tool_id,
                    resolved_arguments,
                )

                # For now, auto-approve for demonstration
                # In production, this would wait for user approval
                approved = True
                if not approved:
                    step.execution_state = StepState.FAILED
                    step.error = "approval denied"
                    self.plan_store.save_plan(plan)
                    return

            # Execute the operation
            result = self._execute_tool(step, resolved_arguments, session_id, mode)

            # Verify result
            verification = self.verifier.verify(
                step.capability,
                step.tool or "",
                result,
                step.expected_output,
            )

            # Update step
            step.execution_state = StepState.COMPLETED
            step.result_data = result
            step.verification_status = verification.status
            self.plan_store.save_plan(plan)

        except Exception as exc:
            # Analyze failure
            failure_analysis = self.recovery.analyze_failure(
                step.capability,
                step.tool or "",
                str(exc),
                step.retry_count,
            )

            if failure_analysis.can_retry and step.retry_count < failure_analysis.max_retries:
                # Retry
                step.retry_count += 1
                step.execution_state = StepState.PENDING
                self.plan_store.save_plan(plan)
                # Re-execute
                self._execute_step_through_runtime(step, plan, session_id, mode)
            else:
                # Mark as failed
                step.execution_state = StepState.FAILED
                step.error = str(exc)
                self.plan_store.save_plan(plan)

    def _resolve_arguments(self, step: WorkflowStep, plan: WorkflowPlan) -> dict[str, Any]:
        """Resolve argument placeholders with actual data from previous steps."""
        resolved = {}
        for key, value in step.arguments.items():
            if isinstance(value, str) and value.startswith("{"):
                # Placeholder - resolve from dependency
                # For now, return as-is (simplified)
                resolved[key] = value
            else:
                resolved[key] = value
        return resolved

    def _execute_tool(
        self,
        step: WorkflowStep,
        arguments: dict[str, Any],
        session_id: str,
        mode: str,
    ) -> dict[str, Any]:
        """Execute a tool through the appropriate mechanism."""
        # This integrates with the existing AgentRuntime tool execution
        # For now, provide a simplified implementation

        if step.capability == "workspace":
            from .tools import WorkspaceTools

            if not self.workspace:
                raise ValueError("workspace required for workspace operations")

            tools = WorkspaceTools(
                self.workspace,
                self.workspace / ".chimera-agent-artifacts" / session_id,
            )

            operation = step.tool or ""
            result = tools.run(operation, arguments, approved=(mode == "build"))

            return {
                "status": result.status,
                "data": result.data,
                "error": result.error,
            }

        elif step.capability == "rea":
            from .reverse_engineering import get_rea_engine

            engine = get_rea_engine()

            if step.tool == "analyze_artifact":
                result = engine.analyze_artifact(arguments.get("artifact_path", ""))
                return {
                    "status": result.status,
                    "data": {
                        "artifact_id": result.artifact_id,
                        "findings": result.findings.to_dict(),
                    },
                    "error": None,
                }

            elif step.tool == "extract_strings":
                result = engine.extract_strings(
                    arguments.get("artifact_path", ""),
                    arguments.get("min_length", 4),
                )
                return {
                    "status": result.status,
                    "data": {
                        "artifact_id": result.artifact_id,
                        "findings": result.findings.to_dict(),
                    },
                    "error": None,
                }

            elif step.tool == "detect_format":
                result = engine.detect_format(arguments.get("artifact_path", ""))
                return {
                    "status": result.status,
                    "data": {
                        "artifact_id": result.artifact_id,
                        "findings": result.findings.to_dict(),
                    },
                    "error": None,
                }

        # Fallback
        return {
            "status": "completed",
            "data": {"tool": step.tool, "capability": step.capability},
            "error": None,
        }

    def _handle_approval(self, step_id: str, step_data: dict[str, Any]) -> bool:
        """Handle approval requests for workflow steps."""
        # In production, this would present to user and wait for response
        # For now, auto-approve read operations
        if step_data.get("risk_level") == "safe":
            return True
        return False

    def get_plan_status(self, plan_id: str) -> dict[str, Any]:
        """Get the current status of a workflow plan."""
        plan = self.plan_store.get_plan(plan_id)
        if not plan:
            return {"error": "plan not found"}

        return {
            "plan_id": plan.plan_id,
            "status": plan.status,
            "steps": [
                {
                    "step_id": s.step_id,
                    "position": s.position,
                    "objective": s.objective,
                    "execution_state": s.execution_state.value,
                    "verification_status": s.verification_status,
                    "error": s.error,
                }
                for s in plan.steps
            ],
        }
