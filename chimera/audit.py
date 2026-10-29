import sqlite3
import uuid, time, os
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path
from .models import now_iso

class ManifestAnchorError(ValueError): pass

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
            db.execute("CREATE TABLE IF NOT EXISTS promotion_records (promotion_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL UNIQUE, candidate_id TEXT NOT NULL, version TEXT NOT NULL, session_id TEXT, run_id TEXT NOT NULL, owner_token TEXT, fencing_token INTEGER, revision INTEGER, workspace TEXT NOT NULL, destination TEXT NOT NULL, manifest TEXT NOT NULL, manifest_digest TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"); db.commit()
            db.execute("CREATE TABLE IF NOT EXISTS clarification_sessions (session_id TEXT PRIMARY KEY, origin_run_id TEXT NOT NULL, current_run_id TEXT NOT NULL, original_request TEXT NOT NULL, questions TEXT NOT NULL, answers TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"); db.commit()
            columns = {row[1] for row in db.execute("PRAGMA table_info(clarification_sessions)")}
            if "owner_token" not in columns: db.execute("ALTER TABLE clarification_sessions ADD COLUMN owner_token TEXT")
            if "lease_expires_at" not in columns: db.execute("ALTER TABLE clarification_sessions ADD COLUMN lease_expires_at REAL")
            if "fencing_token" not in columns: db.execute("ALTER TABLE clarification_sessions ADD COLUMN fencing_token INTEGER NOT NULL DEFAULT 0")
            db.commit()
    @contextmanager
    def promotion_lock(self):
        """Cross-process lock shared by recovery and authoritative promotion."""
        path = self.db_path.with_suffix(self.db_path.suffix + ".promotion.lock")
        handle = open(path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0); handle.write(b"0"); handle.flush(); handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally: handle.close()
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
        with sqlite3.connect(self.db_path) as db: db.execute("INSERT INTO workflow_events(run_id,stage,outcome,detail,candidate_id,manifest_digest,created_at) VALUES (?,?,?,?,?,?,?)", (run_id, stage, outcome, detail, candidate_id, manifest_digest, now_iso())); db.commit()
    def workflow_finish(self, run_id: str, status: str, error: str = "", candidate_id: str | None = None, manifest_digest: str | None = None):
        with sqlite3.connect(self.db_path) as db: db.execute("UPDATE workflow_runs SET finished_at=?,status=?,error=?,candidate_id=?,manifest_digest=? WHERE run_id=?", (now_iso(), status, error[:1000], candidate_id, manifest_digest, run_id)); db.commit()
    def workflow_runs(self, limit: int = 50):
        with sqlite3.connect(self.db_path) as db: return db.execute("SELECT run_id,request_summary,started_at,finished_at,status,candidate_id,manifest_digest,error FROM workflow_runs ORDER BY started_at DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
    def workflow_events_for(self, run_id: str):
        with sqlite3.connect(self.db_path) as db: return db.execute("SELECT stage,outcome,detail,candidate_id,manifest_digest,created_at FROM workflow_events WHERE run_id=? ORDER BY event_id", (run_id,)).fetchall()
    def manifest_anchor(self, run_id: str):
        import json
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute("SELECT detail,candidate_id,manifest_digest FROM workflow_events WHERE run_id=? AND stage='manifest_anchor' ORDER BY event_id", (run_id,)).fetchall()
        if not rows: return None
        if len(rows) != 1: raise ManifestAnchorError("ambiguous durable manifest anchor")
        try: data = json.loads(rows[0][0])
        except (TypeError, json.JSONDecodeError) as exc: raise ManifestAnchorError("malformed durable manifest anchor") from exc
        if not isinstance(data, dict) or not isinstance(data.get("candidate_id"), str) or not isinstance(data.get("version"), str) or not isinstance(data.get("manifest"), list): raise ManifestAnchorError("invalid durable manifest anchor schema")
        if data["candidate_id"] != rows[0][1]: raise ManifestAnchorError("anchor candidate identity mismatch")
        data["candidate_id"] = rows[0][1]; data["manifest_digest"] = rows[0][2]; return data
    def clarification_create(self, session_id, run_id, original_request, questions):
        import json
        if len(original_request) > 20000: raise ValueError("original request exceeds persistence limit")
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO clarification_sessions(session_id,origin_run_id,current_run_id,original_request,questions,answers,state,revision,created_at,updated_at,owner_token,lease_expires_at,fencing_token) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (session_id, run_id, run_id, original_request, json.dumps(questions, separators=(",", ":")), "{}", "pending", 0, now_iso(), now_iso(), None, None, 0)); db.commit()
    def clarification_get(self, session_id):
        import json
        with sqlite3.connect(self.db_path) as db: row = db.execute("SELECT session_id,origin_run_id,current_run_id,original_request,questions,answers,state,revision,created_at,updated_at,owner_token,lease_expires_at,fencing_token FROM clarification_sessions WHERE session_id=?", (session_id,)).fetchone()
        if not row: return None
        try: questions, answers = json.loads(row[4]), json.loads(row[5])
        except (TypeError, json.JSONDecodeError) as exc: raise ValueError("clarification session data is malformed") from exc
        return {"session_id":row[0],"origin_run_id":row[1],"current_run_id":row[2],"original_request":row[3],"questions":questions,"answers":answers,"state":row[6],"revision":row[7],"created_at":row[8],"updated_at":row[9],"owner_token":row[10],"lease_expires_at":row[11],"fencing_token":row[12]}
    def clarification_claim(self, session_id, continuation_run_id, answers, owner_token=None, lease_seconds=120):
        import json
        if len(json.dumps(answers, separators=(",", ":"))) > 10000: raise ValueError("clarification answers exceed persistence limit")
        with sqlite3.connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE"); row = db.execute("SELECT state FROM clarification_sessions WHERE session_id=?", (session_id,)).fetchone()
            if not row: raise KeyError("clarification session not found")
            if row[0] != "pending": raise ValueError("clarification session is no longer pending")
            owner_token = owner_token or uuid.uuid4().hex
            db.execute("UPDATE clarification_sessions SET current_run_id=?,answers=?,state='resuming',revision=revision+1,owner_token=?,lease_expires_at=?,fencing_token=fencing_token+1,updated_at=? WHERE session_id=? AND state='pending'", (continuation_run_id, json.dumps(answers, separators=(",", ":")), owner_token, time.time() + lease_seconds, now_iso(), session_id)); db.commit()
        current = self.clarification_get(session_id); return {"owner_token": owner_token, "fencing_token": current["fencing_token"], "revision": current["revision"]}
    def clarification_renew(self, session_id, owner_token, lease_seconds=120):
        return self.clarification_renew_fenced(session_id, None, owner_token, None, lease_seconds)
    def clarification_renew_fenced(self, session_id, continuation_run_id, owner_token, fencing_token, revision=None, lease_seconds=120):
        with sqlite3.connect(self.db_path) as db:
            query = "UPDATE clarification_sessions SET lease_expires_at=?,updated_at=? WHERE session_id=? AND state='resuming' AND owner_token=? AND lease_expires_at>?"
            args = [time.time()+lease_seconds, now_iso(), session_id, owner_token, time.time()]
            if continuation_run_id is not None: query += " AND current_run_id=?"; args.append(continuation_run_id)
            if fencing_token is not None: query += " AND fencing_token=?"; args.append(fencing_token)
            if revision is not None: query += " AND revision=?"; args.append(revision)
            cur = db.execute(query, args)
            db.commit()
        if cur.rowcount != 1: raise ValueError("continuation ownership is stale or expired")
    def assert_ownership(self, session_id, continuation_run_id, owner_token, fencing_token, revision=None):
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT state,owner_token,lease_expires_at,fencing_token,current_run_id,revision FROM clarification_sessions WHERE session_id=?", (session_id,)).fetchone()
        if not row or row[0] != "resuming" or row[1] != owner_token or row[4] != continuation_run_id or row[3] != fencing_token or (revision is not None and row[5] != revision) or (row[2] is not None and row[2] <= time.time()):
            raise ValueError("continuation ownership is stale or expired")
        return True
    def recover_expired(self, session_id, continuation_run_id, expected_revision, answers):
        """Transfer only an expired generation; does not assert that replay is safe."""
        token = uuid.uuid4().hex
        with self.promotion_lock():
            with sqlite3.connect(self.db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT 1 FROM workflow_runs WHERE run_id=?", (continuation_run_id,)).fetchone():
                    db.rollback(); raise ValueError("continuation has persisted work and requires reconciliation")
                records = db.execute("SELECT state,destination,workspace FROM promotion_records WHERE run_id=?", (continuation_run_id,)).fetchall()
                if records:
                    db.rollback(); raise ValueError("continuation has promotion evidence and requires reconciliation")
                cur = db.execute("UPDATE clarification_sessions SET owner_token=?,lease_expires_at=?,fencing_token=fencing_token+1,revision=revision+1,state='recovery_required',updated_at=? WHERE session_id=? AND current_run_id=? AND revision=? AND state='resuming' AND lease_expires_at<?", (token, time.time()+120, now_iso(), session_id, continuation_run_id, expected_revision, time.time()))
                if cur.rowcount != 1: db.rollback(); raise ValueError("continuation is still owned, terminal, or revision has changed")
                db.commit()
        current = self.clarification_get(session_id); return {"owner_token": token, "fencing_token": current["fencing_token"], "revision": current["revision"]}
    def clarification_finish(self, session_id, state):
        with sqlite3.connect(self.db_path) as db: db.execute("UPDATE clarification_sessions SET state=?,updated_at=? WHERE session_id=?", (state, now_iso(), session_id)); db.commit()
    def clarification_recovery_required(self, session_id, reason):
        """Conditionally mark a claimed continuation as requiring reconciliation."""
        with sqlite3.connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM clarification_sessions WHERE session_id=?", (session_id,)).fetchone()
            if not row: raise KeyError("clarification session not found")
            if row[0] == "resuming":
                db.execute("UPDATE clarification_sessions SET state='recovery_required',updated_at=? WHERE session_id=? AND state='resuming'", (now_iso(), session_id))
            db.commit()
        return self.clarification_get(session_id)
    def clarification_pending(self):
        with sqlite3.connect(self.db_path) as db: ids = [row[0] for row in db.execute("SELECT session_id FROM clarification_sessions WHERE state IN ('pending','resuming','recovery_required') ORDER BY created_at")]
        return [self.clarification_get(session_id) for session_id in ids]
    def workflow_run_status(self, run_id):
        with sqlite3.connect(self.db_path) as db: row = db.execute("SELECT status FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
        return row[0] if row else None
    def promotion_intent(self, promotion_id, attempt_id, candidate_id, version, session_id, run_id, owner_token, fencing_token, revision, workspace, destination, manifest, digest):
        import json
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO promotion_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (promotion_id, attempt_id, candidate_id, version, session_id, run_id, owner_token, fencing_token, revision, str(workspace), str(destination), json.dumps(manifest, sort_keys=True), digest, "intent_recorded", now_iso(), now_iso())); db.commit()
    def promotion_get(self, promotion_id):
        import json
        with sqlite3.connect(self.db_path) as db: row = db.execute("SELECT promotion_id,attempt_id,candidate_id,version,session_id,run_id,owner_token,fencing_token,revision,workspace,destination,manifest,manifest_digest,state,created_at,updated_at FROM promotion_records WHERE promotion_id=?", (promotion_id,)).fetchone()
        if not row: return None
        item = dict(zip(("promotion_id","attempt_id","candidate_id","version","session_id","run_id","owner_token","fencing_token","revision","workspace","destination","manifest","manifest_digest","state","created_at","updated_at"), row)); item["manifest"] = json.loads(item["manifest"]); return item
    def promotion_transition(self, promotion_id, expected, state, *, attempt_id=None, session_id=None, run_id=None, owner_token=None, fencing_token=None, revision=None, candidate_id=None, version=None, digest=None, recovery=False):
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT attempt_id,session_id,run_id,owner_token,fencing_token,revision,candidate_id,version,manifest_digest,state FROM promotion_records WHERE promotion_id=?", (promotion_id,)).fetchone()
            if not row: raise KeyError("promotion record not found")
            if row[9] != expected: 
                if row[9] == state and all(value is not None for value in (attempt_id, run_id, candidate_id, version, digest)): return
                raise ValueError("promotion transition conflict")
            supplied = (attempt_id, session_id, run_id, owner_token, fencing_token, revision, candidate_id, version, digest)
            required = (attempt_id, run_id, candidate_id, version, digest) if row[1] is None else supplied
            if any(value is None for value in required): raise ValueError("complete promotion authority is required")
            if (attempt_id, session_id, run_id, owner_token, fencing_token, revision, candidate_id, version, digest) != (row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8]): raise ValueError("promotion authority or immutable intent mismatch")
            if row[1] is not None:
                session = db.execute("SELECT current_run_id,owner_token,fencing_token,revision,state,lease_expires_at FROM clarification_sessions WHERE session_id=?", (session_id,)).fetchone()
                if not session or (session[0], session[1], session[2], session[3]) != (run_id, owner_token, fencing_token, revision) or session[4] != "resuming" or (session[5] is not None and session[5] <= time.time()):
                    raise ValueError("current continuation ownership does not authorize promotion transition")
            if not recovery and row[1] is not None and state in ("promotion_observed", "finalized") and row[3] is None: raise ValueError("promotion owner is no longer active")
            cur = db.execute("UPDATE promotion_records SET state=?,updated_at=? WHERE promotion_id=? AND state=? AND attempt_id=? AND run_id=? AND candidate_id=? AND version=? AND manifest_digest=? AND revision=?", (state, now_iso(), promotion_id, expected, attempt_id, run_id, candidate_id, version, digest, revision)); db.commit()
        if cur.rowcount != 1 and self.promotion_get(promotion_id) and self.promotion_get(promotion_id)["state"] != state: raise ValueError("promotion transition conflict")
    def verify_promotion_destination(self, promotion_id):
        """Verify a destination against immutable intent; never infers success from existence."""
        record = self.promotion_get(promotion_id)
        if not record: raise KeyError("promotion record not found")
        from .registry import Registry
        destination = Path(record["destination"])
        if not destination.is_dir(): return False
        manifest, digest = Registry.canonical_manifest(destination)
        metadata_path = destination / ".chimera-candidate.json"
        if not metadata_path.is_file(): return False
        import json
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        return (manifest == record["manifest"] and digest == record["manifest_digest"] and metadata.get("candidate_id") == record["candidate_id"] and metadata.get("version") == record["version"] and metadata.get("manifest_digest") == digest)
    def reconcile_promotion(self, promotion_id):
        """Inspect and idempotently reconcile only an exact promoted artifact."""
        with self.promotion_lock():
            record = self.promotion_get(promotion_id)
            if not record: raise KeyError("promotion record not found")
            if record["state"] in ("finalized", "recovery_required"): return {"state": record["state"], "verified": False}
            verified = self.verify_promotion_destination(promotion_id)
            if verified:
                self.promotion_transition(promotion_id, record["state"], "promotion_observed", attempt_id=record["attempt_id"], session_id=record["session_id"], run_id=record["run_id"], owner_token=record["owner_token"], fencing_token=record["fencing_token"], revision=record["revision"], candidate_id=record["candidate_id"], version=record["version"], digest=record["manifest_digest"], recovery=True)
                return {"state": "promotion_observed", "verified": True, "promotion_id": promotion_id}
            self.promotion_transition(promotion_id, record["state"], "recovery_required", attempt_id=record["attempt_id"], session_id=record["session_id"], run_id=record["run_id"], owner_token=record["owner_token"], fencing_token=record["fencing_token"], revision=record["revision"], candidate_id=record["candidate_id"], version=record["version"], digest=record["manifest_digest"], recovery=True)
            return {"state": "recovery_required", "verified": False, "promotion_id": promotion_id}
    def finalize_workflow(self, run_id, expected_status, terminal_status, stage, detail, candidate_id=None, digest=None, session_id=None, owner_token=None, fencing_token=None, revision=None):
        """Atomically persist terminal run/event and, when supplied, session state."""
        with sqlite3.connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
            if not row or row[0] != expected_status: raise ValueError("workflow run is not owned or is already terminal")
            if session_id:
                s = db.execute("SELECT state,owner_token,lease_expires_at,fencing_token,revision FROM clarification_sessions WHERE session_id=? AND current_run_id=?", (session_id, run_id)).fetchone()
                if not s or s[0] != "resuming" or s[1] != owner_token or s[3] != fencing_token or (revision is not None and s[4] != revision) or (s[2] is not None and s[2] <= time.time()): raise ValueError("continuation ownership is stale or expired")
            db.execute("INSERT INTO workflow_events(run_id,stage,outcome,detail,candidate_id,manifest_digest,created_at) VALUES (?,?,?,?,?,?,?)", (run_id, stage, "completed", detail, candidate_id, digest, now_iso()))
            db.execute("UPDATE workflow_runs SET finished_at=?,status=?,error=?,candidate_id=?,manifest_digest=? WHERE run_id=? AND status=?", (now_iso(), terminal_status, "", candidate_id, digest, run_id, expected_status))
            if session_id:
                cur = db.execute("UPDATE clarification_sessions SET state='resolved',owner_token=NULL,lease_expires_at=NULL,updated_at=? WHERE session_id=? AND current_run_id=? AND state='resuming' AND owner_token=? AND fencing_token=? AND revision=?", (now_iso(), session_id, run_id, owner_token, fencing_token, revision))
                if cur.rowcount != 1: raise ValueError("clarification session ownership changed during finalization")
            db.commit()

