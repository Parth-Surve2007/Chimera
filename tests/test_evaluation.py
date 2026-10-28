from chimera.evaluation import evaluate_requirements

def test_evaluation_unknown_for_static_only():
    report = evaluate_requirements(["counts words"], candidate_id="c1", manifest_digest="d1", static_pass=True)
    assert report.status == "unknown" and report.criteria[0].result == "unknown"

def test_evaluation_pass_requires_matching_behavior_digest():
    report = evaluate_requirements(["counts words"], candidate_id="c1", manifest_digest="d1", behavioral={"status":"pass", "manifest_digest":"d1"})
    assert report.status == "pass"

def test_evaluation_stale_behavior_is_unknown():
    report = evaluate_requirements(["counts words"], candidate_id="c1", manifest_digest="d1", behavioral={"status":"pass", "manifest_digest":"old"})
    assert report.status == "unknown"

def test_evaluation_failure_is_distinct():
    report = evaluate_requirements(["counts words"], candidate_id="c1", manifest_digest="d1", behavioral={"status":"fail", "manifest_digest":"d1"})
    assert report.status == "fail" and report.criteria[0].result == "fail"

def test_empty_and_malformed_criteria_are_unknown():
    report = evaluate_requirements(["", "  "], static_pass=False)
    assert report.status == "unknown" and all(item.result == "unknown" for item in report.criteria)
