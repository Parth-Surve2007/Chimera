"""Persistent, operator-owned goals and evidence for Chimera.

Goals are intentionally separate from session-scoped AgentRuntime tasks. A goal
represents a durable outcome; tasks are attempts or pieces of work that may
contribute evidence toward that outcome. No task or model response automatically
marks a goal completed.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GOAL_STATUSES = {"active", "paused", "completed", "archived"}
GOAL_PRIORITIES = {"low", "normal", "high", "critical"}
_TERMINAL = {"completed", "archived"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required_text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{field} must be at most {limit} characters")
    return value


def _optional_text(value: Any, field: str, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{field} must be at most {limit} characters")
    return value


def _string_list(values: Any, field: str, *, maximum: int, item_limit: int) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{field} must be a list of strings")
    if len(values) > maximum:
        raise ValueError(f"{field} cannot contain more than {maximum} items")
    result = []
    seen = set()
    for item in values:
        value = _required_text(item, field, item_limit)
        marker = value.casefold()
        if marker not in seen:
            result.append(value)
            seen.add(marker)
    return result


@dataclass(frozen=True)
class Goal:
    goal_id: str
    title: str
    description: str
    status: str
    priority: str
    project: str
    success_criteria: list[str]
    tags: list[str]
    evidence: list[str]
    completion_note: str | None
    created_at: str
    updated_at: str
    completed_at: str | None


@dataclass(frozen=True)
class GoalEvent:
    event_id: str
    goal_id: str
    event_type: str
    payload: dict[str, Any]
    created_at: str


class GoalStore:
    """SQLite-backed goal ledger sharing a database with AgentRuntime when desired."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS chimera_goals (
                    goal_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('active','paused','completed','archived')),
                    priority TEXT NOT NULL CHECK(priority IN ('low','normal','high','critical')),
                    project TEXT NOT NULL DEFAULT '',
                    success_criteria_json TEXT NOT NULL DEFAULT '[]',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    completion_note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_chimera_goals_status_updated
                    ON chimera_goals(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_chimera_goals_project
                    ON chimera_goals(project);
                CREATE TABLE IF NOT EXISTS chimera_goal_events (
                    event_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(goal_id) REFERENCES chimera_goals(goal_id)
                );
                CREATE INDEX IF NOT EXISTS idx_chimera_goal_events_goal
                    ON chimera_goal_events(goal_id, created_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> Goal | None:
        if row is None:
            return None
        return Goal(
            goal_id=row["goal_id"],
            title=row["title"],
            description=row["description"],
            status=row["status"],
            priority=row["priority"],
            project=row["project"],
            success_criteria=json.loads(row["success_criteria_json"]),
            tags=json.loads(row["tags_json"]),
            evidence=json.loads(row["evidence_json"]),
            completion_note=row["completion_note"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _event(db: sqlite3.Connection, goal_id: str, event_type: str, payload: dict[str, Any]) -> None:
        db.execute(
            "INSERT INTO chimera_goal_events VALUES(?,?,?,?,?)",
            (
                uuid.uuid4().hex,
                goal_id,
                event_type,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                _now(),
            ),
        )

    def create(
        self,
        title: str,
        description: str = "",
        *,
        priority: str = "normal",
        project: str = "",
        success_criteria: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> Goal:
        title = _required_text(title, "title", 160)
        description = _optional_text(description, "description", 4000)
        project = _optional_text(project, "project", 160)
        if priority not in GOAL_PRIORITIES:
            raise ValueError(f"priority must be one of {sorted(GOAL_PRIORITIES)}")
        criteria = _string_list(success_criteria, "success_criteria", maximum=30, item_limit=500)
        normalized_tags = _string_list(tags, "tags", maximum=30, item_limit=48)
        goal_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO chimera_goals
                (goal_id,title,description,status,priority,project,success_criteria_json,tags_json,
                 evidence_json,completion_note,created_at,updated_at,completed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    goal_id, title, description, "active", priority, project,
                    json.dumps(criteria, ensure_ascii=False),
                    json.dumps(normalized_tags, ensure_ascii=False),
                    "[]", None, now, now, None,
                ),
            )
            self._event(db, goal_id, "goal.created", {
                "title": title, "priority": priority, "project": project,
            })
        goal = self.get(goal_id)
        assert goal is not None
        return goal

    def get(self, goal_id: str) -> Goal | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM chimera_goals WHERE goal_id=?", (goal_id,)).fetchone()
        return self._decode(row)

    def list(
        self,
        *,
        status: str | None = None,
        project: str | None = None,
        search: str | None = None,
        limit: int = 100,
    ) -> list[Goal]:
        if status is not None and status not in GOAL_STATUSES:
            raise ValueError(f"status must be one of {sorted(GOAL_STATUSES)}")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        clauses = []
        params: list[Any] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        if project is not None:
            clauses.append("project=?")
            params.append(_optional_text(project, "project", 160))
        if search:
            term = "%" + _required_text(search, "search", 160).replace("%", "\\%").replace("_", "\\_") + "%"
            clauses.append("(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' OR project LIKE ? ESCAPE '\\')")
            params.extend([term, term, term])
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM chimera_goals" + where
                + " ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 WHEN 'completed' THEN 2 ELSE 3 END,"
                + " CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,"
                + " updated_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._decode(row) for row in rows if row is not None]

    def update(
        self,
        goal_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        priority: str | None = None,
        project: str | None = None,
        success_criteria: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> Goal:
        current = self.get(goal_id)
        if current is None:
            raise KeyError("goal not found")
        if current.status in _TERMINAL:
            raise ValueError("completed or archived goals must be reopened before editing")
        fields: dict[str, Any] = {}
        if title is not None:
            fields["title"] = _required_text(title, "title", 160)
        if description is not None:
            fields["description"] = _optional_text(description, "description", 4000)
        if priority is not None:
            if priority not in GOAL_PRIORITIES:
                raise ValueError(f"priority must be one of {sorted(GOAL_PRIORITIES)}")
            fields["priority"] = priority
        if project is not None:
            fields["project"] = _optional_text(project, "project", 160)
        if success_criteria is not None:
            fields["success_criteria_json"] = json.dumps(
                _string_list(success_criteria, "success_criteria", maximum=30, item_limit=500),
                ensure_ascii=False,
            )
        if tags is not None:
            fields["tags_json"] = json.dumps(
                _string_list(tags, "tags", maximum=30, item_limit=48),
                ensure_ascii=False,
            )
        if not fields:
            return current
        now = _now()
        assignments = ", ".join(f"{key}=?" for key in fields)
        values = list(fields.values())
        with self._connect() as db:
            db.execute(
                f"UPDATE chimera_goals SET {assignments},updated_at=? WHERE goal_id=?",
                (*values, now, goal_id),
            )
            self._event(db, goal_id, "goal.updated", {
                "fields": sorted(fields.keys()),
            })
        updated = self.get(goal_id)
        assert updated is not None
        return updated

    def transition(
        self,
        goal_id: str,
        status: str,
        *,
        reason: str | None = None,
        evidence: list[str] | None = None,
    ) -> Goal:
        if status not in GOAL_STATUSES:
            raise ValueError(f"status must be one of {sorted(GOAL_STATUSES)}")
        current = self.get(goal_id)
        if current is None:
            raise KeyError("goal not found")
        if current.status == status:
            return current

        allowed = {
            "active": {"paused", "completed", "archived"},
            "paused": {"active", "completed", "archived"},
            "completed": {"active", "archived"},
            "archived": {"active"},
        }
        if status not in allowed[current.status]:
            raise ValueError(f"transition from {current.status} to {status} is not allowed")

        note = _optional_text(reason, "reason", 2000)
        if status == "completed":
            normalized_evidence = _string_list(evidence, "evidence", maximum=50, item_limit=1000)
            if not normalized_evidence:
                raise ValueError("completion requires at least one evidence reference")
        else:
            normalized_evidence = current.evidence

        if current.status in {"completed", "archived"} and status == "active" and not note:
            raise ValueError("reopening a completed or archived goal requires a reason")

        now = _now()
        completed_at = now if status == "completed" else None if status == "active" else current.completed_at
        reopening = status == "active" and current.status in _TERMINAL
        stored_evidence = (
            normalized_evidence if status == "completed"
            else [] if reopening
            else current.evidence
        )
        completion_note = note or None if status == "completed" else None if status == "active" else current.completion_note
        with self._connect() as db:
            db.execute(
                """UPDATE chimera_goals
                SET status=?, evidence_json=?, completion_note=?, completed_at=?, updated_at=?
                WHERE goal_id=?""",
                (
                    status,
                    json.dumps(stored_evidence, ensure_ascii=False),
                    completion_note,
                    completed_at,
                    now,
                    goal_id,
                ),
            )
            payload: dict[str, Any] = {"from": current.status, "to": status}
            if note:
                payload["reason"] = note
            if status == "completed":
                payload["evidence"] = normalized_evidence
            if reopening:
                payload["previous_completion_evidence"] = current.evidence
                if current.completion_note:
                    payload["previous_completion_note"] = current.completion_note
            self._event(db, goal_id, "goal.status_changed", payload)
        updated = self.get(goal_id)
        assert updated is not None
        return updated

    def events(self, goal_id: str | None = None, *, limit: int = 200) -> list[GoalEvent]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer between 1 and 1000")
        query = "SELECT * FROM chimera_goal_events"
        params: list[Any] = []
        if goal_id is not None:
            query += " WHERE goal_id=?"
            params.append(goal_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return [
            GoalEvent(
                event_id=row["event_id"],
                goal_id=row["goal_id"],
                event_type=row["event_type"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def stats(self) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT status,COUNT(*) AS count FROM chimera_goals GROUP BY status"
            ).fetchall()
        result = {status: 0 for status in GOAL_STATUSES}
        result.update({row["status"]: row["count"] for row in rows})
        result["total"] = sum(result.values())
        return result
