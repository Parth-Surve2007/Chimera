from dataclasses import dataclass
from pathlib import Path
import uuid, json, shutil
from .audit import AuditStore
from .models import now_iso
from .acquisition import ApprovalGate, ProposalVerifier, stage_proposal
from .contracts import TaskSpec, VerificationStatus
from .discovery import discover, match_capabilities
from .evaluation import evaluate_requirements, report_dict

@dataclass(frozen=True)
class WorkflowResult:
    state: str
    message: str
    candidate: Path | None = None
    verification: object | None = None

class CapabilityWorkflow:
    def __init__(self, provider, workspace: Path, max_calls: int = 2, registry=None): self.provider, self.workspace, self.max_calls, self.registry = provider, Path(workspace), max(1, max_calls), registry; self.gate = ApprovalGate()
    def run(self, request: str) -> WorkflowResult:
        run_id = uuid.uuid4().hex; audit = AuditStore(self.workspace.parent / "chimera.db"); audit.workflow_start(run_id, request)
        def event(stage, outcome, detail="", candidate_id=None, digest=None): audit.workflow_event(run_id, stage, outcome, detail, candidate_id, digest)
        event("task_submitted", "completed", "request received")
        if self.provider.status().state != "ready":
            message = "local model unavailable: " + self.provider.status().detail; event("task_analysis", "blocked", message); audit.workflow_finish(run_id, "blocked", message); return WorkflowResult("blocked", message)
        event("task_analysis", "started", "local structured analysis started")
        plan = self.provider.plan(request)
        if plan.status != "ready":
            message = "task analysis failed: " + (plan.error or plan.status); event("task_analysis", plan.status, message); audit.workflow_finish(run_id, "blocked", message); return WorkflowResult("blocked", message)
        task = plan.task; event("task_analysis", "completed", "structured task validated")
        match = match_capabilities(task, self.registry.list() if self.registry else [])
        event("capability_discovery", match.status, json.dumps({"score": round(match.score, 3), "evidence": match.evidence}, separators=(",", ":")) + (f"; capability={match.capability.capability_id}" if match.capability else ""))
        if match.status == "direct":
            message = f"existing accepted capability matches request: {match.capability.name}; no proposal generated"
            audit.workflow_finish(run_id, "matched", "", match.capability.capability_id); return WorkflowResult("matched", message)
        event("proposal_generation", "started", "proposal generation started")
        try: proposal = self.provider.propose(task); event("proposal_generation", "completed", "proposal contract parsed")
        except Exception as exc:
            message = "capability proposal failed: " + str(exc); event("proposal_generation", "failed", message); audit.workflow_finish(run_id, "rejected", message); return WorkflowResult("rejected", message)
        self.workspace.mkdir(parents=True, exist_ok=True)
        storage_id = uuid.uuid4().hex
        staging = self.workspace / (".staging-" + storage_id)
        candidate = self.workspace / ("candidate-" + storage_id)
        try:
            event("candidate_staging", "started", "isolated staging started"); stage_proposal(proposal, staging)
            (staging / ".chimera-candidate.json").write_text(json.dumps({"candidate_id": proposal.candidate_id, "name": proposal.name, "version": "0.1.0", "task": task.outcome, "permissions": proposal.permissions, "status": "pending_review"}), encoding="utf-8")
            event("candidate_staging", "completed", "candidate metadata written", proposal.candidate_id)
            event("static_verification", "started", "independent static verification started", proposal.candidate_id)
            verification = ProposalVerifier().verify(task, staging)
            if verification.status != VerificationStatus.PASS:
                event("static_verification", "failed", verification.diagnostics, proposal.candidate_id); audit.workflow_finish(run_id, "rejected", verification.diagnostics, proposal.candidate_id); return WorkflowResult("rejected", verification.diagnostics, None, verification)
            event("static_verification", "completed", verification.diagnostics, proposal.candidate_id)
            evaluation = evaluate_requirements(task.acceptance_criteria, candidate_id=proposal.candidate_id, static_pass=True)
            event("evidence_evaluation", evaluation.status, json.dumps(report_dict(evaluation), separators=(",", ":")), proposal.candidate_id)
            staging.replace(candidate)
        except Exception as exc:
            message = "candidate staging failed: " + str(exc); event("candidate_staging", "failed", message, getattr(proposal, "candidate_id", None)); audit.workflow_finish(run_id, "rejected", message, getattr(proposal, "candidate_id", None)); return WorkflowResult("rejected", message)
        finally:
            if staging.exists(): shutil.rmtree(staging, ignore_errors=True)
        audit.workflow_finish(run_id, "pending_review", "", proposal.candidate_id); event("pending_review", "completed", "candidate persisted for human review", proposal.candidate_id); return WorkflowResult("pending_review", "verification passed; explicit execution and acceptance approval required", candidate, verification)
