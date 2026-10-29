from chimera.evaluation import BehavioralEvidence, _evaluate_requirements, evaluate_requirements

class Repo:
    def __init__(self, records): self.records = records
    def for_candidate(self, candidate_id, manifest_digest): return self.records

def evidence(status="pass", candidate="c1", digest="d1", criterion="criterion-1", version=None, run="r1", authoritative=True):
    return BehavioralEvidence(run, candidate, digest, criterion, status, "isolated-test", "fixture", version, authoritative)

def test_static_only_is_unknown_and_arbitrary_dict_cannot_pass():
    report = evaluate_requirements(["counts words"], candidate_id="c1", manifest_digest="d1", static_pass=True)
    assert report.status == "unknown"

def test_authoritative_evidence_is_bound_to_candidate_digest_and_criterion():
    report = _evaluate_requirements(["counts words", "writes json"], candidate_id="c1", manifest_digest="d1", evidence_repository=Repo([evidence()]))
    assert report.status == "unknown" and report.criteria[0].result == "pass" and report.criteria[1].result == "unknown"

def test_wrong_identity_stale_or_untrusted_evidence_cannot_pass():
    for record in (evidence(candidate="other"), evidence(digest="old"), evidence(authoritative=False)):
        report = _evaluate_requirements(["counts words"], candidate_id="c1", manifest_digest="d1", evidence_repository=Repo([record]))
        assert report.status == "unknown"

def test_capability_version_must_match_when_candidate_version_is_known():
    matching = _evaluate_requirements(["counts words"], candidate_id="c1", manifest_digest="d1", capability_version="1.0", evidence_repository=Repo([evidence(version="1.0")]))
    omitted = _evaluate_requirements(["counts words"], candidate_id="c1", manifest_digest="d1", capability_version="1.0", evidence_repository=Repo([evidence(version=None)]))
    wrong = _evaluate_requirements(["counts words"], candidate_id="c1", manifest_digest="d1", capability_version="1.0", evidence_repository=Repo([evidence(version="2.0")]))
    assert matching.status == "pass" and omitted.status == wrong.status == "unknown"

def test_conflicting_duplicate_and_duplicate_criteria_are_conservative():
    report = _evaluate_requirements(["counts words", "counts words"], candidate_id="c1", manifest_digest="d1", evidence_repository=Repo([evidence(), evidence(status="fail")]))
    assert report.status == "unknown" and all(item.result == "unknown" for item in report.criteria)

def test_missing_repository_is_blocked_and_malformed_input_does_not_crash():
    assert evaluate_requirements(["counts words"]).status == "blocked"
    assert evaluate_requirements([None, ""]).status == "unknown"
