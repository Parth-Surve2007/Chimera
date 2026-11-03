"""Governed Learning Integration for Workflow Extraction (Phase 5.3).

Extends the ExperienceLearningEngine to extract reusable workflows from
successful multi-step task execution, with operator approval gating.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .learning import SkillStore, ProposedSkill, ExperienceLearningEngine
from .workflow_engine import WorkflowPlan, WorkflowStep


@dataclass
class ProposedWorkflow:
    """A reusable workflow extracted from successful task execution."""
    workflow_id: str
    name: str
    description: str
    version: str
    source_task_id: str
    source_session_id: str
    objective_pattern: str  # Regex or keyword pattern for matching similar objectives
    required_capabilities: list[str]
    steps_template: list[dict[str, Any]]
    required_permissions: list[str]
    estimated_duration_seconds: int
    status: str = "proposed"  # "proposed", "approved", "rejected", "installed"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    usage_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "source_task_id": self.source_task_id,
            "source_session_id": self.source_session_id,
            "objective_pattern": self.objective_pattern,
            "required_capabilities": self.required_capabilities,
            "steps_template": self.steps_template,
            "required_permissions": self.required_permissions,
            "estimated_duration_seconds": self.estimated_duration_seconds,
            "status": self.status,
            "created_at": self.created_at,
            "usage_count": self.usage_count,
        }


class WorkflowSkillStore:
    """Persistent storage for governed workflow skills."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def save(self, workflow: ProposedWorkflow) -> None:
        workflows = [w for w in self.list() if w.get("workflow_id") != workflow.workflow_id]
        workflows.append(workflow.to_dict())
        self.path.write_text(json.dumps(workflows, indent=2), encoding="utf-8")

    def approve(self, workflow_id: str) -> bool:
        workflows = self.list()
        target = next((w for w in workflows if w.get("workflow_id") == workflow_id), None)
        if not target:
            return False
        target["status"] = "approved"
        self.path.write_text(json.dumps(workflows, indent=2), encoding="utf-8")
        return True

    def get(self, workflow_id: str) -> dict[str, Any] | None:
        workflows = self.list()
        return next((w for w in workflows if w.get("workflow_id") == workflow_id), None)


class WorkflowExtractor:
    """Extracts reusable workflows from successful multi-step task execution."""

    def __init__(self, workflow_store: WorkflowSkillStore):
        self.workflow_store = workflow_store

    def extract_workflow_from_plan(
        self,
        plan: WorkflowPlan,
        session_id: str,
        messages: list[Any],
    ) -> ProposedWorkflow:
        """Analyze a completed workflow plan and extract a reusable workflow template."""
        if plan.status != "completed":
            raise ValueError("Cannot extract workflow from incomplete plan")

        # Identify capabilities used
        capabilities_used = list(set(step.capability for step in plan.steps))

        # Identify permissions required
        permissions_required = list(
            set(
                perm
                for step in plan.steps
                for perm in step.required_permissions
            )
        )

        # Generate objective pattern from original objective
        objective_pattern = self._generate_objective_pattern(plan.objective)

        # Create steps template (sanitized)
        steps_template = []
        for step in plan.steps:
            if step.execution_state.value != "completed":
                continue  # Only include successful steps

            steps_template.append({
                "position": step.position,
                "objective": step.objective,
                "capability": step.capability,
                "tool": step.tool,
                "arguments_schema": self._sanitize_arguments(step.arguments),
                "risk_level": step.risk_level.value,
                "required_permissions": step.required_permissions,
                "approval_required": step.approval_required,
            })

        # Estimate duration (simplified)
        estimated_duration = len(steps_template) * 30  # 30 seconds per step estimate

        workflow = ProposedWorkflow(
            workflow_id=f"workflow_{uuid.uuid4().hex[:8]}",
            name=f"Automated workflow: {plan.objective[:40]}",
            description=f"Extracted workflow for: {plan.objective}",
            version="1.0.0",
            source_task_id=plan.task_id,
            source_session_id=session_id,
            objective_pattern=objective_pattern,
            required_capabilities=capabilities_used,
            steps_template=steps_template,
            required_permissions=permissions_required,
            estimated_duration_seconds=estimated_duration,
            status="proposed",
        )

        self.workflow_store.save(workflow)
        return workflow

    def _generate_objective_pattern(self, objective: str) -> str:
        """Generate a pattern for matching similar objectives."""
        # Extract key terms from objective
        objective_lower = objective.lower()

        # Binary analysis pattern
        if any(kw in objective_lower for kw in ["binary", "executable", "analyze", "reverse"]):
            return "binary.*analysis"

        # Source inspection pattern
        if any(kw in objective_lower for kw in ["source", "code", "inspect", "review"]):
            return "source.*inspection"

        # Security analysis pattern
        if any(kw in objective_lower for kw in ["security", "vulnerability", "scan"]):
            return "security.*analysis"

        # Default: use first few words
        words = objective.split()[:3]
        return ".*".join(words).lower()

    def _sanitize_arguments(self, arguments: dict[str, Any]) -> dict[str, str]:
        """Remove task-specific values from arguments, leaving only schema."""
        sanitized = {}
        for key, value in arguments.items():
            if isinstance(value, str) and value.startswith("{"):
                # Placeholder - keep as schema
                sanitized[key] = "string"
            elif isinstance(value, str):
                sanitized[key] = "string"
            elif isinstance(value, int):
                sanitized[key] = "integer"
            elif isinstance(value, bool):
                sanitized[key] = "boolean"
            elif isinstance(value, list):
                sanitized[key] = "array"
            elif isinstance(value, dict):
                sanitized[key] = "object"
            else:
                sanitized[key] = "any"
        return sanitized

    def match_workflow_to_objective(self, objective: str) -> dict[str, Any] | None:
        """Find an approved workflow that matches the given objective."""
        import re

        workflows = self.workflow_store.list()
        for workflow in workflows:
            if workflow.get("status") != "approved":
                continue

            pattern = workflow.get("objective_pattern", "")
            try:
                if re.search(pattern, objective, re.IGNORECASE):
                    return workflow
            except re.error:
                # Invalid regex, skip
                continue

        return None


class WorkflowLearningEngine:
    """High-level engine for workflow learning and extraction."""

    def __init__(self, skill_store: SkillStore, workflow_store: WorkflowSkillStore):
        self.skill_store = skill_store
        self.workflow_store = workflow_store
        self.extractor = WorkflowExtractor(workflow_store)
        self.learning_engine = ExperienceLearningEngine(skill_store)

    def extract_from_successful_task(
        self,
        session_id: str,
        task_objective: str,
        messages: list[Any],
        events: list[dict[str, Any]],
        plan: WorkflowPlan | None = None,
    ) -> tuple[ProposedSkill | None, ProposedWorkflow | None]:
        """Extract both skills and workflows from a successful task."""
        skill = None
        workflow = None

        # Extract skill (existing functionality)
        try:
            skill = self.learning_engine.extract_skill_from_trace(
                session_id,
                task_objective,
                messages,
                events,
            )
        except Exception:
            pass

        # Extract workflow (new functionality)
        if plan and plan.status == "completed":
            try:
                workflow = self.extractor.extract_workflow_from_plan(
                    plan,
                    session_id,
                    messages,
                )
            except Exception:
                pass

        return skill, workflow

    def propose_reusable_components(
        self,
        session_id: str,
        task_objective: str,
        messages: list[Any],
        events: list[dict[str, Any]],
        plan: WorkflowPlan | None = None,
    ) -> dict[str, Any]:
        """Analyze task and propose reusable components for operator approval."""
        skill, workflow = self.extract_from_successful_task(
            session_id,
            task_objective,
            messages,
            events,
            plan,
        )

        proposal = {
            "session_id": session_id,
            "task_objective": task_objective,
            "skill_proposal": skill.to_dict() if skill else None,
            "workflow_proposal": workflow.to_dict() if workflow else None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        return proposal
