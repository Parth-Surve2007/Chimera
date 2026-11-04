"""Durable task and step state for the existing AgentRuntime tool lifecycle."""
from __future__ import annotations
import json, sqlite3, uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

TASK_STATES={"draft","ready","running","waiting_approval","blocked","failed","completed","cancelled"}
STEP_STATES={"pending","running","waiting_approval","blocked","failed","completed","cancelled"}
TODO_STATES={"pending","in_progress","completed","blocked","cancelled"}
MAX_TODOS=16
def _now(): return datetime.now(timezone.utc).isoformat()

@dataclass(frozen=True)
class Task:
    task_id: str; session_id: str; objective: str; mode: str; status: str; provider: str|None; model: str|None; created_at: str; completed_at: str|None

class TaskStore:
    def __init__(self, db_path: Path):
        self.db_path=Path(db_path); self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS agent_tasks(task_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,objective TEXT NOT NULL,mode TEXT NOT NULL,status TEXT NOT NULL,provider TEXT,model TEXT,created_at TEXT NOT NULL,completed_at TEXT);
            CREATE TABLE IF NOT EXISTS agent_task_steps(step_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,position INTEGER NOT NULL,description TEXT NOT NULL,tool TEXT,arguments TEXT,result TEXT,status TEXT NOT NULL,approval_required INTEGER NOT NULL DEFAULT 0,invocation_id TEXT,error TEXT,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_task_events(event_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,event_type TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_task_processed_events(runtime_event_id TEXT PRIMARY KEY,task_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_task_todos(todo_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,position INTEGER NOT NULL,content TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_agent_task_todos_task ON agent_task_todos(task_id,position);
            """)
    def create(self, session_id, objective, mode="plan", provider=None, model=None):
        if mode not in {"plan","build"}: raise ValueError("task mode must be plan or build")
        task_id=uuid.uuid4().hex; now=_now()
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?)",(task_id,session_id,objective,mode,"draft",provider,model,now,None)); self._event(db,task_id,"task.created",{"mode":mode})
        return task_id
    def get(self, task_id):
        with sqlite3.connect(self.db_path) as db: row=db.execute("SELECT task_id,session_id,objective,mode,status,provider,model,created_at,completed_at FROM agent_tasks WHERE task_id=?",(task_id,)).fetchone()
        return Task(*row) if row else None
    def latest_for_session(self, session_id):
        with sqlite3.connect(self.db_path) as db: row=db.execute("SELECT task_id FROM agent_tasks WHERE session_id=? ORDER BY created_at DESC LIMIT 1",(session_id,)).fetchone()
        return self.get(row[0]) if row else None
    def transition(self, task_id, status):
        if status not in TASK_STATES: raise ValueError("invalid task state")
        task=self.get(task_id)
        if not task: raise KeyError("task not found")
        completed=_now() if status in {"completed","failed","cancelled"} else None
        with sqlite3.connect(self.db_path) as db: db.execute("UPDATE agent_tasks SET status=?,completed_at=? WHERE task_id=?",(status,completed,task_id)); self._event(db,task_id,"task.state",{"status":status})
    def add_step(self, task_id, description, tool=None, arguments=None, approval_required=False):
        task=self.get(task_id)
        if not task: raise KeyError("task not found")
        with sqlite3.connect(self.db_path) as db:
            position=db.execute("SELECT COALESCE(MAX(position),0)+1 FROM agent_task_steps WHERE task_id=?",(task_id,)).fetchone()[0]; step_id=uuid.uuid4().hex
            db.execute("INSERT INTO agent_task_steps VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(step_id,task_id,position,description,tool,json.dumps(arguments or {},separators=(",",":")),None,"pending",int(approval_required),None,None,_now()))
        return step_id
    def steps(self, task_id):
        with sqlite3.connect(self.db_path) as db: rows=db.execute("SELECT step_id,position,description,tool,arguments,result,status,approval_required,invocation_id,error,updated_at FROM agent_task_steps WHERE task_id=? ORDER BY position",(task_id,)).fetchall()
        return [dict(zip(("step_id","position","description","tool","arguments","result","status","approval_required","invocation_id","error","updated_at"),row)) for row in rows]
    def step_transition(self, step_id, status, result=None, error=None, invocation_id=None):
        if status not in STEP_STATES: raise ValueError("invalid step state")
        with sqlite3.connect(self.db_path) as db: db.execute("UPDATE agent_task_steps SET status=?,result=?,error=?,invocation_id=?,updated_at=? WHERE step_id=?",(status,json.dumps(result) if result is not None else None,error,invocation_id,_now(),step_id))

    def write_todos(self, task_id, todos):
        """Persist a bounded checklist; unchanged items retain stable IDs."""
        if not self.get(task_id):
            raise KeyError("task not found")
        if not isinstance(todos, list) or not todos or len(todos) > MAX_TODOS:
            raise ValueError(f"todos must be a non-empty list of at most {MAX_TODOS} items")
        cleaned, seen, active = [], set(), 0
        for position, item in enumerate(todos, 1):
            if not isinstance(item, dict) or set(item) - {"content", "status"}:
                raise ValueError("each todo must contain only content and status")
            content, status = item.get("content"), item.get("status", "pending")
            if not isinstance(content, str) or not content.strip() or len(content.strip()) > 400:
                raise ValueError("todo content must be 1 to 400 characters")
            if not isinstance(status, str) or status not in TODO_STATES - {"cancelled"}:
                raise ValueError("todo status must be pending, in_progress, completed, or blocked")
            key = " ".join(content.split()).casefold()
            if key in seen:
                raise ValueError("todo items must be unique")
            seen.add(key)
            active += status == "in_progress"
            cleaned.append((position, content.strip(), status, key))
        if active > 1:
            raise ValueError("only one todo may be in_progress at a time")
        now = _now()
        with sqlite3.connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT todo_id,content,status FROM agent_task_todos WHERE task_id=?", (task_id,)
            ).fetchall()
            existing = {" ".join(row[1].split()).casefold(): row for row in rows}
            retained = set()
            for position, content, status, key in cleaned:
                previous = existing.get(key)
                if previous:
                    todo_id = previous[0]
                    db.execute(
                        "UPDATE agent_task_todos SET position=?,content=?,status=?,updated_at=? WHERE todo_id=?",
                        (position, content, status, now, todo_id),
                    )
                else:
                    todo_id = uuid.uuid4().hex
                    db.execute(
                        "INSERT INTO agent_task_todos VALUES(?,?,?,?,?,?,?)",
                        (todo_id, task_id, position, content, status, now, now),
                    )
                retained.add(todo_id)
            for todo_id, _, old_status in rows:
                if todo_id not in retained and old_status != "cancelled":
                    db.execute("UPDATE agent_task_todos SET status='cancelled',updated_at=? WHERE todo_id=?", (now, todo_id))
            self._event(db, task_id, "task.todos.updated", {
                "count": len(cleaned),
                "completed": sum(s == "completed" for _, _, s, _ in cleaned),
                "in_progress": active,
                "blocked": sum(s == "blocked" for _, _, s, _ in cleaned),
            })
        return self.todos(task_id)

    def todos(self, task_id, include_cancelled=False):
        if not self.get(task_id):
            raise KeyError("task not found")
        query = "SELECT todo_id,position,content,status,created_at,updated_at FROM agent_task_todos WHERE task_id=?"
        if not include_cancelled:
            query += " AND status!='cancelled'"
        query += " ORDER BY position,created_at,todo_id"
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute(query, (task_id,)).fetchall()
        keys = ("todo_id","position","content","status","created_at","updated_at")
        return [dict(zip(keys, row)) for row in rows]

    def apply_runtime_event(self, task_id, runtime_event_id, event_type, payload):
        with sqlite3.connect(self.db_path) as db:
            if db.execute("SELECT 1 FROM agent_task_processed_events WHERE runtime_event_id=?", (runtime_event_id,)).fetchone(): return
            self._event(db, task_id, event_type, payload); db.execute("INSERT INTO agent_task_processed_events VALUES(?,?)", (runtime_event_id, task_id))
            invocation = payload.get("invocation_id")
            if str(payload.get("tool") or "").startswith("agent.todo_"):
                return
            if event_type == "tool.call.requested":
                position = db.execute("SELECT COALESCE(MAX(position),0)+1 FROM agent_task_steps WHERE task_id=?", (task_id,)).fetchone()[0]
                db.execute("INSERT INTO agent_task_steps VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, task_id, position, payload.get("tool", "tool invocation"), payload.get("tool"), json.dumps(payload.get("arguments", {})), None, "pending", 0, invocation, None, _now()))
            elif invocation and event_type in {"tool.started", "tool.completed", "tool.failed"}:
                state = "running" if event_type == "tool.started" else "completed" if payload.get("status") == "completed" else "blocked" if payload.get("status") == "blocked" else "failed"
                db.execute("UPDATE agent_task_steps SET status=?,result=?,error=?,updated_at=? WHERE task_id=? AND invocation_id=?", (state, json.dumps(payload.get("data")) if payload.get("data") is not None else None, payload.get("error"), _now(), task_id, invocation))
    @staticmethod
    def _event(db, task_id, event_type, payload): db.execute("INSERT INTO agent_task_events VALUES(?,?,?,?,?)",(uuid.uuid4().hex,task_id,event_type,json.dumps(payload,separators=(",",":")),_now()))

class TaskPolicy:
    @staticmethod
    def validate(mode, tool, permission):
        from .applications.dispatch import APPLICATION_READ_PERMISSIONS
        allowed_in_plan = {"workspace.read", "computer.observe"}.union(APPLICATION_READ_PERMISSIONS)
        if mode == "plan" and permission not in allowed_in_plan:
            raise PermissionError("Plan Mode is read-only")
        return True
