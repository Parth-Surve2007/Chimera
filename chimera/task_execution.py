"""Durable task and step state for the existing AgentRuntime tool lifecycle."""
from __future__ import annotations
import json, sqlite3, uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

TASK_STATES={"draft","ready","running","waiting_approval","blocked","failed","completed","cancelled"}
STEP_STATES={"pending","running","waiting_approval","blocked","failed","completed","cancelled"}
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

    def apply_runtime_event(self, task_id, runtime_event_id, event_type, payload):
        with sqlite3.connect(self.db_path) as db:
            if db.execute("SELECT 1 FROM agent_task_processed_events WHERE runtime_event_id=?", (runtime_event_id,)).fetchone(): return
            self._event(db, task_id, event_type, payload); db.execute("INSERT INTO agent_task_processed_events VALUES(?,?)", (runtime_event_id, task_id))
            invocation = payload.get("invocation_id")
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
        if mode == "plan" and permission not in {"workspace.read","computer.observe"}: raise PermissionError("Plan Mode is read-only")
        return True
