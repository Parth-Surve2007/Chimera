"""Synthetic demonstration workspace generator and demo runner for Chimera.

Provides a self-contained, reproducible sample application:
- Task 1: Read-only understanding (inspect project structure, purpose, and dependencies).
- Task 2: Governed modification (Plan Mode blocks write; Build Mode prompts approval; authorized tool executes).
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.agent_runtime import AgentRuntime, AgentSessionStore
from chimera.local_model import LocalModelConfig, LocalModelProvider
from chimera.model_hub import ModelProfile, ModelProfileStore
from chimera.providers.base import ModelProvider, ProviderConfig
from chimera.providers.schemas import (
    ApiDialect,
    ExecutionProvenance,
    GenerationRequest,
    GenerationResponse,
    NormalizedMessage,
    ToolCall,
)


def create_demo_workspace(target_dir: Path) -> Path:
    """Populate a self-contained synthetic demonstration project."""
    target_dir.mkdir(parents=True, exist_ok=True)
    
    # pyproject.toml
    (target_dir / "pyproject.toml").write_text(
        """[project]
name = "chimera-task-tracker"
version = "0.1.0"
description = "A lightweight local-first task tracking utility"
dependencies = []
""",
        encoding="utf-8",
    )
    
    # README.md
    (target_dir / "README.md").write_text(
        """# Chimera Task Tracker

Version: 0.1.0
Marker: DEMO_WORKSPACE_STAGING_TOKEN_2026

## Purpose
A lightweight local task manager with simple in-memory storage.
""",
        encoding="utf-8",
    )
    
    # src/
    src = target_dir / "src"
    src.mkdir(exist_ok=True)
    (src / "tracker.py").write_text(
        """class TaskTracker:
    def __init__(self):
        self.tasks = []

    def add(self, title: str):
        self.tasks.append(title)
        return len(self.tasks)
""",
        encoding="utf-8",
    )
    
    # tests/
    tests = target_dir / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_tracker.py").write_text(
        """from src.tracker import TaskTracker

def test_add():
    t = TaskTracker()
    assert t.add("Task 1") == 1
""",
        encoding="utf-8",
    )
    
    return target_dir


@dataclass
class DemoRunResult:
    run_id: str
    scenario_name: str
    task_prompt: str
    mode: str
    model_id: str
    provider_id: str
    provenance: ExecutionProvenance
    status: str
    duration_ms: int
    events: list[dict[str, Any]] = field(default_factory=list)
    final_response: str = ""
    tool_calls_observed: list[str] = field(default_factory=list)
    permissions_checked: list[dict[str, Any]] = field(default_factory=list)


class HackathonDemoRunner:
    """Executes governed hackathon demonstration workflows."""

    def __init__(self, workspace_path: Path, db_path: Path, provider: Any):
        self.workspace_path = workspace_path
        self.store = AgentSessionStore(db_path)
        self.runtime = AgentRuntime(self.store, provider)
        self.provider = provider

    def run_task1_readonly_understanding(self) -> DemoRunResult:
        """Task 1: Read-only inspection and understanding of the demonstration workspace."""
        sid = self.runtime.create_session(self.workspace_path, mode="plan")
        t0 = time.perf_counter()
        
        prompt = "Inspect this workspace. List its files and describe its main purpose."
        res = self.runtime.send(sid, prompt)
        dur = int((time.perf_counter() - t0) * 1000)
        
        messages = self.store.messages(sid)
        events = [e.payload for e in self.store.events(sid)]
        final = messages[-1].content if messages else ""
        
        tool_calls = [
            json.loads(m.content).get("tool", "")
            for m in messages if m.role == "tool"
        ]
        
        return DemoRunResult(
            run_id=f"demo_t1_{sid[:8]}",
            scenario_name="Task 1: Read-Only Workspace Understanding",
            task_prompt=prompt,
            mode="plan",
            model_id=str(getattr(getattr(self.provider, "config", None), "model_path", "default")),
            provider_id="local_model" if hasattr(self.provider, "converse") else "configured_provider",
            provenance=ExecutionProvenance.LIVE_LOCAL,
            status=res.get("status", "failed"),
            duration_ms=dur,
            events=events,
            final_response=final,
            tool_calls_observed=tool_calls,
        )

    def run_task2_governed_modification(self) -> dict[str, Any]:
        """Task 2: Governed modification demonstrating Plan Mode block and Build Mode approval."""
        results = {}
        
        # Step A: Attempt write in Plan Mode -> MUST be blocked
        sid_plan = self.runtime.create_session(self.workspace_path, mode="plan")
        
        class PlanWriteAttemptAdapter(ModelProvider):
            def validate_credentials(self): return True
            def check_readiness(self): return {"status": "ready"}
            def capabilities(self, model): return None
            def generate(self, req, cancel_event=None, on_delta=None):
                # If tool message is in history, complete with message
                if any(m.role == "tool" for m in req.messages):
                    return GenerationResponse(content="Artifact created successfully.", model=req.model)
                return GenerationResponse(
                    tool_calls=(ToolCall(id="c_write", name="workspace.create_artifact", arguments={"name": "notes.txt", "content": "hello"}),),
                    model=req.model,
                )
        
        rt_plan = AgentRuntime(self.store, PlanWriteAttemptAdapter(ProviderConfig("p_test", "P", ApiDialect.CUSTOM)))
        res_plan = rt_plan.send(sid_plan, "Create notes.txt")
        
        plan_msgs = self.store.messages(sid_plan)
        tool_msg = next((m for m in plan_msgs if m.role == "tool"), None)
        is_blocked = tool_msg and "blocked" in tool_msg.content
        results["plan_mode_blocked"] = bool(is_blocked)
        
        # Step B: In Build Mode, protected write requires operator approval
        sid_build = self.runtime.create_session(self.workspace_path, mode="build")
        res_build = rt_plan.send(sid_build, "Create notes.txt")
        requires_approval = res_build.get("status") == "awaiting_approval"
        results["build_mode_requires_approval"] = bool(requires_approval)
        
        # Step C: Operator grants approval
        if requires_approval:
            res_approved = rt_plan.approve_pending(sid_build, approve=True)
            results["approval_executed"] = res_approved.get("status") == "completed"
            # Verify artifact was created in workspace
            artifact_file = self.workspace_path / ".chimera-agent-artifacts" / sid_build / "notes.txt"
            results["artifact_persisted"] = artifact_file.is_file()

        
        return results
