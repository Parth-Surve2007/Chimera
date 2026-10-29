"""Persistent human review service; acceptance never executes candidate code."""
import hashlib, json, re
from dataclasses import dataclass
from pathlib import Path
from .acquisition import ProposalVerifier
from .audit import AuditStore, ManifestAnchorError
from .contracts import TaskSpec, VerificationStatus
from .models import Capability, Lifecycle
from .registry import Registry, IntegrityError
from .behavior import verify_behavior

@dataclass
class CandidateRecord:
    candidate_id: str; path: Path; name: str; version: str; task: str; permissions: list[str]; status: str; digest: str | None = None; execution_approved: bool = False; acceptance_approved: bool = False; execution_digest: str | None = None; manifest: list | None = None; manifest_digest: str | None = None; workflow_run_id: str | None = None; requirement_trace: list | None = None

class ReviewService:
    def __init__(self, data_dir: Path, audit: AuditStore | None = None):
        self.data_dir = Path(data_dir); self.root = self.data_dir / "candidates"; self.root.mkdir(parents=True, exist_ok=True); self.registry = Registry(self.data_dir); self.audit = audit or AuditStore(self.data_dir / "chimera.db")
    def _record(self, path: Path) -> CandidateRecord:
        data = json.loads((path / ".chimera-candidate.json").read_text(encoding="utf-8")); return CandidateRecord(data["candidate_id"], path, data["name"], data["version"], data["task"], data.get("permissions", []), data["status"], data.get("digest"), data.get("execution_approved", False), data.get("acceptance_approved", False), data.get("execution_digest"), data.get("manifest"), data.get("manifest_digest"), data.get("workflow_run_id"), data.get("requirement_trace"))
    def list(self): return [self._record(p) for p in self.root.iterdir() if p.is_dir() and (p / ".chimera-candidate.json").is_file()]
    def get(self, candidate_id: str):
        for item in self.list():
            if item.candidate_id == candidate_id: return item
        raise KeyError("candidate not found")
    def _save(self, item: CandidateRecord):
        path = item.path / ".chimera-candidate.json"; payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update({"candidate_id":item.candidate_id,"name":item.name,"version":item.version,"task":item.task,"permissions":item.permissions,"status":item.status,"digest":item.digest,"execution_approved":item.execution_approved,"acceptance_approved":item.acceptance_approved,"execution_digest":item.execution_digest})
        if item.manifest is not None: payload["manifest"] = item.manifest
        if item.manifest_digest is not None: payload["manifest_digest"] = item.manifest_digest
        if item.workflow_run_id is not None: payload["workflow_run_id"] = item.workflow_run_id
        if item.requirement_trace is not None: payload["requirement_trace"] = item.requirement_trace
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    def _validate_anchor(self, item: CandidateRecord):
        if not item.manifest or not item.manifest_digest or not item.workflow_run_id: raise IntegrityError("candidate has no durable manifest anchor")
        anchor = self.audit.manifest_anchor(item.workflow_run_id)
        if not anchor or anchor.get("candidate_id") != item.candidate_id or anchor.get("manifest_digest") != item.manifest_digest or anchor.get("version") != item.version or anchor.get("manifest") != item.manifest: raise IntegrityError("durable manifest anchor mismatch")
        if not re.fullmatch(r"[0-9a-f]{64}", item.manifest_digest) or item.manifest != sorted(item.manifest, key=lambda entry: entry.get("path", "")): raise IntegrityError("manifest anchor ordering or digest format is invalid")
        paths = []
        for entry in item.manifest:
            if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"} or not isinstance(entry["path"], str) or type(entry["size"]) is not int or entry["size"] < 0 or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]): raise IntegrityError("manifest entry schema is invalid")
            if not entry["path"] or entry["path"].startswith("/") or ".." in Path(entry["path"]).parts or entry["path"] in paths: raise IntegrityError("manifest path is invalid or duplicated")
            paths.append(entry["path"])
        encoded = json.dumps(item.manifest, sort_keys=True, separators=(",", ":")).encode()
        if hashlib.sha256(encoded).hexdigest() != item.manifest_digest: raise IntegrityError("manifest digest does not match anchored entries")
        actual, digest = self.registry._manifest(item.path)
        if actual != item.manifest or digest != item.manifest_digest: raise IntegrityError("candidate changed since manifest anchoring")
        return digest
    def verify(self, candidate_id: str):
        item = self.get(candidate_id); digest = self._validate_anchor(item); result = ProposalVerifier().verify(TaskSpec(item.task), item.path); manifest = item.manifest
        if item.digest and item.digest != digest: result = result.__class__(VerificationStatus.FAIL, diagnostics="candidate changed since review")
        item.digest = digest; self._save(item); self.audit.event(candidate_id, "verification", result.status.value, result.diagnostics); return result
    def approve_execution(self, candidate_id: str):
        item = self.get(candidate_id); item.digest = self._validate_anchor(item); item.execution_approved = True; item.execution_digest = item.digest; self._save(item); self.audit.event(candidate_id, "execution_approval", "granted", f"manifest={item.digest}; approval does not execute artifact"); return item
    def behavior_verify(self, candidate_id: str):
        item = self.get(candidate_id); digest = self._validate_anchor(item)
        result = verify_behavior(item.path, digest, item.execution_approved, item.execution_digest)
        self.audit.event(candidate_id, "behavior_verification", result.status, result.diagnostics)
        return result
    def reject(self, candidate_id: str, reason: str):
        item = self.get(candidate_id); item.status = "rejected"; self._save(item); self.audit.event(candidate_id, "rejection", "completed", reason); return item
    def accept(self, candidate_id: str):
        item = self.get(candidate_id)
        if not item.acceptance_approved: raise PermissionError("explicit acceptance approval required")
        result = self.verify(candidate_id)
        if result.status != VerificationStatus.PASS: raise IntegrityError("candidate is not currently verified")
        cap = Capability(item.candidate_id, item.name, "generic", item.task, version=item.version, status=Lifecycle.VALIDATING, permissions=item.permissions, verification_evidence={"status":"static_pass"})
        digest = self.registry.accept(cap, item.path); item.status = "accepted"; self._save(item); self.audit.event(candidate_id, "acceptance", "completed", f"version={item.version}; digest={digest}"); return cap
    def approve_acceptance(self, candidate_id: str):
        item = self.get(candidate_id); self._validate_anchor(item); item.acceptance_approved = True; self._save(item); self.audit.event(candidate_id, "acceptance_approval", "granted"); return item

if __name__ == "__main__":
    from .review_cli import main
    raise SystemExit(main())
