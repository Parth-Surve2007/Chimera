"""Governed experience capture and self-improving procedural memory.

Chimera turns completed, repeatable tool traces into *proposed* skills. A proposal
is never executable knowledge until an operator approves it. Skill content stores
argument shapes and result fields, not run-specific argument values or raw logs.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


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
    status: str = "proposed"
    created_at: str = field(default_factory=_now)
    source_task_id: str | None = None
    trace_fingerprint: str | None = None
    lessons: list[dict[str, str]] = field(default_factory=list)
    evidence_count: int = 1
    source_task_ids: list[str] = field(default_factory=list)
    revision: int = 1
    usage_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_used_at: str | None = None
    last_outcome: str | None = None
    review_required: bool = False
    locked: bool = False
    pinned: bool = False
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SkillStore:
    """Atomic JSON storage with append-only revisions and operator governance."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path = self.path.with_name(self.path.name + ".history.jsonl")
        self._lock = threading.RLock()

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return value if isinstance(value, list) else []

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._read_unlocked()

    def get(self, skill_id: str) -> dict[str, Any] | None:
        if not isinstance(skill_id, str) or not skill_id.strip():
            return None
        return next((item for item in self.list() if item.get("skill_id") == skill_id), None)

    def proposals(self) -> list[dict[str, Any]]:
        return [item for item in self.list() if item.get("status") == "proposed"]

    def approved(self) -> list[dict[str, Any]]:
        return [item for item in self.list() if item.get("status") == "approved"]

    def find_by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        if not fingerprint:
            return None
        return next(
            (item for item in self.list() if item.get("trace_fingerprint") == fingerprint),
            None,
        )

    def _write_unlocked(self, skills: list[dict[str, Any]]) -> None:
        payload = _json_bytes(skills)
        fd, temporary = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            except OSError:
                pass

    def _history(self, action: str, skill_id: str, before: dict | None, after: dict | None) -> None:
        record = {
            "revision_id": uuid.uuid4().hex,
            "timestamp": _now(),
            "action": action,
            "skill_id": skill_id,
            "before": before,
            "after": after,
        }
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()

    def history(self, skill_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("history limit must be between 1 and 500")
        if not self.history_path.is_file():
            return []
        records = []
        try:
            with self.history_path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict) and (
                        skill_id is None or item.get("skill_id") == skill_id
                    ):
                        records.append(item)
        except OSError:
            return []
        return records[-limit:]

    def save(self, skill: ProposedSkill) -> None:
        """Create a proposal or revise an existing skill while preserving governance."""
        data = skill.to_dict()
        with self._lock:
            skills = self._read_unlocked()
            previous = next(
                (item for item in skills if item.get("skill_id") == skill.skill_id), None
            )
            if previous:
                if previous.get("locked"):
                    raise PermissionError("skill is locked; unlock it before revising its content")
                if previous.get("status") == "approved":
                    content_fields = (
                        "name", "description", "version", "required_permissions",
                        "parameters_schema", "reusable_steps", "expected_outputs",
                        "lessons", "trace_fingerprint",
                    )
                    if any(previous.get(key) != data.get(key) for key in content_fields):
                        raise PermissionError(
                            "approved skill content is immutable; create a new proposal revision for operator review"
                        )
                data["revision"] = int(previous.get("revision", 1)) + 1
                for key in (
                    "status", "usage_count", "success_count", "failure_count",
                    "last_used_at", "last_outcome", "review_required", "locked", "pinned",
                    "evidence_count", "source_task_ids",
                ):
                    if key in previous:
                        data[key] = previous[key]
            data["updated_at"] = _now()
            updated = [item for item in skills if item.get("skill_id") != skill.skill_id]
            updated.append(data)
            self._write_unlocked(updated)
            self._history("create" if previous is None else "revise", skill.skill_id, previous, data)

    def record_evidence(self, skill_id: str, source_task_id: str | None = None) -> bool:
        """Record a repeated successful trace without creating duplicate skills."""
        with self._lock:
            skills = self._read_unlocked()
            target = next((item for item in skills if item.get("skill_id") == skill_id), None)
            if target is None:
                return False
            before = dict(target)
            task_ids = list(target.get("source_task_ids") or [])
            if source_task_id and source_task_id not in task_ids:
                task_ids.append(source_task_id)
                target["evidence_count"] = int(target.get("evidence_count", 1)) + 1
            target["source_task_ids"] = task_ids[-50:]
            target["updated_at"] = _now()
            target["revision"] = int(target.get("revision", 1)) + 1
            self._write_unlocked(skills)
            self._history("evidence", skill_id, before, target)
            return True

    def approve(self, skill_id: str) -> bool:
        return self._set_status(skill_id, "approved", expected="proposed")

    def reject(self, skill_id: str) -> bool:
        return self._set_status(skill_id, "rejected", expected="proposed")

    def _set_status(self, skill_id: str, status: str, expected: str) -> bool:
        with self._lock:
            skills = self._read_unlocked()
            target = next((item for item in skills if item.get("skill_id") == skill_id), None)
            if not target or target.get("status") != expected:
                return False
            before = dict(target)
            target["status"] = status
            target["revision"] = int(target.get("revision", 1)) + 1
            target["updated_at"] = _now()
            self._write_unlocked(skills)
            self._history(status, skill_id, before, target)
            return True

    def resolve_review(self, skill_id: str) -> bool:
        """Clear a failure-triggered review flag after operator inspection."""
        with self._lock:
            skills = self._read_unlocked()
            target = next((item for item in skills if item.get("skill_id") == skill_id), None)
            if target is None or target.get("status") != "approved":
                return False
            before = dict(target)
            target["review_required"] = False
            target["revision"] = int(target.get("revision", 1)) + 1
            target["updated_at"] = _now()
            self._write_unlocked(skills)
            self._history("review_resolved", skill_id, before, target)
            return True

    def set_locked(self, skill_id: str, locked: bool) -> bool:
        return self._set_flag(skill_id, "locked", locked)

    def set_pinned(self, skill_id: str, pinned: bool) -> bool:
        return self._set_flag(skill_id, "pinned", pinned)

    def _set_flag(self, skill_id: str, key: str, enabled: bool) -> bool:
        if type(enabled) is not bool:
            raise ValueError(f"{key} must be a boolean")
        with self._lock:
            skills = self._read_unlocked()
            target = next((item for item in skills if item.get("skill_id") == skill_id), None)
            if not target:
                return False
            before = dict(target)
            target[key] = enabled
            target["revision"] = int(target.get("revision", 1)) + 1
            target["updated_at"] = _now()
            self._write_unlocked(skills)
            self._history(key, skill_id, before, target)
            return True

    def record_use(self, skill_id: str, outcome: str) -> bool:
        """Track outcomes; repeated failures flag a skill for human review."""
        if outcome not in {"completed", "failed", "partial", "cancelled"}:
            raise ValueError("outcome must be completed, failed, partial, or cancelled")
        with self._lock:
            skills = self._read_unlocked()
            target = next((item for item in skills if item.get("skill_id") == skill_id), None)
            if not target or target.get("status") != "approved":
                return False
            before = dict(target)
            target["usage_count"] = int(target.get("usage_count", 0)) + 1
            if outcome == "completed":
                target["success_count"] = int(target.get("success_count", 0)) + 1
            elif outcome == "failed":
                target["failure_count"] = int(target.get("failure_count", 0)) + 1
            target["last_used_at"] = _now()
            target["last_outcome"] = outcome
            target["review_required"] = int(target.get("failure_count", 0)) >= 2
            target["revision"] = int(target.get("revision", 1)) + 1
            target["updated_at"] = _now()
            self._write_unlocked(skills)
            self._history("usage:" + outcome, skill_id, before, target)
            return True

    def rollback(self, skill_id: str, revision_id: str | None = None) -> bool:
        """Restore a prior content revision as a new revision; preserve operator policy."""
        records = self.history(skill_id, limit=500)
        candidates = [
            item for item in records
            if item.get("before") is not None
            and item.get("action") == "revise"
            and (revision_id is None or item.get("revision_id") == revision_id)
        ]
        if not candidates:
            return False
        snapshot = candidates[-1]["before"]
        with self._lock:
            skills = self._read_unlocked()
            current = next((item for item in skills if item.get("skill_id") == skill_id), None)
            if current is None:
                return False
            before = dict(current)
            if current.get("locked"):
                raise PermissionError("skill is locked; unlock it before rolling back")
            if current.get("status") == "approved":
                raise PermissionError(
                    "approved skill content cannot be rolled back directly; create a reviewed proposal revision"
                )
            for key in (
                "name", "description", "version", "required_permissions",
                "parameters_schema", "reusable_steps", "expected_outputs", "lessons",
                "trace_fingerprint",
            ):
                if key in snapshot:
                    current[key] = snapshot[key]
            current["revision"] = int(before.get("revision", 1)) + 1
            current["updated_at"] = _now()
            self._write_unlocked(skills)
            self._history("rollback", skill_id, before, current)
            return True


class ExperienceLearningEngine:
    """Extracts reusable procedures from completed, repeatable tool traces."""

    MIN_ACTIONS = 3
    MIN_DISTINCT_TOOLS = 2

    def __init__(self, skill_store: SkillStore):
        self.skill_store = skill_store

    def extract_skill_from_trace(
        self,
        session_id: str,
        task_objective: str,
        messages: list[Any],
        events: list[dict[str, Any]],
        *,
        source_task_id: str | None = None,
    ) -> ProposedSkill | None:
        """Create a *proposal* only after enough distinct tool actions completed.

        Run-specific values, raw output, and failure text are excluded. Failures
        are reduced to general categories so a future reviewer can understand
        recurring pitfalls without retaining incident transcripts.
        """
        reusable_steps: list[dict[str, Any]] = []
        permissions_needed: set[str] = set()
        lessons: list[dict[str, str]] = []
        action_names: list[str] = []

        for message in messages:
            role = getattr(message, "role", None) or (
                message.get("role") if isinstance(message, dict) else ""
            )
            if role != "tool":
                continue
            content = getattr(message, "content", "") if not isinstance(message, dict) else message.get("content", "")
            try:
                data = json.loads(content) if isinstance(content, str) else content
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue

            tool_name = data.get("tool", "")
            if not isinstance(tool_name, str) or not tool_name or tool_name.startswith("agent."):
                continue
            status = data.get("status")
            if status in {"failed", "blocked", "uncertain", "partial"}:
                category = self._failure_category(data.get("error") or data.get("reason"))
                lessons.append({
                    "action": tool_name[:160],
                    "failure_category": category,
                    "rule": self._failure_rule(category),
                })
                continue
            if status not in (None, "completed"):
                continue

            permission = self._permission_for_tool(tool_name)
            permissions_needed.add(permission)
            result_data = data.get("result")
            result_fields = sorted(str(key)[:80] for key in result_data)[:20] if isinstance(result_data, dict) else []
            arguments = data.get("arguments")
            if isinstance(arguments, dict):
                sanitized_arguments = self._argument_schema(self._redact_secrets(arguments))
            else:
                sanitized_arguments = {}
            reusable_steps.append({
                "position": len(reusable_steps) + 1,
                "action": tool_name[:160],
                "sanitized_arguments": sanitized_arguments,
                "expected_result_fields": result_fields,
                "requires_fresh_observation": tool_name.startswith("computer."),
                "revalidate_permissions": True,
            })
            action_names.append(tool_name)

        if len(reusable_steps) < self.MIN_ACTIONS:
            return None
        if len(set(action_names)) < self.MIN_DISTINCT_TOOLS:
            return None

        fingerprint_payload = {
            "steps": [
                {
                    "action": item["action"],
                    "arguments": item["sanitized_arguments"],
                    "outputs": item["expected_result_fields"],
                }
                for item in reusable_steps
            ],
            "lessons": lessons,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        existing = self.skill_store.find_by_fingerprint(fingerprint)
        if existing:
            self.skill_store.record_evidence(existing["skill_id"], source_task_id)
            return None

        labels = [name.split(".")[-1].replace("_", " ") for name in action_names[:3]]
        sequence = " → ".join(labels)
        skill = ProposedSkill(
            skill_id=f"skill_{uuid.uuid4().hex[:10]}",
            name=f"Workflow: {sequence}"[:120],
            description=(
                f"Reusable procedure distilled from {len(reusable_steps)} completed tool actions. "
                "Review the steps and permissions before approval."
            ),
            version="1.0.0",
            source_session_id=session_id,
            source_task_id=source_task_id,
            trace_fingerprint=fingerprint,
            required_permissions=sorted(permissions_needed),
            parameters_schema={"workspace_relative": "string"},
            reusable_steps=reusable_steps[:20],
            expected_outputs=sorted({
                field for item in reusable_steps for field in item["expected_result_fields"]
            })[:20],
            lessons=lessons[:12],
            status="proposed",
            evidence_count=1,
            source_task_ids=[source_task_id] if source_task_id else [],
        )
        self.skill_store.save(skill)
        return skill

    @staticmethod
    def _permission_for_tool(tool_name: str) -> str:
        try:
            if tool_name.startswith(("workspace.", "computer.")):
                from .tools import tool_catalog
                operation = tool_name.removeprefix("workspace.")
                permission = tool_catalog()["operations"].get(operation, {}).get("permission")
                if permission:
                    return str(permission)
            if tool_name.startswith("application."):
                from .applications.dispatch import decode_tool_id
                from .applications.registry import get_application_registry
                decoded = decode_tool_id(tool_name)
                if decoded:
                    connector = get_application_registry().get(decoded[0])
                    if connector:
                        action = next(
                            (item for item in connector.manifest.actions if item.action_id == decoded[1]),
                            None,
                        )
                        if action:
                            return str(action.permission)
        except Exception:
            pass
        prefix = tool_name.split(".", 1)[0]
        read_indicators = ("list", "read", "extract", "inspect", "enumerate", "scan", "get", "query", "search", "find")
        return f"{prefix}.read" if any(token in tool_name.casefold() for token in read_indicators) else f"{prefix}.write"

    @staticmethod
    def _failure_category(error: Any) -> str:
        text = str(error or "").casefold()
        if any(word in text for word in ("permission", "denied", "approval")):
            return "permission"
        if any(word in text for word in ("not found", "missing", "unavailable")):
            return "unavailable"
        if any(word in text for word in ("timeout", "timed out")):
            return "timeout"
        if any(word in text for word in ("unsupported", "not exposed", "not registered")):
            return "unsupported"
        if any(word in text for word in ("verify", "verification", "mismatch")):
            return "verification"
        return "other"

    @staticmethod
    def _failure_rule(category: str) -> str:
        return {
            "permission": "Request the required operator approval before retrying a protected action.",
            "unavailable": "Re-discover the target and check installation or availability before retrying.",
            "timeout": "Re-observe the target and verify its state before deciding whether retrying is safe.",
            "unsupported": "Inspect the current capability contract and choose a registered supported action.",
            "verification": "Inspect the artifact or application state independently before reporting success.",
            "other": "Treat the action as unresolved; inspect fresh state and evidence before retrying.",
        }.get(category, "Inspect fresh state and evidence before retrying.")

    @staticmethod
    def _argument_schema(data: Any) -> Any:
        if isinstance(data, dict):
            return {
                str(key): ExperienceLearningEngine._argument_schema(value)
                for key, value in data.items()
            }
        if isinstance(data, list):
            return {
                "type": "array",
                "items": ExperienceLearningEngine._argument_schema(data[0]) if data else "any",
            }
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
        if isinstance(data, dict):
            return {
                str(key): (
                    "***REDACTED***"
                    if any(marker in str(key).casefold() for marker in (
                        "key", "secret", "token", "pass", "credential", "authorization",
                    ))
                    else ExperienceLearningEngine._redact_secrets(value)
                )
                for key, value in data.items()
            }
        if isinstance(data, list):
            return [ExperienceLearningEngine._redact_secrets(item) for item in data]
        if isinstance(data, str) and len(data) > 30 and re.search(
            r"(?:api|token|secret)[-_a-zA-Z0-9]{16,}", data, re.IGNORECASE
        ):
            return "***REDACTED_SECRET***"
        return data
