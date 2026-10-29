from dataclasses import dataclass
from pathlib import Path
import uuid, json, shutil, threading, time
from .audit import AuditStore
from .models import now_iso
from .acquisition import ApprovalGate, ProposalVerifier, stage_proposal
from .contracts import TaskSpec, VerificationStatus
from .discovery import discover, match_capabilities
from .evaluation import evaluate_requirements, report_dict
from .registry import Registry, IntegrityError
from .local_model import assess_readiness

@dataclass(frozen=True)
class WorkflowResult:
    state: str
    message: str
    candidate: Path | None = None
    verification: object | None = None
    evaluation: object | None = None
    run_id: str | None = None
    readiness: object | None = None
    clarification_session_id: str | None = None

class CapabilityWorkflow:
    def __init__(self, provider, workspace: Path, max_calls: int = 2, registry=None): self.provider, self.workspace, self.max_calls, self.registry = provider, Path(workspace), max(1, max_calls), registry; self.gate = ApprovalGate()
    def run(self, request: str, clarifications: dict[str, str] | None = None, _run_id: str | None = None, _session_id: str | None = None, _owner_token: str | None = None, _fencing_token: int | None = None, _revision: int | None = None) -> WorkflowResult:
        run_id = _run_id or uuid.uuid4().hex; audit = AuditStore(self.workspace.parent / "chimera.db"); original_request = request; clarifications = clarifications or {}
        if clarifications:
            event_request = json.dumps({"original_request": original_request, "clarifications": clarifications}, separators=(",", ":"))
            request = original_request + "\nClarifications supplied by the user:\n" + "\n".join(f"{key}: {value}" for key, value in clarifications.items())
        else: event_request = original_request
        audit.workflow_start(run_id, event_request)
        def event(stage, outcome, detail="", candidate_id=None, digest=None): audit.workflow_event(run_id, stage, outcome, detail, candidate_id, digest)
        def own():
            if _session_id:
                if ownership_lost.is_set(): raise ValueError("continuation ownership was lost")
                audit.assert_ownership(_session_id, run_id, _owner_token, _fencing_token, _revision)
        heartbeat_stop = threading.Event(); ownership_lost = threading.Event()
        heartbeat = None
        if _session_id:
            def beat():
                while not heartbeat_stop.wait(30):
                    try: audit.clarification_renew_fenced(_session_id, run_id, _owner_token, _fencing_token, _revision, 120)
                    except Exception: ownership_lost.set(); return
            heartbeat = threading.Thread(target=beat, daemon=True); heartbeat.start()
        def stop_heartbeat():
            heartbeat_stop.set()
            if heartbeat and heartbeat is not threading.current_thread(): heartbeat.join(timeout=1)
        def failure_result(status, message, candidate_id=None):
            """Record failure best-effort without replacing the primary workflow error."""
            try: event("workflow_failure", "failed", message, candidate_id)
            except Exception: pass
            try: audit.workflow_finish(run_id, status, message, candidate_id)
            except Exception: pass
            return WorkflowResult("rejected" if status == "rejected" else "blocked", message, run_id=run_id)
        event("task_submitted", "completed", "request received")
        if self.provider.status().state != "ready":
            message = "local model unavailable: " + self.provider.status().detail; event("task_analysis", "blocked", message); audit.workflow_finish(run_id, "blocked", message); stop_heartbeat(); return WorkflowResult("blocked", message)
        event("task_analysis", "started", "local structured analysis started")
        try:
            plan = self.provider.plan(request); own()
        except Exception:
            stop_heartbeat(); raise
        if plan.status != "ready":
            message = "task analysis failed: " + (plan.error or plan.status); event("task_analysis", plan.status, message); audit.workflow_finish(run_id, "blocked", message); stop_heartbeat(); return WorkflowResult("blocked", message)
        task = plan.task; event("task_analysis", "completed", "structured task validated")
        if clarifications: event("clarification_supplied", "completed", json.dumps({"clarifications": clarifications}, separators=(",", ":")))
        readiness = assess_readiness(task)
        event("readiness_assessment", readiness.status, json.dumps({"reason": readiness.reason, "questions": [q.__dict__ for q in readiness.questions]}, separators=(",", ":")))
        if readiness.status == "needs_clarification":
            event("clarification_requested", "completed", json.dumps({"questions": [q.__dict__ for q in readiness.questions]}, separators=(",", ":")))
            session_id = uuid.uuid4().hex
            audit.clarification_create(session_id, run_id, original_request, [q.__dict__ for q in readiness.questions])
            audit.workflow_finish(run_id, "needs_clarification", readiness.reason)
            stop_heartbeat(); return WorkflowResult("needs_clarification", readiness.reason, run_id=run_id, readiness=readiness, clarification_session_id=session_id)
        if readiness.status == "blocked":
            audit.workflow_finish(run_id, "blocked", readiness.reason); stop_heartbeat(); return WorkflowResult("blocked", readiness.reason, run_id=run_id, readiness=readiness)
        match = match_capabilities(task, self.registry.list() if self.registry else [])
        event("capability_discovery", match.status, json.dumps({"score": round(match.score, 3), "evidence": match.evidence}, separators=(",", ":")) + (f"; capability={match.capability.capability_id}" if match.capability else ""))
        if match.status == "direct":
            message = f"existing accepted capability matches request: {match.capability.name}; no proposal generated"
            audit.workflow_finish(run_id, "matched", "", match.capability.capability_id); stop_heartbeat(); return WorkflowResult("matched", message)
        event("proposal_generation", "started", "proposal generation started")
        try: proposal = self.provider.propose(task); event("proposal_generation", "completed", "proposal contract parsed")
        except Exception as exc:
            message = "capability proposal failed: " + str(exc)
            try: event("proposal_generation", "failed", message)
            except Exception: pass
            stop_heartbeat(); return failure_result("rejected", message)
        self.workspace.mkdir(parents=True, exist_ok=True)
        storage_id = uuid.uuid4().hex
        staging = self.workspace / (".staging-" + storage_id)
        candidate = self.workspace / ("candidate-" + storage_id)
        try:
            own(); event("candidate_staging", "started", "isolated staging started"); stage_proposal(proposal, staging)
            trace = []
            for index, requirement in enumerate(task.acceptance_criteria, 1):
                rid = f"requirement-{index}"; cid = f"criterion-{index}"
                matches = [item for item in proposal.requirement_coverage if item.get("requirement_id") == rid]
                mapping = matches[0] if len(matches) == 1 and matches[0].get("criterion_id") == cid else None
                trace.append({"requirement_id": rid, "requirement": requirement, "criterion_id": cid, "coverage": mapping.get("coverage", "unavailable") if mapping else "unavailable", "proposal_intent": mapping.get("proposal_intent", "") if mapping else "", "unresolved": mapping.get("unresolved", "no explicit proposal-to-criterion mapping") if mapping else "no explicit proposal-to-criterion mapping"})
            own(); (staging / ".chimera-candidate.json").write_text(json.dumps({"candidate_id": proposal.candidate_id, "name": proposal.name, "version": "0.1.0", "task": task.outcome, "permissions": proposal.permissions, "status": "pending_review", "requirement_trace": trace}), encoding="utf-8")
            event("candidate_staging", "completed", "candidate metadata written", proposal.candidate_id)
            event("static_verification", "started", "independent static verification started", proposal.candidate_id)
            verification = ProposalVerifier().verify(task, staging)
            if verification.status != VerificationStatus.PASS:
                event("static_verification", "failed", verification.diagnostics, proposal.candidate_id); audit.workflow_finish(run_id, "rejected", verification.diagnostics, proposal.candidate_id); stop_heartbeat(); return WorkflowResult("rejected", verification.diagnostics, None, verification)
            event("static_verification", "completed", verification.diagnostics, proposal.candidate_id)
            metadata_path = staging / ".chimera-candidate.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("candidate_id") != proposal.candidate_id or not isinstance(metadata.get("version"), str) or not metadata["version"]:
                raise IntegrityError("candidate metadata identity or version is invalid")
            manifest, digest = Registry.canonical_manifest(staging)
            if not manifest or not isinstance(digest, str) or len(digest) != 64:
                raise IntegrityError("candidate manifest is missing or invalid")
            metadata["workflow_run_id"] = run_id
            metadata["manifest"] = manifest
            metadata["manifest_digest"] = digest
            with audit.promotion_lock():
                own(); metadata_path.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
                event("manifest_anchor", "completed", json.dumps({"candidate_id": proposal.candidate_id, "version": metadata["version"], "manifest": manifest}, sort_keys=True), proposal.candidate_id, digest)
            anchored = json.loads(metadata_path.read_text(encoding="utf-8"))
            actual_manifest, actual_digest = Registry.canonical_manifest(staging)
            if (anchored.get("candidate_id"), anchored.get("manifest"), anchored.get("manifest_digest")) != (proposal.candidate_id, actual_manifest, actual_digest):
                raise IntegrityError("candidate manifest anchor does not match staged artifact")
            digest = anchored["manifest_digest"]
            own(); evaluation = evaluate_requirements(task.acceptance_criteria, candidate_id=metadata["candidate_id"], manifest_digest=digest, capability_version=metadata["version"], static_pass=True)
            event("evidence_evaluation", evaluation.status, json.dumps(report_dict(evaluation), separators=(",", ":")), proposal.candidate_id, digest)
            with audit.promotion_lock():
                own()
                promotion_id = uuid.uuid4().hex
                audit.promotion_intent(promotion_id, storage_id, proposal.candidate_id, metadata["version"], _session_id, run_id, _owner_token or "", _fencing_token if _fencing_token is not None else 0, _revision if _revision is not None else 0, staging, candidate, actual_manifest, digest)
                event("staging_intent", "completed", json.dumps({"promotion_id": promotion_id, "attempt_id": storage_id, "candidate_id": proposal.candidate_id, "run_id": run_id, "fencing_token": _fencing_token, "revision": _revision, "manifest_digest": digest, "target": str(candidate)}, sort_keys=True), proposal.candidate_id, digest)
                own(); staging.replace(candidate)
                if not audit.verify_promotion_destination(promotion_id): raise IntegrityError("promoted destination does not match durable intent")
                audit.promotion_transition(promotion_id, "intent_recorded", "promotion_observed", attempt_id=storage_id, session_id=_session_id, run_id=run_id, owner_token=_owner_token or "", fencing_token=_fencing_token if _fencing_token is not None else 0, revision=_revision if _revision is not None else 0, candidate_id=proposal.candidate_id, version=metadata["version"], digest=digest)
        except Exception as exc:
            message = "candidate staging failed: " + str(exc); candidate_id = getattr(proposal, "candidate_id", None)
            try: event("candidate_staging", "failed", message, candidate_id)
            except Exception: pass
            stop_heartbeat(); return failure_result("rejected", message, candidate_id)
        finally:
            if staging.exists(): shutil.rmtree(staging, ignore_errors=True)
        try:
            if _session_id:
                audit.finalize_workflow(run_id, "running", "pending_review", "pending_review", "candidate persisted for human review", proposal.candidate_id, digest, _session_id, _owner_token, _fencing_token, _revision)
            else:
                audit.finalize_workflow(run_id, "running", "pending_review", "pending_review", "candidate persisted for human review", proposal.candidate_id, digest)
        finally:
            stop_heartbeat()
        return WorkflowResult("pending_review", "verification passed; explicit execution and acceptance approval required", candidate, verification, evaluation, run_id, readiness)

    def resume_clarification(self, session_id: str, answers: dict[str, str]) -> WorkflowResult:
        session = AuditStore(self.workspace.parent / "chimera.db").clarification_get(session_id)
        if not session: raise KeyError("clarification session not found")
        if not isinstance(answers, dict): raise ValueError("clarification answers must be an object")
        expected = {q.get("question_id") for q in session["questions"] if isinstance(q, dict)}
        if set(answers) != expected or any(not isinstance(value, str) or not value.strip() for value in answers.values()): raise ValueError("answers must match the pending question IDs")
        audit = AuditStore(self.workspace.parent / "chimera.db")
        if session["state"] == "pending":
            continuation = uuid.uuid4().hex; ownership = audit.clarification_claim(session_id, continuation, answers); owner_token = ownership["owner_token"]; fencing_token = ownership["fencing_token"]; revision = ownership["revision"]
        elif session["state"] == "resuming":
            continuation = session["current_run_id"]
            status = audit.workflow_run_status(continuation)
            owner_token = session.get("owner_token")
            fencing_token = session.get("fencing_token")
            revision = session.get("revision")
            if status is not None: raise ValueError("clarification continuation requires recovery reconciliation")
        elif session["state"] == "recovery_required" and session.get("owner_token") and session.get("lease_expires_at", 0) > time.time():
            continuation = session["current_run_id"]; owner_token = session["owner_token"]; fencing_token = session["fencing_token"]; revision = session["revision"]
        elif session["state"] == "recovery_required":
            raise ValueError("clarification continuation requires an explicit safe recovery claim")
        else: raise ValueError("clarification session is not recoverable")
        try:
            result = self.run(session["original_request"], answers, _run_id=continuation, _session_id=session_id, _owner_token=owner_token, _fencing_token=fencing_token, _revision=revision)
            if result.state not in ("pending_review",): audit.clarification_finish(session_id, "blocked" if result.state == "blocked" else "failed")
            return result
        except Exception:
            raise

    def reconcile_clarification(self, session_id: str):
        """Inspect an interrupted continuation without retrying or changing candidates."""
        audit = AuditStore(self.workspace.parent / "chimera.db")
        session = audit.clarification_get(session_id)
        if not session: raise KeyError("clarification session not found")
        run_id = session["current_run_id"]
        status = audit.workflow_run_status(run_id)
        events = audit.workflow_events_for(run_id)
        evidence = "active or indeterminate" if status == "running" else "terminal" if status in {"pending_review", "matched", "blocked", "rejected"} else "missing"
        return {"session": session, "run_id": run_id, "run_status": status, "evidence_state": evidence, "events": events, "read_only": True}
