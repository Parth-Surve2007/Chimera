import shutil, sys
from dataclasses import dataclass
from .contracts import DiscoveryReport, TaskSpec

def discover(names: list[str]) -> DiscoveryReport:
    return DiscoveryReport({name: shutil.which(name) for name in names}, {"python": sys.executable})

@dataclass(frozen=True)
class CapabilityMatch:
    status: str
    capability: object | None = None
    score: float = 0.0
    evidence: dict = None

def match_capabilities(request, capabilities) -> CapabilityMatch:
    """Conservative deterministic matching with explicit contract evidence."""
    task = request if isinstance(request, TaskSpec) else TaskSpec(str(request))
    request_words = {word.casefold() for word in task.outcome.split() if len(word) > 2}
    best, best_score = None, 0.0
    best_evidence = {}
    for capability in capabilities:
        status = getattr(getattr(capability, "status", None), "value", getattr(capability, "status", None))
        if status != "accepted": continue
        text = f"{capability.name} {capability.description}".casefold()
        words = {word for word in text.replace("-", " ").split() if len(word) > 2}
        score = len(request_words & words) / max(1, len(request_words))
        missing = []
        incompatible = []
        metadata_missing = []
        required_inputs = task.inputs or {}
        declared_inputs = getattr(capability, "input_contract", None)
        if required_inputs and not declared_inputs: metadata_missing.append("input_contract")
        elif required_inputs and any(key not in declared_inputs for key in required_inputs): missing.append("inputs:" + ",".join(sorted(set(required_inputs) - set(declared_inputs))))
        if task.constraints:
            declared_constraints = getattr(capability, "resource_profile", {}) or {}
            if not declared_constraints: metadata_missing.append("resource_profile")
            elif any(str(item).casefold() not in str(declared_constraints).casefold() for item in task.constraints): missing.append("constraints")
        evidence = {"covered_requirements": sorted(request_words & words), "uncovered_requirements": sorted(request_words - words), "contract_gaps": missing, "incompatible_requirements": incompatible, "missing_metadata": metadata_missing, "behaviorally_verified": bool((getattr(capability, "verification_evidence", {}) or {}).get("behavioral"))}
        rank = score - (0.5 if incompatible else 0) - (0.15 if metadata_missing else 0)
        if rank > best_score: best, best_score, best_evidence = capability, rank, evidence
    if best is None: return CapabilityMatch("missing", evidence={"reason": "no accepted capability metadata matched", "covered_requirements": [], "uncovered_requirements": sorted(request_words)})
    if best_evidence["incompatible_requirements"]: status = "incompatible"
    elif best_evidence["missing_metadata"] or best_evidence["contract_gaps"] or best_evidence["uncovered_requirements"]: status = "partial"
    elif best_score >= 0.6: status = "direct"
    else: status = "partial"
    return CapabilityMatch(status, best, max(0.0, best_score), best_evidence)

