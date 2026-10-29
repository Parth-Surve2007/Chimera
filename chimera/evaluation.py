"""Conservative, evidence-bound proposal evaluation; never executes candidates."""
from dataclasses import dataclass, asdict
import json
from pathlib import Path
from typing import Iterable, Protocol

EVALUATION_STATES = frozenset(("pass", "fail", "unknown", "blocked"))

@dataclass(frozen=True)
class BehavioralEvidence:
    run_id: str; candidate_id: str; manifest_digest: str; criterion_id: str; status: str; evidence_type: str; origin: str
    capability_version: str | None = None; authoritative: bool = False

class EvidenceRepository(Protocol):
    def for_candidate(self, candidate_id: str, manifest_digest: str) -> Iterable[BehavioralEvidence]: ...

@dataclass(frozen=True)
class CriterionResult:
    criterion_id: str; requirement: str; expected: str; result: str
    evidence_refs: tuple[str, ...] = (); reason: str = ""; remaining_evidence: tuple[str, ...] = ()

@dataclass(frozen=True)
class EvaluationReport:
    status: str; criteria: tuple[CriterionResult, ...]; candidate_id: str | None = None; manifest_digest: str | None = None; capability_version: str | None = None

def evaluate_requirements(requirements, *, candidate_id=None, manifest_digest=None, capability_version=None, static_pass=False) -> EvaluationReport:
    """Production entry point; no caller-supplied behavioral evidence is accepted."""
    return _evaluate_requirements(requirements, candidate_id=candidate_id, manifest_digest=manifest_digest, capability_version=capability_version, static_pass=static_pass, evidence_repository=None)

def _evaluate_requirements(requirements, *, candidate_id=None, manifest_digest=None, capability_version=None, static_pass=False, evidence_repository: EvidenceRepository | None = None) -> EvaluationReport:
    """Internal dependency seam for deterministic tests and future trusted wiring."""
    if not isinstance(requirements,(list,tuple)) or not requirements:
        return EvaluationReport("unknown",(CriterionResult("requirements","explicit acceptance criteria","at least one criterion","unknown",reason="no criteria were supplied",remaining_evidence=("structured acceptance criteria",)),),candidate_id,manifest_digest,capability_version)
    results=[]; seen=set()
    for index, requirement in enumerate(requirements,1):
        cid=f"criterion-{index}"; text=requirement.strip() if isinstance(requirement,str) else ""
        if not text:
            results.append(CriterionResult(cid,"empty requirement","non-empty requirement","unknown",reason="criterion is malformed",remaining_evidence=("clarified requirement",))); continue
        if text in seen:
            results.append(CriterionResult(cid,text,"unique criterion","unknown",reason="duplicate criterion is ambiguous",remaining_evidence=("deduplicated criteria",))); continue
        seen.add(text); records=[]
        if evidence_repository is not None and candidate_id and manifest_digest:
            try: records=list(evidence_repository.for_candidate(candidate_id,manifest_digest))
            except Exception: records=[]
        valid=[]
        for record in records:
            if not isinstance(record,BehavioralEvidence) or not record.authoritative: continue
            if record.candidate_id != candidate_id or record.manifest_digest != manifest_digest or (capability_version is not None and record.capability_version != capability_version): continue
            if record.criterion_id != cid or record.status not in ("pass","fail") or not record.evidence_type or not record.origin: continue
            valid.append(record)
        statuses={r.status for r in valid}; refs=tuple(f"behavior:{r.run_id}" for r in valid)
        if len(valid)>1 and len(statuses)>1:
            results.append(CriterionResult(cid,text,"one consistent authoritative result","unknown",refs,"conflicting applicable evidence",("single authoritative verification result",))); continue
        if len(valid)>1:
            results.append(CriterionResult(cid,text,"one authoritative result","unknown",refs,"duplicate evidence records",("deduplicated verification result",))); continue
        if valid:
            results.append(CriterionResult(cid,text,"authoritative applicable behavioral evidence",valid[0].status,refs,"authoritative evidence matches candidate, manifest, criterion, and version")); continue
        outcome="unknown" if static_pass else "blocked" if evidence_repository is None else "unknown"
        reason="behavioral verification prerequisite is unavailable" if evidence_repository is None else "applicable authoritative evidence is unavailable or rejected"
        results.append(CriterionResult(cid,text,"authoritative applicable behavioral evidence",outcome,reason=reason,remaining_evidence=("verified behavioral evidence",)))
    status="pass" if results and all(x.result=="pass" for x in results) else "fail" if any(x.result=="fail" for x in results) else "blocked" if any(x.result=="blocked" for x in results) else "unknown"
    return EvaluationReport(status,tuple(results),candidate_id,manifest_digest,capability_version)

def report_dict(report: EvaluationReport) -> dict:
    return {"status":report.status,"candidate_id":report.candidate_id,"manifest_digest":report.manifest_digest,"capability_version":report.capability_version,"criteria":[asdict(item) for item in report.criteria]}
