"""Persistent human review service; acceptance never executes candidate code."""
import hashlib, json
from dataclasses import dataclass
from pathlib import Path
from .acquisition import ProposalVerifier
from .audit import AuditStore
from .contracts import TaskSpec, VerificationStatus
from .models import Capability, Lifecycle
from .registry import Registry, IntegrityError
from .behavior import verify_behavior

@dataclass
class CandidateRecord:
    candidate_id: str; path: Path; name: str; version: str; task: str; permissions: list[str]; status: str; digest: str | None = None; execution_approved: bool = False; acceptance_approved: bool = False; execution_digest: str | None = None

class ReviewService:
    def __init__(self, data_dir: Path, audit: AuditStore | None = None):
        self.data_dir = Path(data_dir); self.root = self.data_dir / "candidates"; self.root.mkdir(parents=True, exist_ok=True); self.registry = Registry(self.data_dir); self.audit = audit or AuditStore(self.data_dir / "chimera.db")
    def _record(self, path: Path) -> CandidateRecord:
        data = json.loads((path / ".chimera-candidate.json").read_text(encoding="utf-8")); return CandidateRecord(data["candidate_id"], path, data["name"], data["version"], data["task"], data.get("permissions", []), data["status"], data.get("digest"), data.get("execution_approved", False), data.get("acceptance_approved", False), data.get("execution_digest"))
    def list(self): return [self._record(p) for p in self.root.iterdir() if p.is_dir() and (p / ".chimera-candidate.json").is_file()]
    def get(self, candidate_id: str):
        for item in self.list():
            if item.candidate_id == candidate_id: return item
        raise KeyError("candidate not found")
    def _save(self, item: CandidateRecord):
        payload = {"candidate_id":item.candidate_id,"name":item.name,"version":item.version,"task":item.task,"permissions":item.permissions,"status":item.status,"digest":item.digest,"execution_approved":item.execution_approved,"acceptance_approved":item.acceptance_approved,"execution_digest":item.execution_digest}; (item.path / ".chimera-candidate.json").write_text(json.dumps(payload), encoding="utf-8")
    def verify(self, candidate_id: str):
        item = self.get(candidate_id); result = ProposalVerifier().verify(TaskSpec(item.task), item.path); manifest, digest = self.registry._manifest(item.path)
        if item.digest and item.digest != digest: result = result.__class__(VerificationStatus.FAIL, diagnostics="candidate changed since review")
        item.digest = digest; self._save(item); self.audit.event(candidate_id, "verification", result.status.value, result.diagnostics); return result
    def approve_execution(self, candidate_id: str):
        item = self.get(candidate_id); _, item.digest = self.registry._manifest(item.path); item.execution_approved = True; item.execution_digest = item.digest; self._save(item); self.audit.event(candidate_id, "execution_approval", "granted", f"manifest={item.digest}; approval does not execute artifact"); return item
    def behavior_verify(self, candidate_id: str):
        item = self.get(candidate_id); _, digest = self.registry._manifest(item.path)
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
        item = self.get(candidate_id); item.acceptance_approved = True; self._save(item); self.audit.event(candidate_id, "acceptance_approval", "granted"); return item

if __name__ == "__main__":
    from .review_cli import main
    raise SystemExit(main())
