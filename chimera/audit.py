import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from .models import now_iso

@dataclass
class AuditRecord:
    task_id: str; capability_id: str | None; started_at: str; finished_at: str | None = None
    model_state: str = "unavailable"; api_requests: int = 0; known_cost: float | None = None
    command: str | None = None; exit_code: int | None = None; error: str | None = None
    duration_ms: int | None = None; verification: str = "not_run"; final_status: str = "pending"

class AuditStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        with sqlite3.connect(self.db_path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS audits (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS audit_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT, event TEXT NOT NULL, outcome TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL)"); db.commit()
            db.execute("CREATE TABLE IF NOT EXISTS workflow_runs (run_id TEXT PRIMARY KEY, request_summary TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, candidate_id TEXT, manifest_digest TEXT, error TEXT)"); db.commit()
            db.execute("CREATE TABLE IF NOT EXISTS workflow_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, stage TEXT NOT NULL, outcome TEXT NOT NULL, detail TEXT NOT NULL, candidate_id TEXT, manifest_digest TEXT, created_at TEXT NOT NULL)"); db.commit()
    def record(self, item: AuditRecord):
        import json
        with sqlite3.connect(self.db_path) as db: db.execute("INSERT OR REPLACE INTO audits VALUES (?, ?)", (item.task_id, json.dumps(asdict(item)))); db.commit()
    def event(self, candidate_id: str, event: str, outcome: str, detail: str = ""):
        with sqlite3.connect(self.db_path) as db: db.execute("INSERT INTO audit_events(candidate_id,event,outcome,detail,created_at) VALUES (?,?,?,?,?)", (candidate_id, event, outcome, detail[:1000], now_iso())); db.commit()
    def events(self, candidate_id: str):
        with sqlite3.connect(self.db_path) as db: return db.execute("SELECT event,outcome,detail,created_at FROM audit_events WHERE candidate_id=? ORDER BY event_id", (candidate_id,)).fetchall()
    def workflow_start(self, run_id: str, request: str):
        with sqlite3.connect(self.db_path) as db: db.execute("INSERT INTO workflow_runs(run_id,request_summary,started_at,status) VALUES (?,?,?,?)", (run_id, request[:240], now_iso(), "running")); db.commit()
    def workflow_event(self, run_id: str, stage: str, outcome: str, detail: str = "", candidate_id: str | None = None, manifest_digest: str | None = None):
        with sqlite3.connect(self.db_path) as db: db.execute("INSERT INTO workflow_events(run_id,stage,outcome,detail,candidate_id,manifest_digest,created_at) VALUES (?,?,?,?,?,?,?)", (run_id, stage, outcome, detail[:1000], candidate_id, manifest_digest, now_iso())); db.commit()
    def workflow_finish(self, run_id: str, status: str, error: str = "", candidate_id: str | None = None, manifest_digest: str | None = None):
        with sqlite3.connect(self.db_path) as db: db.execute("UPDATE workflow_runs SET finished_at=?,status=?,error=?,candidate_id=?,manifest_digest=? WHERE run_id=?", (now_iso(), status, error[:1000], candidate_id, manifest_digest, run_id)); db.commit()
    def workflow_runs(self, limit: int = 50):
        with sqlite3.connect(self.db_path) as db: return db.execute("SELECT run_id,request_summary,started_at,finished_at,status,candidate_id,manifest_digest,error FROM workflow_runs ORDER BY started_at DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
    def workflow_events_for(self, run_id: str):
        with sqlite3.connect(self.db_path) as db: return db.execute("SELECT stage,outcome,detail,candidate_id,manifest_digest,created_at FROM workflow_events WHERE run_id=? ORDER BY event_id", (run_id,)).fetchall()

