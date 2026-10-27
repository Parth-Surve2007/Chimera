from dataclasses import dataclass
from pathlib import Path
import uuid, json
from .acquisition import ApprovalGate, ProposalVerifier, stage_proposal
from .contracts import TaskSpec, VerificationStatus
from .discovery import discover

@dataclass(frozen=True)
class WorkflowResult:
    state: str
    message: str
    candidate: Path | None = None
    verification: object | None = None

class CapabilityWorkflow:
    def __init__(self, provider, workspace: Path, max_calls: int = 2): self.provider, self.workspace, self.max_calls = provider, Path(workspace), max(1, max_calls); self.gate = ApprovalGate()
    def run(self, request: str) -> WorkflowResult:
        if self.provider.status().state != "ready": return WorkflowResult("blocked", "local model unavailable: " + self.provider.status().detail)
        plan = self.provider.plan(request)
        if plan.status != "ready": return WorkflowResult("blocked", "task analysis failed: " + (plan.error or plan.status))
        task = plan.task; tools = discover([])
        try: proposal = self.provider.propose(task)
        except Exception as exc: return WorkflowResult("rejected", "capability proposal failed: " + str(exc))
        candidate = self.workspace / ("candidate-" + uuid.uuid4().hex)
        try:
            stage_proposal(proposal, candidate)
            (candidate / ".chimera-candidate.json").write_text(json.dumps({"candidate_id": proposal.candidate_id, "name": proposal.name, "version": "0.1.0", "task": task.outcome, "permissions": proposal.permissions, "status": "pending_review"}), encoding="utf-8")
        except Exception as exc: return WorkflowResult("rejected", "candidate staging failed: " + str(exc))
        verification = ProposalVerifier().verify(task, candidate)
        if verification.status != VerificationStatus.PASS: return WorkflowResult("rejected", verification.diagnostics, candidate, verification)
        return WorkflowResult("pending_review", "verification passed; explicit execution and acceptance approval required", candidate, verification)
