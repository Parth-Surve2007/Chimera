"""Bounded, observable generic acquisition lifecycle."""
from pathlib import Path
import shutil, uuid
from .contracts import AcquisitionResult, CandidateProvider, TaskSpec, VerificationResult, VerificationStatus, Verifier, CapabilityProposal

class FakeProvider:
    """Deterministic fixture provider; it is not a model and performs no inference."""
    def __init__(self, content: str = "verified fixture\n"): self.content = content
    def construct(self, task: TaskSpec, workspace: Path) -> Path:
        workspace.mkdir(parents=True, exist_ok=False); result = workspace / "artifact.txt"; result.write_text(self.content, encoding="utf-8"); return result

class FileVerifier:
    def verify(self, task: TaskSpec, candidate: Path) -> VerificationResult:
        if not candidate.is_file(): return VerificationResult(VerificationStatus.FAIL, diagnostics="candidate deliverable is missing")
        if candidate.stat().st_size == 0: return VerificationResult(VerificationStatus.INCONCLUSIVE, diagnostics="candidate is empty")
        return VerificationResult(VerificationStatus.PASS, {"size": candidate.stat().st_size})

class AcquisitionEngine:
    def __init__(self, workspace: Path, max_attempts: int = 2): self.workspace, self.max_attempts = Path(workspace), max(1, max_attempts)
    def acquire(self, task: TaskSpec, provider: CandidateProvider, verifier: Verifier) -> AcquisitionResult:
        last = VerificationResult(VerificationStatus.INCONCLUSIVE, diagnostics="not attempted")
        for attempt in range(1, self.max_attempts + 1):
            candidate_dir = self.workspace / ("candidate-" + uuid.uuid4().hex)
            try:
                artifact = provider.construct(task, candidate_dir); last = verifier.verify(task, artifact)
                if last.status == VerificationStatus.PASS: return AcquisitionResult("accepted", attempt, artifact, last)
            except Exception as exc: last = VerificationResult(VerificationStatus.FAIL, diagnostics=str(exc))
        return AcquisitionResult("rejected" if last.status == VerificationStatus.FAIL else "unverified", self.max_attempts, None, last, "acceptance criteria not established")

class ProposalVerifier:
    """Independent static checks; it never executes candidate code."""
    def verify(self, task: TaskSpec, candidate: Path) -> VerificationResult:
        if not candidate.is_dir(): return VerificationResult(VerificationStatus.FAIL, diagnostics="candidate package is missing")
        files = list(candidate.rglob("*")); unsafe = [p for p in files if p.is_symlink() or (p.is_file() and (".." in p.relative_to(candidate).parts))]
        if unsafe: return VerificationResult(VerificationStatus.FAIL, diagnostics="unsafe candidate path or symlink")
        if not any(p.is_file() for p in files): return VerificationResult(VerificationStatus.INCONCLUSIVE, diagnostics="candidate contains no files")
        prohibited = ("subprocess", "os.system", "socket", "requests.")
        for p in files:
            if p.is_file() and any(token in p.read_text(errors="ignore") for token in prohibited): return VerificationResult(VerificationStatus.FAIL, diagnostics=f"prohibited operation in {p.name}")
        return VerificationResult(VerificationStatus.PASS, {"static_files": sum(p.is_file() for p in files), "behavioral": "not_run"}, "static verification only; behavioral verification remains unresolved")

def stage_proposal(proposal: CapabilityProposal, root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=False); total = 0
    for item in proposal.files:
        path = Path(item.path)
        if path.is_absolute() or ".." in path.parts: raise ValueError("proposal path escapes candidate workspace")
        total += len(item.content.encode());
        if total > 500_000: raise ValueError("candidate package exceeds size limit")
        target = root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(item.content, encoding="utf-8", newline="")
    return root

class ApprovalGate:
    def __init__(self): self.execution_approved = False; self.acceptance_approved = False
    def approve_execution(self): self.execution_approved = True
    def approve_acceptance(self): self.acceptance_approved = True
    def can_execute(self): return self.execution_approved
    def can_accept(self): return self.acceptance_approved

