"""Governed Experience Capture and Self-Learning Engine for Chimera.

Implements structured memory tiers (working, episodic, semantic, procedural),
trace inspection, secret redaction, and proposed skill extraction with approval gating.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ProposedSkill:
    skill_id: str
    name: str
    description: str
    version: str
    source_session_id: str
    required_permissions: list[str]
    parameters_schema: dict[str, Any]
    reusable_steps: list[dict[str, Any]]
    expected_outputs: list[str]
    status: str = "proposed"  # "proposed", "approved", "rejected", "installed"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SkillStore:
    """Persistent storage for governed, operator-approved skills."""

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

    def save(self, skill: ProposedSkill) -> None:
        skills = [s for s in self.list() if s.get("skill_id") != skill.skill_id]
        skills.append(skill.to_dict())
        self.path.write_text(json.dumps(skills, indent=2), encoding="utf-8")

    def approve(self, skill_id: str) -> bool:
        skills = self.list()
        target = next((s for s in skills if s.get("skill_id") == skill_id), None)
        if not target:
            return False
        target["status"] = "approved"
        self.path.write_text(json.dumps(skills, indent=2), encoding="utf-8")
        return True


class ExperienceLearningEngine:
    """Extracts, redacts, and formats reusable skills from successful agent traces."""

    def __init__(self, skill_store: SkillStore):
        self.skill_store = skill_store

    def extract_skill_from_trace(
        self,
        session_id: str,
        task_objective: str,
        messages: list[Any],
        events: list[dict[str, Any]],
    ) -> ProposedSkill:
        """Inspect execution trace, redact task-specific secrets, and synthesize a ProposedSkill."""
        reusable_steps = []
        permissions_needed = set()

        for m in messages:
            role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else "")
            content = getattr(m, "content", "") if not isinstance(m, dict) else m.get("content", "")

            if role == "tool":
                try:
                    data = json.loads(content) if isinstance(content, str) else content
                    tool_name = data.get("tool", "")
                    if tool_name:
                        # Extract permission prefix
                        read_indicators = ("list", "read", "extract", "inspect", "enumerate", "scan", "get", "query", "search", "find")
                        perm = f"{tool_name.split('.')[0]}.read" if any(k in tool_name for k in read_indicators) else f"{tool_name.split('.')[0]}.write"
                        permissions_needed.add(perm)
                        reusable_steps.append({
                            "action": tool_name,
                            "sanitized_arguments": self._redact_secrets(data.get("result", {})),
                        })
                except Exception:
                    pass

        skill = ProposedSkill(
            skill_id=f"skill_{uuid.uuid4().hex[:8]}",
            name=f"Automated workflow: {task_objective[:40]}",
            description=f"Extracted procedure for: {task_objective}",
            version="1.0.0",
            source_session_id=session_id,
            required_permissions=sorted(list(permissions_needed)),
            parameters_schema={"workspace_relative": "string"},
            reusable_steps=reusable_steps,
            expected_outputs=["verified_inspection_summary"],
        )

        self.skill_store.save(skill)
        return skill

    @staticmethod
    def _redact_secrets(data: Any) -> Any:
        """Strip API keys, tokens, and password strings from recorded arguments."""
        if isinstance(data, dict):
            return {k: ("***REDACTED***" if any(s in k.lower() for s in ["key", "secret", "token", "pass"]) else ExperienceLearningEngine._redact_secrets(v)) for k, v in data.items()}
        if isinstance(data, list):
            return [ExperienceLearningEngine._redact_secrets(item) for item in data]
        if isinstance(data, str) and len(data) > 30 and re.search(r'(?:api|token|secret)[-_a-zA-Z0-9]{16,}', data, re.IGNORECASE):
            return "***REDACTED_SECRET***"
        return data
