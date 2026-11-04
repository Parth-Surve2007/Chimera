import json
from pathlib import Path
import pytest
def test_flet_ui_uses_black_graphite_tokens_and_persistent_service():
    from chimera import ui
    assert ui.BG == "#000000" and ui.SURFACE == "#0A0A0A" and ui.ACCENT == "#C5C5C5" and ui.FOCUS == "#E0E0E0"
    assert "warm-gold" not in (Path(__file__).parents[1] / "chimera" / "ui.py").read_text(encoding="utf-8").lower()

def test_workspace_path_validation_accepts_real_directory_and_rejects_invalid(tmp_path):
    from chimera.ui import validate_workspace_path
    spaced = tmp_path / "Unicode workspace Ω"
    spaced.mkdir()
    assert validate_workspace_path(str(spaced)) == spaced.resolve()
    with pytest.raises(ValueError, match="does not exist"):
        validate_workspace_path(str(tmp_path / "missing"))
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a directory"):
        validate_workspace_path(file_path)

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


def test_default_workspace_root_uses_system_drive_on_windows(tmp_path):
    from chimera.ui import default_workspace_root

    assert str(default_workspace_root(system_drive="D:", platform="nt")) == "D:\\"
    assert default_workspace_root(platform="posix", home=tmp_path) == tmp_path.resolve()


def test_ui_uses_default_drive_without_workspace_picker_and_keeps_navigation():
    ui = (Path(__file__).parents[1] / "chimera" / "ui.py").read_text(encoding="utf-8")
    assert "default_workspace_root()" in ui
    assert "workspace_picker" not in ui
    assert "workspace_path_field" not in ui
    assert "ATTACH PROJECT" not in ui
    assert "DARK EMPEROR / LOCAL-FIRST" not in ui
    assert "self._chat_sidebar()," in ui


def test_non_chat_pages_keep_left_navigation_mounted():
    from types import SimpleNamespace
    from chimera import ui
    from chimera.ui import MainWindow

    window = object.__new__(MainWindow)
    window.active_page = "applications"
    window.agent_session_id = "nav-test"
    window.agent_store = SimpleNamespace(list_sessions=lambda limit=6: [])
    window.page = SimpleNamespace(update=lambda: None)
    window.body = ui.ft.Container(expand=True)

    window._set([ui.ft.Text("Applications", color=ui.TEXT)])

    assert isinstance(window.body.content, ui.ft.Row)
    assert len(window.body.content.controls) == 2
    assert window.body.content.controls[0].width == 238


def test_resuming_legacy_conversation_migrates_it_to_default_drive(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from chimera import ui
    from chimera.ui import MainWindow

    target_root = tmp_path / "drive"
    target_root.mkdir()
    old_workspace = tmp_path / "old-project"
    old_workspace.mkdir()
    saved = {}

    class Store:
        def session(self, session_id):
            return {"workspace": str(old_workspace)}

        def set_workspace(self, session_id, workspace):
            saved["workspace"] = workspace

        def messages(self, session_id):
            return []

        def pending_tool(self, session_id):
            return None

    window = object.__new__(MainWindow)
    window.agent_store = Store()
    window.agent_session_id = "legacy-session"
    window.drawer = None
    window.local_verification = None
    window.last_turn_notice = None
    window._bind_agent_session = lambda: None
    window._show_overview = lambda: None

    monkeypatch.setattr(ui, "default_workspace_root", lambda: target_root)
    window._resume_agent_session("legacy-session")

    assert window.workspace_path == target_root
    assert saved["workspace"] == target_root
