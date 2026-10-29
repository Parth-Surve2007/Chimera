"""Tamperevident, versioned capability storage."""
import hashlib, json, os, shutil, sqlite3, uuid
from pathlib import Path
from .models import Capability, Lifecycle, now_iso, transition_allowed

class IntegrityError(ValueError): pass

class Registry:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).resolve(); self.data_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts = self.data_dir / "artifacts"; self.artifacts.mkdir(exist_ok=True)
        self.candidates = self.data_dir / "candidates"; self.candidates.mkdir(exist_ok=True)
        self.db_path = self.data_dir / "chimera.db"
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS capabilities (id TEXT PRIMARY KEY, version TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL, artifact_hash TEXT, manifest TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS versions (id TEXT NOT NULL, version TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL, artifact_hash TEXT NOT NULL, manifest TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(id, version))")
            db.execute("CREATE TABLE IF NOT EXISTS registry_events (event_id TEXT PRIMARY KEY, capability_id TEXT, version TEXT, event TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL)"); db.commit()
    def _connect(self):
        db = sqlite3.connect(self.db_path); db.row_factory = sqlite3.Row; return db
    @staticmethod
    def canonical_manifest(source: Path):
        source = Path(source).resolve()
        if not source.is_dir(): raise IntegrityError("artifact source must be a directory")
        entries = []
        for p in source.rglob("*"):
            if p.name == ".chimera-candidate.json": continue
            if p.is_symlink(): raise IntegrityError(f"symbolic links are not allowed: {p}")
            if not p.is_file():
                if not p.is_dir(): raise IntegrityError(f"unsupported filesystem object: {p}")
                continue
            rel = p.relative_to(source).as_posix()
            if Path(rel).is_absolute() or ".." in Path(rel).parts: raise IntegrityError(f"invalid artifact path: {rel}")
            entries.append({"path": rel, "size": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
        entries.sort(key=lambda x: x["path"])
        encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
        return entries, hashlib.sha256(encoded).hexdigest()
    def _manifest(self, source: Path):
        return self.canonical_manifest(source)
    def _event(self, db, cid, version, event, detail=""):
        db.execute("INSERT INTO registry_events VALUES (?, ?, ?, ?, ?, ?)", (str(uuid.uuid4()), cid, version, event, detail, now_iso()))
    def verify(self, capability_id: str, version: str | None = None):
        with self._connect() as db: row = db.execute("SELECT version, artifact_hash, manifest FROM versions WHERE id=? AND version=COALESCE(?, version)", (capability_id, version)).fetchone()
        if not row: raise IntegrityError("capability version not found")
        actual, digest = self._manifest(self.artifacts / capability_id / row["version"])
        if digest != row["artifact_hash"] or actual != json.loads(row["manifest"]): raise IntegrityError(f"artifact integrity failure: {capability_id}@{row['version']}")
        return True
    def save(self, capability: Capability, artifact_dir: Path | None = None):
        digest = None; manifest = []
        if artifact_dir:
            manifest, digest = self._manifest(artifact_dir); target = self.artifacts / capability.capability_id / capability.version
            if target.exists(): raise IntegrityError("version already exists; refusing overwrite")
            stage = self.candidates / uuid.uuid4().hex; shutil.copytree(artifact_dir, stage, symlinks=True); target.parent.mkdir(parents=True, exist_ok=True)
            try: os.replace(stage, target)
            except Exception: shutil.rmtree(stage, ignore_errors=True); raise
        payload = json.dumps({**capability.__dict__, "status": capability.status.value})
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO capabilities VALUES (?, ?, ?, ?, ?, ?, ?)", (capability.capability_id, capability.version, capability.status.value, payload, digest, json.dumps(manifest), now_iso()))
            if digest: db.execute("INSERT OR REPLACE INTO versions VALUES (?, ?, ?, ?, ?, ?, ?)", (capability.capability_id, capability.version, capability.status.value, payload, digest, json.dumps(manifest), now_iso()))
            db.commit()
        return digest
    def accept(self, capability: Capability, candidate_dir: Path):
        if capability.status != Lifecycle.VALIDATING: raise IntegrityError("candidate must be validating before acceptance")
        manifest, digest = self._manifest(candidate_dir); target = self.artifacts / capability.capability_id / capability.version
        if target.exists(): raise IntegrityError("accepted version already exists; refusing overwrite")
        stage = self.candidates / uuid.uuid4().hex
        try:
            shutil.copytree(candidate_dir, stage, symlinks=True); staged, staged_digest = self._manifest(stage)
            if (manifest, digest) != (staged, staged_digest): raise IntegrityError("candidate changed during staging")
            target.parent.mkdir(parents=True, exist_ok=True); os.replace(stage, target); capability.status = Lifecycle.ACCEPTED
            payload = json.dumps({**capability.__dict__, "status": "accepted"})
            with self._connect() as db:
                db.execute("INSERT INTO versions VALUES (?, ?, ?, ?, ?, ?, ?)", (capability.capability_id, capability.version, "accepted", payload, digest, json.dumps(manifest), now_iso()))
                db.execute("INSERT OR REPLACE INTO capabilities VALUES (?, ?, ?, ?, ?, ?, ?)", (capability.capability_id, capability.version, "accepted", payload, digest, json.dumps(manifest), now_iso()))
                self._event(db, capability.capability_id, capability.version, "accepted"); db.commit()
            self.verify(capability.capability_id, capability.version); return digest
        except Exception:
            if stage.exists(): shutil.rmtree(stage, ignore_errors=True)
            raise
    def get(self, capability_id: str):
        with self._connect() as db: row = db.execute("SELECT payload FROM capabilities WHERE id=?", (capability_id,)).fetchone()
        if not row: return None
        data = json.loads(row["payload"]); data["status"] = Lifecycle(data["status"]); return Capability(**data)
    def list(self):
        with self._connect() as db: rows = db.execute("SELECT payload FROM capabilities").fetchall()
        rows.sort(key=lambda row: json.loads(row["payload"])["name"].casefold()); return [self.get(json.loads(r["payload"])["capability_id"]) for r in rows]
    def transition(self, capability_id: str, target: Lifecycle):
        cap = self.get(capability_id)
        if not cap or not transition_allowed(cap.status, target): raise ValueError(f"invalid lifecycle transition: {cap.status if cap else 'missing'} -> {target}")
        cap.status = target; self.save(cap); return cap
    def rollback(self, capability_id: str, version: str):
        self.verify(capability_id, version)
        with self._connect() as db: row = db.execute("SELECT payload, artifact_hash, manifest FROM versions WHERE id=? AND version=? AND status='accepted'", (capability_id, version)).fetchone()
        if not row: raise IntegrityError("only accepted versions can be restored")
        old = self.get(capability_id); data = json.loads(row["payload"]); data["status"] = "accepted"
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO capabilities VALUES (?, ?, 'accepted', ?, ?, ?, ?)", (capability_id, version, json.dumps(data), row["artifact_hash"], row["manifest"], now_iso())); self._event(db, capability_id, version, "rollback", f"from {old.version if old else 'none'}"); db.commit()
        return Capability(**data)
    def incomplete_candidates(self): return [p for p in self.candidates.iterdir() if p.is_dir()]
