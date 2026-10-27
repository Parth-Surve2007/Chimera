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
    def record(self, item: AuditRecord):
        import json
        with sqlite3.connect(self.db_path) as db: db.execute("INSERT OR REPLACE INTO audits VALUES (?, ?)", (item.task_id, json.dumps(asdict(item)))); db.commit()
    def event(self, candidate_id: str, event: str, outcome: str, detail: str = ""):
        with sqlite3.connect(self.db_path) as db: db.execute("INSERT INTO audit_events(candidate_id,event,outcome,detail,created_at) VALUES (?,?,?,?,?)", (candidate_id, event, outcome, detail[:1000], now_iso())); db.commit()
    def events(self, candidate_id: str):
        with sqlite3.connect(self.db_path) as db: return db.execute("SELECT event,outcome,detail,created_at FROM audit_events WHERE candidate_id=? ORDER BY event_id", (candidate_id,)).fetchall()

