"""Deterministic, evidence-bound proposal evaluation; never executes candidates."""
from dataclasses import dataclass, asdict
from typing import Any

@dataclass(frozen=True)
class CriterionResult:
    criterion_id: str
    requirement: str
    expected: str
    result: str
    evidence_refs: tuple[str, ...] = ()
    reason: str = ""
    remaining_evidence: tuple[str, ...] = ()

@dataclass(frozen=True)
class EvaluationReport:
    status: str
    criteria: tuple[CriterionResult, ...]
    candidate_id: str | None = None
    manifest_digest: str | None = None

def evaluate_requirements(requirements: list[str] | tuple[str, ...], *, candidate_id: str | None = None, manifest_digest: str | None = None, static_pass: bool = False, behavioral: dict[str, Any] | None = None) -> EvaluationReport:
    if not requirements:
        return EvaluationReport("unknown", (CriterionResult("requirements", "explicit acceptance criteria", "at least one criterion", "unknown", reason="no criteria were supplied", remaining_evidence=("structured acceptance criteria",)),), candidate_id, manifest_digest)
    behavioral = behavioral or {}
    results = []
    for index, requirement in enumerate(requirements, 1):
        text = str(requirement).strip()
        cid = f"criterion-{index}"
        if not text:
            results.append(CriterionResult(cid, "empty requirement", "non-empty requirement", "unknown", reason="criterion is empty", remaining_evidence=("clarified requirement",))); continue
        if behavioral.get("status") == "pass" and behavioral.get("manifest_digest") == manifest_digest:
            results.append(CriterionResult(cid, text, "independent behavioral evidence", "pass", (f"behavior:{candidate_id}:{manifest_digest}",), "behavioral evidence matches this candidate digest")); continue
        if behavioral.get("status") == "fail" and behavioral.get("manifest_digest") == manifest_digest:
            results.append(CriterionResult(cid, text, "independent behavioral evidence", "fail", (f"behavior:{candidate_id}:{manifest_digest}",), "behavioral evidence demonstrates failure")); continue
        if static_pass:
            results.append(CriterionResult(cid, text, "static contract and file checks", "unknown", (f"static:{candidate_id}:{manifest_digest}",), "static verification passed but does not establish runtime behavior", ("independent behavioral verification",))); continue
        results.append(CriterionResult(cid, text, "validated proposal evidence", "unknown", reason="required evidence is unavailable", remaining_evidence=("successful applicable verification",)))
    status = "pass" if results and all(item.result == "pass" for item in results) else "fail" if any(item.result == "fail" for item in results) else "unknown"
    return EvaluationReport(status, tuple(results), candidate_id, manifest_digest)

def report_dict(report: EvaluationReport) -> dict:
    return {"status": report.status, "candidate_id": report.candidate_id, "manifest_digest": report.manifest_digest, "criteria": [asdict(item) for item in report.criteria]}
