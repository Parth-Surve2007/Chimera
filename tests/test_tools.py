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


def test_workspace_reads_are_side_effect_free(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = workspace / ".chimera-agent-artifacts" / "session"
    tools = WorkspaceTools(workspace, output)
    assert not output.exists()
    result = tools.run("list_files", {"relative": ".", "recursive": True})
    assert result.status == "completed"
    assert not output.exists()


def test_search_text_never_follows_external_symlinks(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("sensitive-marker", encoding="utf-8")
    (workspace / "inside.txt").write_text("ordinary", encoding="utf-8")
    link = workspace / "linked.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        return
    result = WorkspaceTools(workspace, workspace / "out").run(
        "search_text", {"query": "sensitive-marker"}
    )
    assert result.status == "completed"
    assert result.data["matches"] == []



def test_create_document_writes_a_valid_word_docx_only_after_approval(tmp_path):
    import hashlib
    import xml.etree.ElementTree as ET
    import zipfile

    workspace = tmp_path / "drive"
    workspace.mkdir()
    target = workspace / "Users" / "Parth" / "Desktop" / "Cake-Recipe.docx"
    tools = WorkspaceTools(workspace, workspace / "out")
    arguments = {
        "path": "Users/Parth/Desktop/Cake-Recipe.docx",
        "title": "Chocolate Cake Recipe",
        "content": "# Ingredients\n- Flour\n- Cocoa\n\n## Method\nMix and bake.",
    }

    pending = tools.run("create_document", arguments, approved=False)
    assert pending.status == "awaiting_approval"
    assert not target.exists()

    created = tools.run("create_document", arguments, approved=True)
    assert created.status == "completed"
    assert created.data["path"] == "Users/Parth/Desktop/Cake-Recipe.docx"
    assert created.data["format"] == "docx"
    assert created.data["bytes"] == target.stat().st_size
    assert created.data["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()

    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert {
            "[Content_Types].xml",
            "_rels/.rels",
            "word/document.xml",
            "word/styles.xml",
        }.issubset(set(archive.namelist()))
        document = ET.fromstring(archive.read("word/document.xml"))
    text = " ".join(
        node.text or "" for node in document.iter()
        if node.tag.endswith("}t")
    )
    assert "Chocolate Cake Recipe" in text
    assert "Ingredients" in text and "Flour" in text and "Mix and bake." in text

    duplicate = tools.run("create_document", arguments, approved=True)
    assert duplicate.status == "failed"
    assert "refusing to overwrite" in duplicate.error


def test_create_document_rejects_non_docx_and_workspace_escape(tmp_path):
    tools = WorkspaceTools(tmp_path, tmp_path / "out")
    bad_extension = tools.run(
        "create_document",
        {"path": "recipe.txt", "title": "Recipe", "content": "Cake"},
        approved=True,
    )
    escaped = tools.run(
        "create_document",
        {"path": "../recipe.docx", "title": "Recipe", "content": "Cake"},
        approved=True,
    )
    assert bad_extension.status == "failed" and ".docx" in bad_extension.error
    assert escaped.status == "failed" and "escapes" in escaped.error



def test_create_pdf_is_approval_gated_and_independently_extractable(tmp_path):
    import hashlib
    from pypdf import PdfReader

    workspace = tmp_path / "drive"
    workspace.mkdir()
    target = workspace / "Desktop" / "Research-Notes.pdf"
    tools = WorkspaceTools(workspace, workspace / "out")
    source = (
        "The local-first agent must verify the actual output.\n"
        "Unicode check: café, naïve, £ and €.\n"
        "Literal markup must survive: <tag> & ampersand."
    )
    arguments = {
        "path": "Desktop/Research-Notes.pdf",
        "title": "Research Notes",
        "content": source,
    }

    pending = tools.run("create_pdf", arguments, approved=False)
    assert pending.status == "awaiting_approval"
    assert not target.exists()

    created = tools.run("create_pdf", arguments, approved=True)
    assert created.status == "completed"
    assert created.data["format"] == "pdf"
    assert created.data["text_verified"] is True
    assert created.data["page_count"] >= 1
    assert created.data["bytes"] == target.stat().st_size
    assert created.data["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert target.read_bytes().startswith(b"%PDF-")

    reader = PdfReader(str(target), strict=True)
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    normalize = lambda value: " ".join(value.replace("\\u00a0", " ").split())
    assert normalize("Research Notes\n" + source) == normalize(extracted)

    duplicate = tools.run("create_pdf", arguments, approved=True)
    assert duplicate.status == "failed"
    assert "refusing to overwrite" in duplicate.error


def test_create_pdf_rejects_bad_extension_escape_and_empty_content(tmp_path):
    tools = WorkspaceTools(tmp_path, tmp_path / "out")
    bad_extension = tools.run(
        "create_pdf",
        {"path": "report.txt", "title": "Report", "content": "Text"},
        approved=True,
    )
    escaped = tools.run(
        "create_pdf",
        {"path": "../report.pdf", "title": "Report", "content": "Text"},
        approved=True,
    )
    empty = tools.run(
        "create_pdf",
        {"path": "report.pdf", "title": "Report", "content": "  "},
        approved=True,
    )
    assert bad_extension.status == "failed" and ".pdf" in bad_extension.error
    assert escaped.status == "failed" and "escapes" in escaped.error
    assert empty.status == "failed" and "non-empty" in empty.error
