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
        """Extract reusable action schemas from successful tool traces.

        New runtime messages persist arguments and explicit status. Older
        traces may omit both; they remain readable for backwards compatibility,
        but their result payload is retained only as redacted historical data.
        """
        reusable_steps = []
        permissions_needed = set()

        for message in messages:
            role = getattr(message, "role", None) or (message.get("role") if isinstance(message, dict) else "")
            if role != "tool":
                continue
            content = getattr(message, "content", "") if not isinstance(message, dict) else message.get("content", "")
            try:
                data = json.loads(content) if isinstance(content, str) else content
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue

            # Missing status means an older persisted success-shaped result.
            # An explicit non-completed status is never learned as a skill step.
            if data.get("status") not in (None, "completed"):
                continue
            tool_name = data.get("tool", "")
            if not isinstance(tool_name, str) or not tool_name or tool_name.startswith("agent."):
                continue

            permission = None
            if tool_name.startswith("application."):
                try:
                    from .applications.registry import get_application_registry
                    from .applications.dispatch import decode_tool_id
                    decoded = decode_tool_id(tool_name)
                    if decoded:
                        connector = get_application_registry().get(decoded[0])
                        if connector:
                            action = next((item for item in connector.manifest.actions if item.action_id == decoded[1]), None)
                            if action:
                                permission = action.permission
                except Exception:
                    permission = None

            if not permission and "." in tool_name:
                app_part = tool_name.replace("application.", "")
                if "." in app_part:
                    app_id, action_name = app_part.split(".", 1)
                    try:
                        from .applications.registry import get_application_registry
                        connector = get_application_registry().get(app_id)
                        if connector:
                            action = next((item for item in connector.manifest.actions if item.action_id == action_name), None)
                            if action:
                                permission = action.permission
                    except Exception:
                        permission = None

            if not permission:
                read_indicators = ("list", "read", "extract", "inspect", "enumerate", "scan", "get", "query", "search", "find")
                permission = f"{tool_name.split('.')[0]}.read" if any(token in tool_name for token in read_indicators) else f"{tool_name.split('.')[0]}.write"
            permissions_needed.add(permission)

            result_data = data.get("result")
            result_fields = sorted(str(key)[:80] for key in result_data)[:20] if isinstance(result_data, dict) else []
            if isinstance(data.get("arguments"), dict):
                # Prefer sanitized argument shapes, never prior task-specific values.
                sanitized_arguments = self._argument_schema(self._redact_secrets(data["arguments"]))
            else:
                # Legacy records have no arguments. Preserve only redacted historical evidence.
                sanitized_arguments = self._redact_secrets(result_data or {})

            reusable_steps.append({
                "action": tool_name,
                "sanitized_arguments": sanitized_arguments,
                "expected_result_fields": result_fields,
            })

        skill = ProposedSkill(
            skill_id=f"skill_{uuid.uuid4().hex[:8]}",
            name=f"Automated workflow: {task_objective[:40]}",
            description=f"Extracted procedure for: {task_objective}",
            version="1.0.0",
            source_session_id=session_id,
            required_permissions=sorted(permissions_needed),
            parameters_schema={"workspace_relative": "string"},
            reusable_steps=reusable_steps,
            expected_outputs=["verified_inspection_summary"],
        )
        self.skill_store.save(skill)
        return skill

    @staticmethod
    def _argument_schema(data: Any) -> Any:
        """Keep argument shapes while removing run-specific values from learned skills."""
        if isinstance(data, dict):
            return {str(key): ExperienceLearningEngine._argument_schema(value) for key, value in data.items()}
        if isinstance(data, list):
            return {"type": "array", "items": ExperienceLearningEngine._argument_schema(data[0]) if data else "any"}
        if isinstance(data, bool):
            return "boolean"
        if isinstance(data, int):
            return "integer"
        if isinstance(data, float):
            return "number"
        if isinstance(data, str):
            return "string"
        if data is None:
            return "null"
        return "any"

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
