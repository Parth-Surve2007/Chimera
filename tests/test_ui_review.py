import json
def test_flet_ui_uses_warm_design_tokens_and_persistent_service():
    from chimera import ui
    assert ui.BG == "#070808" and ui.ACCENT == "#8AAE9C" and not any("blue" in value.lower() for value in (ui.BG, ui.SURFACE, ui.ACCENT))

def test_evaluation_rows_preserve_all_four_states_and_provenance():
    from chimera.ui import evaluation_rows
    report = {"criteria": [{"criterion_id": state, "result": state, "reason": state, "evidence_refs": ["run:1"], "remaining_evidence": ["setup"]} for state in ("pass", "fail", "unknown", "blocked")]}
    rows = evaluation_rows(report)
    assert [row[1] for row in rows] == ["pass", "fail", "unknown", "blocked"]
    assert all("run:1" in row[3] and "setup" in row[3] for row in rows)

def test_missing_or_malformed_evaluation_is_honest():
    from chimera.ui import evaluation_rows
    assert evaluation_rows(None)[0][1] == "unknown"
    assert evaluation_rows({"status": "unavailable", "reason": "history unavailable"})[0][2] == "history unavailable"
    assert evaluation_rows({"criteria": [{"result": "made_up"}]})[0][1] == "unknown"

def test_traceability_rows_preserve_uncovered_and_partial_requirements():
    from chimera.ui import traceability_rows
    rows = traceability_rows([{"requirement_id": "requirement-1", "requirement": "export JSON", "criterion_id": "criterion-1", "coverage": "partial", "proposal_intent": "writes a file", "unresolved": "format not verified"}, {"requirement_id": "requirement-2", "requirement": "sign output", "coverage": "missing", "unresolved": "no proposal mapping"}])
    assert rows[0][1] == "partial" and "format not verified" in rows[0][2]
    assert rows[1][1] == "missing" and "NO CRITERION" in rows[1][0]

def test_legacy_traceability_is_unavailable():
    from chimera.ui import traceability_rows
    assert traceability_rows(None)[0][1] == "unavailable"
