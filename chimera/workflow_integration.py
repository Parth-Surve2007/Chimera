"""Governed multi-capability workflow orchestration.

Every workflow operation is dispatched through AgentRuntime. The orchestrator
never calls connector handlers or workspace tools directly.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent_runtime import AgentRuntime, _now
from .task_execution import TaskStore
from .workflow_engine import WorkflowPlan, WorkflowPlanStore, CapabilitySelector, WorkflowStep, StepState
from .workflow_planner import WorkflowPlanner
from .result_verification import ResultVerifier
from .failure_recovery import FailureRecovery


class AgenticWorkflowOrchestrator:
    """Execute persisted dependency plans using AgentRuntime's governed dispatcher."""

    BINARY_EXTENSIONS = {
        ".exe", ".dll", ".so", ".dylib", ".elf", ".bin", ".o", ".a",
        ".lib", ".apk", ".dex", ".macho", ".zip", ".7z", ".tar", ".gz",
    }

    def __init__(self, agent_runtime: AgentRuntime, task_store: TaskStore, workspace: Path | None = None):
        self.agent_runtime = agent_runtime
        self.task_store = task_store
        self.workspace = Path(workspace).resolve() if workspace else None
        db_path = Path(agent_runtime.store.db_path).parent / "workflow_plans.db"
        self.plan_store = WorkflowPlanStore(db_path)
        self.capability_selector = CapabilitySelector()
        self.planner = WorkflowPlanner(self.capability_selector)
        self.verifier = ResultVerifier()
        self.recovery = FailureRecovery(max_retries=2, base_retry_delay=0.5)

    def execute_objective(self, session_id: str, objective: str, mode: str | None = None) -> dict[str, Any]:
        session = self.agent_runtime.store.session(session_id)
        if not session:
            raise KeyError("agent session not found")
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("workflow objective must be non-empty")
        if self.agent_runtime.store.pending_tool(session_id):
            raise RuntimeError("this session already has an action awaiting approval")

        session_workspace = Path(session["workspace"]).resolve() if session.get("workspace") else None
        if self.workspace is None:
            self.workspace = session_workspace
        if self.workspace is None or not self.workspace.is_dir() or session_workspace != self.workspace:
            raise ValueError("workflow requires the session's existing, validated workspace")
        active_mode = session.get("mode", "plan")
        if mode is not None and mode != active_mode:
            raise ValueError("workflow mode must match the active session mode")

        task_id = self.task_store.create(
            session_id, objective.strip(), mode=active_mode,
            provider=None, model=session.get("model"),
        )
        self.task_store.transition(task_id, "running")
        self.agent_runtime._tasks[session_id] = task_id
        plan = self.planner.create_plan(
            task_id=task_id, objective=objective.strip(),
            workspace=self.workspace, mode=active_mode,
        )
        self.plan_store.save_plan(plan)
        self.agent_runtime._record(session_id, "workflow.created", {
            "plan_id": plan.plan_id, "task_id": task_id, "objective": objective.strip(),
        })

        status = self._execute_plan_sync(plan, session_id)
        self._finish_task(session_id, task_id, status)
        return {
            "status": status, "plan_id": plan.plan_id, "task_id": task_id,
            "steps_executed": sum(step.execution_state == StepState.COMPLETED for step in plan.steps),
            "steps_total": len(plan.steps),
            "pending_step": next((step.step_id for step in plan.steps if step.execution_state == StepState.AWAITING_APPROVAL), None),
            "step_results": [
                {
                    "step_id": step.step_id,
                    "position": step.position,
                    "tool": step.tool,
                    "status": step.execution_state.value,
                    "error": step.error,
                    "verification_status": step.verification_status,
                }
                for step in plan.steps
            ],
        }

    def _finish_task(self, session_id: str, task_id: str, status: str) -> None:
        task_state = {
            "completed": "completed",
            "failed": "failed",
            "blocked": "blocked",
            "cancelled": "cancelled",
            "awaiting_approval": "waiting_approval",
        }.get(status, "blocked")
        self.task_store.transition(task_id, task_state)
        if status != "awaiting_approval":
            session_state = "idle" if status in {"completed", "blocked"} else status
            with sqlite3.connect(self.agent_runtime.store.db_path) as db:
                db.execute("UPDATE agent_sessions SET state=?,updated_at=? WHERE session_id=?", (session_state, _now(), session_id))
            self.agent_runtime._tasks.pop(session_id, None)

    def _execute_plan_sync(self, plan: WorkflowPlan, session_id: str) -> str:
        plan.status = "running"
        self.plan_store.save_plan(plan)
        while True:
            if session_id in self.agent_runtime._cancelled:
                self.agent_runtime._cancelled.discard(session_id)
                plan.status = "cancelled"
                self.plan_store.save_plan(plan)
                return "cancelled"
            step = self._find_next_ready_step(plan)
            if step is None:
                states = {item.execution_state for item in plan.steps}
                if states and states.issubset({StepState.COMPLETED}):
                    plan.status = "completed"
                    self.plan_store.save_plan(plan)
                    return "completed"
                if StepState.AWAITING_APPROVAL in states:
                    plan.status = "awaiting_approval"
                    self.plan_store.save_plan(plan)
                    return "awaiting_approval"
                if StepState.FAILED in states:
                    plan.status = "failed"
                    self.plan_store.save_plan(plan)
                    return "failed"
                if StepState.CANCELLED in states:
                    plan.status = "cancelled"
                    self.plan_store.save_plan(plan)
                    return "cancelled"
                plan.status = "blocked"
                self.plan_store.save_plan(plan)
                return "blocked"
            self._execute_step(step, plan, session_id)

    def _find_next_ready_step(self, plan: WorkflowPlan) -> WorkflowStep | None:
        by_id = {step.step_id: step for step in plan.steps}
        for step in plan.steps:
            if step.execution_state != StepState.PENDING:
                continue
            dependencies = [by_id.get(dep.step_id) for dep in step.dependencies]
            if any(dep is None for dep in dependencies):
                step.execution_state = StepState.BLOCKED
                step.error = "workflow dependency references an unknown step"
                self.plan_store.save_plan(plan)
                continue
            failed = next((dep for dep in dependencies if dep.execution_state in {StepState.FAILED, StepState.BLOCKED, StepState.CANCELLED}), None)
            if failed:
                step.execution_state = StepState.BLOCKED
                step.error = f"dependency {failed.step_id} did not complete"
                self.plan_store.save_plan(plan)
                continue
            if all(dep.execution_state == StepState.COMPLETED for dep in dependencies):
                step.execution_state = StepState.READY
                self.plan_store.save_plan(plan)
                return step
        return None

    @staticmethod
    def _tool_id(step: WorkflowStep) -> str:
        if not step.tool:
            raise ValueError("workflow step has no registered tool")
        if step.capability == "workspace":
            return "workspace." + step.tool
        if step.capability in {"rea", "ghidra"}:
            return f"application.{step.capability}.{step.tool}"
        raise ValueError(f"unsupported workflow capability: {step.capability}")

    def _execute_step(self, step: WorkflowStep, plan: WorkflowPlan, session_id: str) -> None:
        step.execution_state = StepState.RUNNING
        self.plan_store.save_plan(plan)
        try:
            args = self._resolve_arguments(step, plan)
            tool_id = self._tool_id(step)
            invocation_id = f"wf:{plan.plan_id}:{step.step_id}:{uuid.uuid4().hex}"
            result = self.agent_runtime.execute_workflow_tool(
                session_id, tool_id, args, plan_id=plan.plan_id,
                invocation_id=invocation_id, approved=False,
            )
            if result.get("status") == "awaiting_approval":
                step.execution_state = StepState.AWAITING_APPROVAL
                step.result_data = {"tool": tool_id, "arguments": args, "invocation_id": invocation_id}
                plan.status = "awaiting_approval"
                self.plan_store.save_plan(plan)
                return
            if result.get("status") == "blocked":
                step.execution_state = StepState.BLOCKED
                step.error = result.get("error", "action blocked by policy")
                self.plan_store.save_plan(plan)
                return
            if result.get("status") != "completed":
                raise RuntimeError(result.get("error") or f"tool returned {result.get('status')}")
            verification = self.verifier.verify(step.capability, step.tool or "", result, step.expected_output)
            if verification.status == "failed":
                raise RuntimeError(verification.reason or "result verification failed")
            step.execution_state = StepState.COMPLETED
            step.result_data = result
            step.verification_status = verification.status
            step.completed_at = datetime.now(timezone.utc).isoformat()
        except InterruptedError as exc:
            step.execution_state = StepState.CANCELLED
            step.error = str(exc)
        except Exception as exc:
            failure = self.recovery.analyze_failure(step.capability, step.tool or "", str(exc), step.retry_count)
            read_only = (
                step.capability == "workspace"
                and step.tool in {"list_files", "inspect_file", "search_files", "search_text", "read_text"}
            ) or step.capability in {"rea", "ghidra"}
            if read_only and failure.can_retry and step.retry_count < failure.max_retries:
                step.retry_count += 1
                step.execution_state = StepState.PENDING
                step.error = str(exc)
            else:
                step.execution_state = StepState.FAILED
                step.error = str(exc)
        self.plan_store.save_plan(plan)

    def _resolve_arguments(self, step: WorkflowStep, plan: WorkflowPlan) -> dict[str, Any]:
        """Resolve supported placeholders from actual completed dependency results."""
        resolved: dict[str, Any] = {}
        for key, value in step.arguments.items():
            if not isinstance(value, str) or not (value.startswith("{") and value.endswith("}")):
                resolved[key] = value
                continue
            if key == "artifact_path" and value == "{from_step_1}":
                discovery = next((item for item in plan.steps if item.position == 0), None)
                if not discovery or discovery.execution_state != StepState.COMPLETED or not discovery.result_data:
                    raise ValueError("workspace discovery has no completed result")
                files = discovery.result_data.get("data", {}).get("files", [])
                candidates = [name for name in files if isinstance(name, str) and Path(name).suffix.casefold() in self.BINARY_EXTENSIONS]
                if len(candidates) != 1:
                    raise ValueError(f"expected exactly one supported binary artifact; found {len(candidates)}")
                target = (self.workspace / candidates[0]).resolve()
                if target != self.workspace and self.workspace not in target.parents:
                    raise ValueError("discovered artifact escapes the selected workspace")
                if not target.is_file():
                    raise ValueError("discovered artifact no longer exists")
                resolved[key] = str(target)
                continue
            if key == "content" and value == "{from_steps_3_4}":
                source_steps = [item for item in plan.steps if item.position in {2, 3}]
                if len(source_steps) != 2 or any(item.execution_state != StepState.COMPLETED or not item.result_data for item in source_steps):
                    raise ValueError("analysis results required for report generation are unavailable")
                sections = ["# Binary Analysis Report", "", f"Objective: {plan.objective}", ""]
                for item in source_steps:
                    payload = item.result_data.get("data", item.result_data)
                    sections.extend([f"## {item.objective}", "", json.dumps(payload, indent=2, sort_keys=True)[:50000], ""])
                resolved[key] = "\n".join(sections)
                continue
            raise ValueError(f"unsupported or unresolved workflow placeholder for {key}: {value}")
        return resolved

    def approve_pending(self, session_id: str, approved: bool) -> dict[str, Any]:
        pending = self.agent_runtime.store.pending_tool(session_id)
        if not pending:
            raise ValueError("no pending workflow approval")
        match = re.fullmatch(r"wf:([0-9a-f]+):([0-9a-f]+):([0-9a-f]+)", pending.get("invocation_id", ""))
        if not match:
            raise ValueError("pending action does not belong to a workflow")
        plan_id, step_id, _nonce = match.groups()
        plan = self.plan_store.get_plan(plan_id)
        if plan is None:
            raise KeyError("workflow plan not found")
        step = next((item for item in plan.steps if item.step_id == step_id), None)
        if step is None or step.execution_state != StepState.AWAITING_APPROVAL:
            raise ValueError("workflow step is no longer awaiting approval")
        self.agent_runtime._tasks[session_id] = plan.task_id

        if not approved:
            self.agent_runtime.store.clear_pending_tool(session_id)
            self.agent_runtime.store.add_message(session_id, "tool", json.dumps({
                "invocation_id": pending["invocation_id"], "tool": pending["tool"],
                "status": "denied", "error": "operator denied workflow action",
            }, separators=(",", ":")))
            self.agent_runtime._record(session_id, "tool.permission.denied", {
                "turn_id": plan_id, "invocation_id": pending["invocation_id"],
                "tool": pending["tool"], "source": "operator",
            })
            step.execution_state = StepState.BLOCKED
            step.error = "operator denied workflow action"
            plan.status = "blocked"
            self.plan_store.save_plan(plan)
            status = "blocked"
        else:
            result = self.agent_runtime.execute_workflow_tool(
                session_id, pending["tool"], pending["arguments"],
                plan_id=plan_id, invocation_id=pending["invocation_id"], approved=True,
            )
            if result.get("status") == "completed":
                verification = self.verifier.verify(step.capability, step.tool or "", result, step.expected_output)
                if verification.status == "failed":
                    step.execution_state = StepState.FAILED
                    step.error = verification.reason or "approved step failed verification"
                else:
                    step.execution_state = StepState.COMPLETED
                    step.result_data = result
                    step.verification_status = verification.status
                    step.completed_at = datetime.now(timezone.utc).isoformat()
            else:
                step.execution_state = StepState.BLOCKED if result.get("status") == "blocked" else StepState.FAILED
                step.error = result.get("error") or f"approved action returned {result.get('status')}"
            self.plan_store.save_plan(plan)
            status = self._execute_plan_sync(plan, session_id)

        self._finish_task(session_id, plan.task_id, status)
        self.agent_runtime._record(session_id, "workflow.resumed", {
            "plan_id": plan_id, "step_id": step_id, "approved": approved, "status": status,
        })
        return {
            "status": status, "plan_id": plan_id, "task_id": plan.task_id,
            "steps_executed": sum(item.execution_state == StepState.COMPLETED for item in plan.steps),
            "steps_total": len(plan.steps),
        }

    def get_plan_status(self, plan_id: str) -> dict[str, Any]:
        plan = self.plan_store.get_plan(plan_id)
        if not plan:
            return {"error": "plan not found"}
        return {
            "plan_id": plan.plan_id, "task_id": plan.task_id,
            "status": plan.status, "objective": plan.objective,
            "steps": [{
                "step_id": step.step_id, "position": step.position,
                "objective": step.objective, "tool": step.tool,
                "execution_state": step.execution_state.value,
                "verification_status": step.verification_status, "error": step.error,
            } for step in plan.steps],
        }
