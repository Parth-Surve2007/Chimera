from pathlib import Path
from chimera.behavior import verify_behavior

def test_behavior_never_falls_back_without_execution_approval(tmp_path):
    candidate = tmp_path / "candidate"; candidate.mkdir(); (candidate / "main.py").write_text("print('fixture')")
    result = verify_behavior(candidate, "digest", False, None)
    assert result.status == "blocked"
    assert "approval" in result.diagnostics

def test_behavior_rejects_stale_execution_digest(tmp_path):
    candidate = tmp_path / "candidate"; candidate.mkdir()
    result = verify_behavior(candidate, "current", True, "old")
    assert result.status == "blocked"
    assert "manifest digest" in result.diagnostics
