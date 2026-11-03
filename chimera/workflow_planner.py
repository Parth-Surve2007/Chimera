"""Workflow Planner for Chimera Phase 5.3.

Decomposes high-level objectives into structured multi-step execution plans
with dependency management, capability selection, and risk classification.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .workflow_engine import (
    WorkflowPlan,
    WorkflowStep,
    StepDependency,
    StepState,
    RiskLevel,
    CapabilitySelector,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowPlanner:
    """Decomposes objectives into structured execution plans."""

    def __init__(self, capability_selector: CapabilitySelector):
        self.capability_selector = capability_selector

    def create_plan(
        self,
        task_id: str,
        objective: str,
        workspace: Path | None = None,
        mode: str = "plan",
    ) -> WorkflowPlan:
        """Analyze an objective and create a structured execution plan."""
        plan_id = uuid.uuid4().hex

        # Analyze objective and determine workflow type
        workflow_type = self._classify_objective(objective)

        # Generate steps based on workflow type
        if workflow_type == "binary_analysis":
            steps = self._plan_binary_analysis(objective, workspace, mode)
        elif workflow_type == "source_inspection":
            steps = self._plan_source_inspection(objective, workspace, mode)
        elif workflow_type == "security_analysis":
            steps = self._plan_security_analysis(objective, workspace, mode)
        else:
            # Generic fallback
            steps = self._plan_generic(objective, workspace, mode)

        plan = WorkflowPlan(
            plan_id=plan_id,
            task_id=task_id,
            objective=objective,
            steps=steps,
            created_at=_now(),
            status="pending",
        )

        return plan

    def _classify_objective(self, objective: str) -> str:
        """Classify the objective to determine the appropriate workflow template."""
        objective_lower = objective.lower()

        # Binary analysis keywords
        binary_keywords = [
            "binary", "executable", "exe", "dll", "analyze", "reverse engineer",
            "extract strings", "imports", "exports", "sections", "pe", "elf", "macho",
        ]
        if any(kw in objective_lower for kw in binary_keywords):
            return "binary_analysis"

        # Source inspection keywords
        source_keywords = [
            "source", "code", "inspect", "review", "read", "search", "find",
            "understand", "explain", "document",
        ]
        if any(kw in objective_lower for kw in source_keywords):
            return "source_inspection"

        # Security analysis keywords
        security_keywords = [
            "security", "vulnerability", "scan", "audit", "check", "assess",
            "cve", "exploit", "malware", "threat",
        ]
        if any(kw in objective_lower for kw in security_keywords):
            return "security_analysis"

        return "generic"

    def _plan_binary_analysis(
        self,
        objective: str,
        workspace: Path | None,
        mode: str,
    ) -> list[WorkflowStep]:
        """Plan a binary analysis workflow."""
        steps = []
        position = 0

        # Step 1: Workspace inspection to identify the artifact
        step1_id = uuid.uuid4().hex
        steps.append(
            WorkflowStep(
                step_id=step1_id,
                position=position,
                objective="Inspect workspace to identify the binary artifact",
                capability="workspace",
                tool="list_files",
                arguments={"relative": ".", "recursive": True},
                dependencies=[],
                expected_output="List of files in workspace",
                risk_level=RiskLevel.SAFE,
                required_permissions=["workspace.read"],
                approval_required=False,
                execution_state=StepState.PENDING,
            )
        )
        position += 1

        # Step 2: Identify the artifact format (depends on step 1)
        step2_id = uuid.uuid4().hex
        steps.append(
            WorkflowStep(
                step_id=step2_id,
                position=position,
                objective="Detect binary format and architecture",
                capability="rea",
                tool="detect_format",
                arguments={"artifact_path": "{from_step_1}"},  # Placeholder for actual path
                dependencies=[StepDependency(step1_id, "data")],
                expected_output="Format detection results (PE, ELF, etc.)",
                risk_level=RiskLevel.SAFE,
                required_permissions=["rea.read"],
                approval_required=False,
                execution_state=StepState.PENDING,
            )
        )
        position += 1

        # Step 3: Extract strings (depends on step 2)
        step3_id = uuid.uuid4().hex
        steps.append(
            WorkflowStep(
                step_id=step3_id,
                position=position,
                objective="Extract printable strings from the binary",
                capability="rea",
                tool="extract_strings",
                arguments={"artifact_path": "{from_step_1}", "min_length": 4},
                dependencies=[StepDependency(step1_id, "data"), StepDependency(step2_id, "order")],
                expected_output="List of extracted strings with offsets",
                risk_level=RiskLevel.SAFE,
                required_permissions=["rea.read"],
                approval_required=False,
                execution_state=StepState.PENDING,
            )
        )
        position += 1

        # Step 4: Comprehensive static analysis (depends on step 2)
        step4_id = uuid.uuid4().hex
        steps.append(
            WorkflowStep(
                step_id=step4_id,
                position=position,
                objective="Perform comprehensive static analysis",
                capability="rea",
                tool="analyze_artifact",
                arguments={"artifact_path": "{from_step_1}"},
                dependencies=[StepDependency(step1_id, "data"), StepDependency(step2_id, "order")],
                expected_output="Full analysis including sections, imports, exports",
                risk_level=RiskLevel.SAFE,
                required_permissions=["rea.read"],
                approval_required=False,
                execution_state=StepState.PENDING,
            )
        )
        position += 1

        # Step 5: Generate report (depends on steps 3 and 4)
        step5_id = uuid.uuid4().hex
        steps.append(
            WorkflowStep(
                step_id=step5_id,
                position=position,
                objective="Generate structured technical analysis report",
                capability="workspace",
                tool="create_artifact",
                arguments={
                    "name": "binary_analysis_report.md",
                    "content": "{from_steps_3_4}",  # Placeholder for aggregated data
                },
                dependencies=[
                    StepDependency(step3_id, "data"),
                    StepDependency(step4_id, "data"),
                ],
                expected_output="Markdown report file",
                risk_level=RiskLevel.LOW,
                required_permissions=["execution.write"],
                approval_required=(mode == "build"),  # Requires approval in build mode
                execution_state=StepState.PENDING,
            )
        )

        return steps

    def _plan_source_inspection(
        self,
        objective: str,
        workspace: Path | None,
        mode: str,
    ) -> list[WorkflowStep]:
        """Plan a source code inspection workflow."""
        steps = []
        position = 0

        # Step 1: List files
        step1_id = uuid.uuid4().hex
        steps.append(
            WorkflowStep(
                step_id=step1_id,
                position=position,
                objective="List source files in workspace",
                capability="workspace",
                tool="list_files",
                arguments={"relative": ".", "recursive": True},
                dependencies=[],
                expected_output="List of source files",
                risk_level=RiskLevel.SAFE,
                required_permissions=["workspace.read"],
                approval_required=False,
                execution_state=StepState.PENDING,
            )
        )
        position += 1

        # Step 2: Search for relevant files based on objective
        step2_id = uuid.uuid4().hex
        steps.append(
            WorkflowStep(
                step_id=step2_id,
                position=position,
                objective="Search for relevant source files",
                capability="workspace",
                tool="search_files",
                arguments={"pattern": "*.py"},  # Could be inferred from objective
                dependencies=[StepDependency(step1_id, "order")],
                expected_output="List of matching source files",
                risk_level=RiskLevel.SAFE,
                required_permissions=["workspace.read"],
                approval_required=False,
                execution_state=StepState.PENDING,
            )
        )
        position += 1

        # Step 3: Read and analyze key files
        step3_id = uuid.uuid4().hex
        steps.append(
            WorkflowStep(
                step_id=step3_id,
                position=position,
                objective="Read and analyze key source files",
                capability="workspace",
                tool="read_text",
                arguments={"path": "{from_step_2}"},
                dependencies=[StepDependency(step2_id, "data")],
                expected_output="Source file contents",
                risk_level=RiskLevel.SAFE,
                required_permissions=["workspace.read"],
                approval_required=False,
                execution_state=StepState.PENDING,
            )
        )
        position += 1

        return steps

    def _plan_security_analysis(
        self,
        objective: str,
        workspace: Path | None,
        mode: str,
    ) -> list[WorkflowStep]:
        """Plan a security analysis workflow."""
        steps = []
        position = 0

        # Step 1: Inspect workspace
        step1_id = uuid.uuid4().hex
        steps.append(
            WorkflowStep(
                step_id=step1_id,
                position=position,
                objective="Inspect workspace for security-relevant files",
                capability="workspace",
                tool="list_files",
                arguments={"relative": ".", "recursive": True},
                dependencies=[],
                expected_output="List of files",
                risk_level=RiskLevel.SAFE,
                required_permissions=["workspace.read"],
                approval_required=False,
                execution_state=StepState.PENDING,
            )
        )
        position += 1

        # Step 2: Search for sensitive patterns
        step2_id = uuid.uuid4().hex
        steps.append(
            WorkflowStep(
                step_id=step2_id,
                position=position,
                objective="Search for sensitive patterns (keys, tokens, etc.)",
                capability="workspace",
                tool="search_text",
                arguments={"query": "api_key"},
                dependencies=[StepDependency(step1_id, "order")],
                expected_output="Matches for sensitive patterns",
                risk_level=RiskLevel.LOW,
                required_permissions=["workspace.read"],
                approval_required=False,
                execution_state=StepState.PENDING,
            )
        )
        position += 1

        return steps

    def _plan_generic(
        self,
        objective: str,
        workspace: Path | None,
        mode: str,
    ) -> list[WorkflowStep]:
        """Plan a generic workflow as fallback."""
        steps = []
        position = 0

        # Single step: simple workspace inspection
        step1_id = uuid.uuid4().hex
        steps.append(
            WorkflowStep(
                step_id=step1_id,
                position=position,
                objective="Inspect workspace to understand context",
                capability="workspace",
                tool="list_files",
                arguments={"relative": ".", "recursive": True},
                dependencies=[],
                expected_output="List of files in workspace",
                risk_level=RiskLevel.SAFE,
                required_permissions=["workspace.read"],
                approval_required=False,
                execution_state=StepState.PENDING,
            )
        )

        return steps
