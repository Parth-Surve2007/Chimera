import json
from chimera.tools import WorkspaceTools

def test_workspace_tools_are_contained_and_bounded(tmp_path):
    (tmp_path / "main.py").write_text("DATABASE_URL = 'x'\n", encoding="utf-8")
    tools = WorkspaceTools(tmp_path, tmp_path / "out")
    listed = tools.run("list_files", {"relative": ".", "recursive": True})
    assert listed.status == "completed" and "main.py" in listed.data["files"]
    found = tools.run("search_text", {"query": "DATABASE"})
    assert found.status == "completed" and found.data["matches"][0]["path"] == "main.py"
    assert tools.run("read_text", {"path": "../main.py"}).status == "failed"
    assert tools.run("create_artifact", {"name": "report.md", "content": "ok"}).status == "awaiting_approval"
    created = tools.run("create_artifact", {"name": "report.md", "content": "ok"}, approved=True)
    assert created.status == "completed" and (tmp_path / "out" / "report.md").read_text() == "ok"

def test_workspace_tool_refuses_overwrite(tmp_path):
    out = tmp_path / "out"; out.mkdir(); (out / "x.md").write_text("old")
    result = WorkspaceTools(tmp_path, out).run("create_artifact", {"name": "x.md", "content": "new"}, approved=True)
    assert result.status == "failed" and (out / "x.md").read_text() == "old"
