import json
import os
import pytest

def test_native_review_view_renders_persistent_candidate(tmp_path, monkeypatch):
    pytest.importorskip("PySide6"); monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from chimera.registry import Registry
    from chimera.ui import MainWindow
    data = tmp_path / "data"; root = data / "candidates" / "c1"; root.mkdir(parents=True); (root / "x.txt").write_text("safe"); (root / ".chimera-candidate.json").write_text(json.dumps({"candidate_id":"c1","name":"Candidate","version":"1","task":"test","status":"pending_review","permissions":[]}))
    app = QApplication.instance() or QApplication([]); window = MainWindow(Registry(data)); assert window.candidate_table.rowCount() == 1; assert window.candidate_table.item(0, 0).text() == "c1"; window.close()
