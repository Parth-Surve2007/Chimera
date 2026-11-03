"""Workflow Timeline UI Component for Phase 5.3.

Provides clear visualization of multi-step workflow execution progress,
step dependencies, and real-time status updates using Chimera's design language.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .workflow_engine import WorkflowPlan, WorkflowStep, StepState


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TimelineEvent:
    """A single event in the workflow execution timeline."""
    event_id: str
    timestamp: str
    event_type: str
    step_id: str | None
    description: str
    details: dict[str, Any]


class WorkflowTimeline:
    """Manages and formats workflow execution timeline for UI display."""

    def __init__(self, plan: WorkflowPlan):
        self.plan = plan
        self.events: list[TimelineEvent] = []

    def add_event(
        self,
        event_type: str,
        step_id: str | None,
        description: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Add an event to the timeline."""
        event = TimelineEvent(
            event_id=f"evt_{len(self.events)}",
            timestamp=_now(),
            event_type=event_type,
            step_id=step_id,
            description=description,
            details=details or {},
        )
        self.events.append(event)

    def to_display_dict(self) -> dict[str, Any]:
        """Convert timeline to a display-ready dictionary."""
        return {
            "plan_id": self.plan.plan_id,
            "objective": self.plan.objective,
            "status": self.plan.status,
            "created_at": self.plan.created_at,
            "steps": self._format_steps(),
            "events": self._format_events(),
            "summary": self._generate_summary(),
        }

    def _format_steps(self) -> list[dict[str, Any]]:
        """Format steps for UI display."""
        formatted = []
        for step in self.plan.steps:
            formatted.append({
                "step_id": step.step_id,
                "position": step.position,
                "objective": step.objective,
                "capability": step.capability,
                "tool": step.tool,
                "state": step.execution_state.value,
                "risk_level": step.risk_level.value,
                "dependencies": [d.step_id for d in step.dependencies],
                "approval_required": step.approval_required,
                "verification_status": step.verification_status,
                "error": step.error,
                "started_at": step.started_at,
                "completed_at": step.completed_at,
                "retry_count": step.retry_count,
            })
        return formatted

    def _format_events(self) -> list[dict[str, Any]]:
        """Format events for UI display."""
        return [
            {
                "event_id": e.event_id,
                "timestamp": e.timestamp,
                "event_type": e.event_type,
                "step_id": e.step_id,
                "description": e.description,
                "details": e.details,
            }
            for e in self.events
        ]

    def _generate_summary(self) -> dict[str, Any]:
        """Generate execution summary."""
        total_steps = len(self.plan.steps)
        completed = sum(1 for s in self.plan.steps if s.execution_state == StepState.COMPLETED)
        failed = sum(1 for s in self.plan.steps if s.execution_state == StepState.FAILED)
        running = sum(1 for s in self.plan.steps if s.execution_state == StepState.RUNNING)
        pending = sum(1 for s in self.plan.steps if s.execution_state == StepState.PENDING)

        return {
            "total_steps": total_steps,
            "completed": completed,
            "failed": failed,
            "running": running,
            "pending": pending,
            "progress_percent": int((completed / total_steps) * 100) if total_steps > 0 else 0,
        }


class WorkflowProgressDisplay:
    """Formats workflow progress for terminal/UI display."""

    # Chimera design colors (monochrome palette)
    COLORS = {
        "reset": "\033[0m",
        "bold": "\033[1m",
        "dim": "\033[2m",
        "graphite": "\033[38;5;240m",  # #585858
        "greige": "\033[38;5;247m",  # #9e9e9e
        "bronze": "\033[38;5;214m",  # #ffaf00
        "success": "\033[38;5;149m",  # #afdfff
        "error": "\033[38;5;203m",  # #ff5f87
        "warning": "\033[38;5;221m",  # #ffd787
    }

    @staticmethod
    def color(text: str, color_name: str) -> str:
        """Apply color to text."""
        return f"{WorkflowProgressDisplay.COLORS.get(color_name, '')}{text}{WorkflowProgressDisplay.COLORS['reset']}"

    @staticmethod
    def display_plan(plan: WorkflowPlan) -> str:
        """Display workflow plan in a formatted way."""
        lines = []
        lines.append(WorkflowProgressDisplay.color(f"Workflow Plan: {plan.plan_id}", "bold"))
        lines.append(f"Objective: {plan.objective}")
        lines.append(f"Status: {plan.status}")
        lines.append(f"Steps: {len(plan.steps)}")
        lines.append("")

        for step in plan.steps:
            state_color = {
                StepState.PENDING: "graphite",
                StepState.READY: "greige",
                StepState.RUNNING: "bronze",
                StepState.COMPLETED: "success",
                StepState.FAILED: "error",
                StepState.BLOCKED: "warning",
            }.get(step.execution_state, "graphite")

            lines.append(
                f"  [{step.position}] {WorkflowProgressDisplay.color(step.execution_state.value.upper(), state_color)} "
                f"{step.capability}.{step.tool or 'general'}"
            )
            lines.append(f"      {step.objective}")
            if step.dependencies:
                deps = ", ".join(d.step_id for d in step.dependencies)
                lines.append(f"      Depends: {deps}")
            if step.error:
                lines.append(f"      Error: {step.error}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def display_timeline(timeline: WorkflowTimeline) -> str:
        """Display workflow execution timeline."""
        display = timeline.to_display_dict()
        lines = []
        lines.append(WorkflowProgressDisplay.color(f"Workflow Timeline: {display['plan_id']}", "bold"))
        lines.append(f"Objective: {display['objective']}")
        lines.append(f"Status: {display['status']}")
        lines.append("")

        # Summary
        summary = display["summary"]
        lines.append("Summary:")
        lines.append(f"  Progress: {summary['progress_percent']}%")
        lines.append(f"  Completed: {summary['completed']}/{summary['total_steps']}")
        lines.append(f"  Failed: {summary['failed']}")
        lines.append(f"  Running: {summary['running']}")
        lines.append(f"  Pending: {summary['pending']}")
        lines.append("")

        # Steps
        lines.append("Steps:")
        for step in display["steps"]:
            state_color = {
                "pending": "graphite",
                "ready": "greige",
                "running": "bronze",
                "completed": "success",
                "failed": "error",
                "blocked": "warning",
            }.get(step["state"], "graphite")

            lines.append(
                f"  [{step['position']}] {WorkflowProgressDisplay.color(step['state'].upper(), state_color)} "
                f"{step['capability']}.{step['tool'] or 'general'}"
            )
            lines.append(f"      {step['objective']}")
            if step["dependencies"]:
                lines.append(f"      Depends: {', '.join(step['dependencies'])}")
            if step["verification_status"] != "unknown":
                lines.append(f"      Verification: {step['verification_status']}")
            if step["error"]:
                lines.append(f"      Error: {step['error']}")
            lines.append("")

        # Recent events
        if display["events"]:
            lines.append("Recent Events:")
            for event in display["events"][-5:]:  # Last 5 events
                lines.append(f"  [{event['timestamp']}] {event['event_type']}: {event['description']}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def display_compact_progress(plan: WorkflowPlan) -> str:
        """Display compact progress bar for workflow."""
        total = len(plan.steps)
        completed = sum(1 for s in plan.steps if s.execution_state == StepState.COMPLETED)
        failed = sum(1 for s in plan.steps if s.execution_state == StepState.FAILED)
        running = sum(1 for s in plan.steps if s.execution_state == StepState.RUNNING)

        progress = int((completed / total) * 100) if total > 0 else 0

        # Build progress bar
        bar_width = 40
        filled = int((progress / 100) * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)

        status_line = (
            f"[{bar}] {progress}% | "
            f"✓ {completed} | "
            f"✗ {failed} | "
            f"⟳ {running} | "
            f"⏳ {total - completed - failed - running}"
        )

        return status_line


class WorkflowReportGenerator:
    """Generates structured reports from workflow execution."""

    @staticmethod
    def generate_execution_report(
        plan: WorkflowPlan,
        timeline: WorkflowTimeline,
        workspace: Path | None = None,
    ) -> str:
        """Generate a Markdown execution report."""
        lines = []
        lines.append("# Workflow Execution Report")
        lines.append("")
        lines.append(f"**Plan ID:** {plan.plan_id}")
        lines.append(f"**Objective:** {plan.objective}")
        lines.append(f"**Status:** {plan.status}")
        lines.append(f"**Created:** {plan.created_at}")
        lines.append("")

        # Summary
        display = timeline.to_display_dict()
        summary = display["summary"]
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- **Total Steps:** {summary['total_steps']}")
        lines.append(f"- **Completed:** {summary['completed']}")
        lines.append(f"- **Failed:** {summary['failed']}")
        lines.append(f"- **Progress:** {summary['progress_percent']}%")
        lines.append("")

        # Step Details
        lines.append("## Step Details")
        lines.append("")
        for step in plan.steps:
            lines.append(f"### Step {step.position}: {step.objective}")
            lines.append("")
            lines.append(f"- **ID:** {step.step_id}")
            lines.append(f"- **Capability:** {step.capability}")
            lines.append(f"- **Tool:** {step.tool or 'N/A'}")
            lines.append(f"- **State:** {step.execution_state.value}")
            lines.append(f"- **Risk Level:** {step.risk_level.value}")
            lines.append(f"- **Verification:** {step.verification_status}")
            lines.append(f"- **Started:** {step.started_at or 'N/A'}")
            lines.append(f"- **Completed:** {step.completed_at or 'N/A'}")
            lines.append(f"- **Retries:** {step.retry_count}")

            if step.dependencies:
                lines.append(f"- **Dependencies:** {', '.join(d.step_id for d in step.dependencies)}")

            if step.result_data:
                lines.append("- **Result:**")
                lines.append("```json")
                lines.append(json.dumps(step.result_data, indent=2))
                lines.append("```")

            if step.error:
                lines.append(f"- **Error:** {step.error}")

            lines.append("")

        # Timeline
        lines.append("## Execution Timeline")
        lines.append("")
        for event in timeline.events:
            lines.append(f"- **{event.timestamp}** - {event.event_type}: {event.description}")
            if event.details:
                lines.append(f"  Details: {json.dumps(event.details, indent=2)}")
        lines.append("")

        # Deliverables
        lines.append("## Deliverables")
        lines.append("")
        # Check for generated artifacts
        if workspace:
            output_dir = workspace / ".chimera-agent-artifacts"
            if output_dir.exists():
                lines.append("Generated artifacts:")
                for artifact in output_dir.iterdir():
                    if artifact.is_file():
                        lines.append(f"- {artifact.name} ({artifact.stat().st_size} bytes)")
        lines.append("")

        return "\n".join(lines)
