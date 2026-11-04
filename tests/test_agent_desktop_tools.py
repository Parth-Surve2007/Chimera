from pathlib import Path
from types import SimpleNamespace

from chimera.agent_runtime import AgentRuntime, AgentSessionStore
from chimera.computer_use import WindowObservation, WindowsComputerUse
from chimera.local_model import LocalModelConfig, LocalModelProvider
from chimera.tools import WorkspaceTools


class _UnusedProvider:
    config = SimpleNamespace(model_path=None)


def test_workspace_file_creation_is_bounded_and_requires_approval(tmp_path):
    tools = WorkspaceTools(tmp_path, tmp_path / ".chimera-agent-artifacts" / "session")
    arguments = {"path": "notes/hello.txt", "content": "Chimera is ready."}
    pending = tools.run("create_file", arguments, approved=False)
    assert pending.status == "awaiting_approval"
    assert not (tmp_path / "notes" / "hello.txt").exists()

    completed = tools.run("create_file", arguments, approved=True)
    target = tmp_path / "notes" / "hello.txt"
    assert completed.status == "completed"
    assert completed.data["path"] == "notes/hello.txt"
    assert target.read_text(encoding="utf-8") == "Chimera is ready."

    duplicate = tools.run("create_file", arguments, approved=True)
    assert duplicate.status == "failed"
    assert "refusing to overwrite" in duplicate.error


def test_workspace_file_creation_rejects_path_escape_and_unsupported_extension(tmp_path):
    tools = WorkspaceTools(tmp_path, tmp_path / "out")
    escaped = tools.run("create_file", {"path": "../outside.txt", "content": "x"}, approved=True)
    unsupported = tools.run("create_file", {"path": "payload.exe", "content": "x"}, approved=True)
    assert escaped.status == "failed" and "escapes" in escaped.error
    assert unsupported.status == "failed" and "extensions" in unsupported.error


def test_runtime_requires_build_approval_for_workspace_file_creation(tmp_path):
    class DecidingProvider:
        config = SimpleNamespace(model_path=None)

        def __init__(self):
            self.decisions = iter([
                {"type": "tool_call", "tool": "workspace.create_file", "arguments": {
                    "path": "notes/approved.txt", "content": "created only after approval",
                }, "invocation_id": "create-file-1"},
                {"type": "message", "content": "The file was created and verified."},
            ])

        def decide(self, messages, catalog, session=None):
            return next(self.decisions)

    runtime = AgentRuntime(AgentSessionStore(tmp_path / "chimera.db"), DecidingProvider())
    session_id = runtime.create_session(tmp_path, mode="build")
    pending = runtime.send(session_id, "Create a text file.")
    target = tmp_path / "notes" / "approved.txt"
    assert pending["status"] == "awaiting_approval" and not target.exists()

    completed = runtime.approve_pending(session_id, True)
    assert completed["status"] == "completed"
    assert target.read_text(encoding="utf-8") == "created only after approval"


def test_desktop_inventory_omits_blank_helper_windows_and_is_compact(tmp_path):
    backend = WindowsComputerUse(tmp_path)
    backend._windows = lambda: [
        WindowObservation(1, 10, "", "DummyDWMListenerWindow", True),
        WindowObservation(2, 11, "Untitled - Notepad", "Notepad", True),
    ]
    backend.process_path = lambda pid: r"C:\\Windows\\System32\\notepad.exe" if pid == 11 else None
    applications = backend.list_applications()
    assert len(applications) == 1
    assert applications[0]["pid"] == 11
    assert applications[0]["executable"].endswith("notepad.exe")
    assert applications[0]["windows"] == [{"hwnd": 2, "pid": 11, "title": "Untitled - Notepad"}]


def test_agent_tool_catalog_exposes_bounded_app_discovery_without_workspace(tmp_path):
    runtime = AgentRuntime(AgentSessionStore(tmp_path / "chimera.db"), _UnusedProvider())
    catalog = runtime._protocol_catalog({"session_id": "session-a", "workspace": None})
    ids = {tool["id"] for tool in catalog["tools"]}
    assert "computer.list_applications" in ids
    assert "computer.list_windows" not in ids
    assert "computer.inspect_window" not in ids
    assert "computer.launch" in ids and "workspace.create_file" not in ids


def test_agent_tool_catalog_exposes_file_creation_only_with_workspace(tmp_path):
    runtime = AgentRuntime(AgentSessionStore(tmp_path / "chimera.db"), _UnusedProvider())
    catalog = runtime._protocol_catalog({"session_id": "session-a", "workspace": str(tmp_path)})
    ids = {tool["id"] for tool in catalog["tools"]}
    assert "workspace.create_file" in ids
    assert "computer.list_applications" in ids
    assert "computer.list_windows" not in ids


def test_local_agent_prompt_compacts_history_and_omits_backend_inventory():
    provider = LocalModelProvider(LocalModelConfig(None, None, context=2048, max_tokens=256))
    captured = {}

    def fake_structured(prompt, **kwargs):
        captured["prompt"] = prompt
        return {"type": "message", "content": "Please attach the target folder first."}, {"context": 2048}

    provider.structured_with_metrics = fake_structured
    messages = [
        SimpleNamespace(role="tool", content='{"tool":"computer.list_applications","result":{"items":[{"pid":1,"executable":"notepad.exe","windows":[{"title":"Untitled"}]}]}}'),
        SimpleNamespace(role="user", content="Create a text file."),
    ]
    inventory = [{"backend": "win32", "description": "unused inventory payload " * 100} for _ in range(40)]
    result = provider.decide(
        messages,
        {"tools": [{"id": "computer.launch", "description": "Launch an approved app " * 30,
                    "arguments": {"executable": "string"}, "permission": "computer.launch"}],
         "authorized_applications": [], "interaction_inventory": inventory},
        session={"mode": "plan", "workspace": None},
    )
    assert result["type"] == "message"
    prompt_budget = max(3200, int(max(256, provider.config.context - provider.config.max_tokens - 128) * 3.2))
    assert len(captured["prompt"]) <= prompt_budget
    assert "interaction_inventory" not in captured["prompt"]
    assert "unused inventory payload" not in captured["prompt"]
    assert "Desktop inventory summary" in captured["prompt"]
    assert "- notepad.exe: Untitled" in captured["prompt"]
    assert "No user-facing applications found" not in captured["prompt"]


def test_new_local_model_context_default_is_eight_k():
    assert LocalModelConfig(None, None).context == 8192


def test_ui_removes_verify_controls_and_optional_langflow_setup():
    ui = (Path(__file__).parents[1] / "chimera" / "ui.py").read_text(encoding="utf-8")
    assert "VERIFY MODEL" not in ui
    assert "VERIFY AGENT PROTOCOL" not in ui
    assert "INSTALL LANGFLOW" not in ui and "LangflowService" not in ui
    assert 'font_family=getattr(self, "logo_font_family", "Segoe UI")' in ui
    assert 'tooltip="Return to the Chimera chat"' in ui
    assert "async def _approve_agent_tool_yes" in ui
    assert 'self._button("APPROVE THIS ACTION", self._approve_agent_tool_yes)' in ui
    assert "self.page.run_task(self._watch_agent)" not in ui
    assert "auto_scroll=True" in ui
    assert "self._context_rail()," not in ui
    assert "list_sessions(limit=6)" in ui


def test_workspace_file_creation_rejects_absolute_host_paths_before_approval(tmp_path):
    tools = WorkspaceTools(tmp_path, tmp_path / "out")
    result = tools.run(
        "create_file",
        {"path": "/home/user/Desktop/hello.txt", "content": "Hello World"},
        approved=False,
    )
    assert result.status == "failed"
    assert "workspace-relative" in result.error
    assert not (tmp_path / "hello.txt").exists()


def test_workspace_file_creation_rejects_windows_drive_paths_on_all_platforms(tmp_path):
    tools = WorkspaceTools(tmp_path, tmp_path / "out")
    result = tools.run(
        "create_file",
        {"path": r"C:\Users\Parth\Desktop\hello.txt", "content": "Hello World"},
        approved=False,
    )
    assert result.status == "failed"
    assert "workspace-relative" in result.error
    assert not (tmp_path / "C:" / "Users" / "Parth" / "Desktop" / "hello.txt").exists()


def test_runtime_rejects_invalid_create_file_path_before_operator_approval(tmp_path):
    class DecidingProvider:
        config = SimpleNamespace(model_path=None)

        def __init__(self):
            self.decisions = iter([
                {"type": "tool_call", "tool": "workspace.create_file", "arguments": {
                    "path": "/home/user/Desktop/hello.txt", "content": "Hello World",
                }, "invocation_id": "absolute-path"},
                {"type": "message", "content": "The supplied path was invalid; no file was written."},
            ])

        def decide(self, messages, catalog, session=None):
            return next(self.decisions)

    runtime = AgentRuntime(
        AgentSessionStore(tmp_path / "chimera.db"),
        DecidingProvider(),
    )
    session_id = runtime.create_session(tmp_path, mode="build")
    result = runtime.send(session_id, "Create hello.txt on the Desktop.")

    assert result["status"] == "completed"
    assert runtime.store.pending_tool(session_id) is None
    assert not (tmp_path / "hello.txt").exists()
    tool_messages = [
        message for message in runtime.store.messages(session_id)
        if message.role == "tool"
    ]
    assert tool_messages
    payload = __import__("json").loads(tool_messages[0].content)
    assert payload["status"] == "failed"
    assert "workspace-relative" in payload["error"]


def test_workspace_file_creation_supports_common_source_files(tmp_path):
    tools = WorkspaceTools(tmp_path, tmp_path / "out")
    result = tools.run(
        "create_file",
        {"path": "src/app.py", "content": "print('hello from Chimera')\n"},
        approved=True,
    )
    assert result.status == "completed"
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "print('hello from Chimera')\n"


def test_workspace_directory_walk_is_bounded_and_lists_nested_files(tmp_path):
    root = tmp_path / "drive"
    (root / "Users" / "Parth" / "Desktop").mkdir(parents=True)
    (root / "Users" / "Parth" / "Desktop" / "hello.txt").write_text("hello", encoding="utf-8")
    (root / "Projects").mkdir()
    (root / "Projects" / "readme.md").write_text("project", encoding="utf-8")

    tools = WorkspaceTools(root, root / "out")
    listed = tools.run("list_files", {"relative": ".", "recursive": True})
    searched = tools.run("search_files", {"pattern": "*.md"})

    assert listed.status == "completed"
    assert set(listed.data["files"]) == {
        "Projects/readme.md",
        "Users/Parth/Desktop/hello.txt",
    }
    assert searched.data["files"] == ["Projects/readme.md"]


def test_agent_session_can_persist_default_drive_scope(tmp_path):
    store = AgentSessionStore(tmp_path / "chimera.db")
    session_id = store.create_session(None, None)
    store.set_workspace(session_id, tmp_path)
    assert store.session(session_id)["workspace"] == str(tmp_path.resolve())


def test_builtin_capability_catalog_is_present_and_workspace_actions_resolve():
    from chimera.builtin_capabilities import BUILTIN_CAPABILITIES, builtin_capability_ids, validate_builtin_catalog

    assert len(BUILTIN_CAPABILITIES) >= 7
    assert "3d.blender" in builtin_capability_ids()
    assert "reverse_engineering.rea" in builtin_capability_ids()
    assert validate_builtin_catalog()



def test_agent_application_inventory_ignores_irrelevant_model_arguments(tmp_path, monkeypatch):
    import json

    class DecidingProvider:
        config = SimpleNamespace(model_path=None)

        def __init__(self):
            self.decisions = iter([
                {
                    "type": "tool_call",
                    "tool": "computer.list_applications",
                    "arguments": {"application_name": "Word", "filter": "Word"},
                    "invocation_id": "inventory-with-extra-args",
                },
                {"type": "message", "content": "Microsoft Word is visible."},
            ])

        def decide(self, messages, catalog, session=None):
            return next(self.decisions)

    expected_inventory = [{
        "name": "Microsoft Word",
        "pid": 1234,
        "executable": "C:\\Program Files\\Microsoft Office\\root\\Office16\\WINWORD.EXE",
        "windows": [{"hwnd": 77, "pid": 1234, "title": "Document1 - Word"}],
    }]
    monkeypatch.setattr(WindowsComputerUse, "list_applications", lambda self: expected_inventory)

    runtime = AgentRuntime(
        AgentSessionStore(tmp_path / "chimera.db"),
        DecidingProvider(),
    )
    session_id = runtime.create_session(tmp_path, mode="plan")
    result = runtime.send(session_id, "Find the visible Word window.")

    assert result["status"] == "completed"
    assert "Microsoft Word" in result["message"].content
    tool_messages = [
        message for message in runtime.store.messages(session_id)
        if message.role == "tool"
    ]
    assert tool_messages
    payload = json.loads(tool_messages[0].content)
    assert payload["status"] == "completed"
    assert payload["result"] == {"items": expected_inventory}


def test_application_inventory_remains_tolerant_only_for_no_argument_reads():
    import pytest

    AgentRuntime._validate_arguments("computer.list_applications", {"filter": "Word"})
    AgentRuntime._validate_arguments("computer.list_windows", {"application": "Word"})
    with pytest.raises(ValueError, match="unsupported arguments"):
        AgentRuntime._validate_arguments(
            "computer.inspect_controls",
            {"session_id": "session", "hwnd": 1, "extra": "not allowed"},
        )


def test_standalone_launch_request_is_completed_from_confirmed_windows_result(tmp_path, monkeypatch):
    class DecidingProvider:
        config = SimpleNamespace(model_path=None)

        def __init__(self):
            self.calls = 0

        def decide(self, messages, catalog, session=None):
            self.calls += 1
            return {
                "type": "tool_call",
                "tool": "computer.launch",
                "arguments": {"executable": "Notepad"},
                "invocation_id": "launch-notepad",
            }

    def fake_launch(self, executable, approved=False):
        if not approved:
            return {
                "status": "awaiting_approval",
                "application": r"C:\\Windows\\System32\\notepad.exe",
                "requested_application": executable,
                "permission": "computer.launch",
            }
        return {
            "status": "completed",
            "pid": 456,
            "executable": r"C:\\Windows\\System32\\notepad.exe",
            "windows": [{"hwnd": 77, "pid": 456, "title": "Untitled - Notepad"}],
        }

    monkeypatch.setattr(WindowsComputerUse, "launch", fake_launch)
    provider = DecidingProvider()
    runtime = AgentRuntime(AgentSessionStore(tmp_path / "chimera.db"), provider)
    session_id = runtime.create_session(tmp_path, mode="build")

    pending = runtime.send(session_id, "Open Notepad. Do not create a text file.")
    assert pending["status"] == "awaiting_approval"

    completed = runtime.approve_pending(session_id, True)

    assert completed["status"] == "completed"
    assert "Notepad opened successfully" in completed["message"].content
    assert "Process ID: 456" in completed["message"].content
    assert "Untitled - Notepad" in completed["message"].content
    assert provider.calls == 1, "a standalone launch should not need a second model inference"
    assert runtime.store.session(session_id)["state"] == "idle"


def test_launch_completion_shortcut_does_not_skip_requested_follow_up_work():
    assert AgentRuntime._is_terminal_launch_request("Open Notepad. Do not create a text file.")
    assert not AgentRuntime._is_terminal_launch_request("Open Word and create a document.")
    assert not AgentRuntime._is_terminal_launch_request("Launch Notepad, then type hello.")



def test_runtime_completes_verified_text_file_write_without_second_model_call(tmp_path):
    class DecidingProvider:
        config = SimpleNamespace(model_path=None)

        def __init__(self):
            self.calls = 0

        def decide(self, messages, catalog, session=None):
            self.calls += 1
            return {
                "type": "tool_call",
                "tool": "workspace.create_file",
                "arguments": {"path": "hello.txt", "content": "Hello from Chimera."},
                "invocation_id": "create-text-file",
            }

    provider = DecidingProvider()
    runtime = AgentRuntime(AgentSessionStore(tmp_path / "chimera.db"), provider)
    session_id = runtime.create_session(tmp_path, mode="build")
    pending = runtime.send(session_id, "Create a text file named hello.txt with Hello from Chimera.")

    assert pending["status"] == "awaiting_approval"
    completed = runtime.approve_pending(session_id, True)

    target = tmp_path / "hello.txt"
    assert completed["status"] == "completed"
    assert target.read_text(encoding="utf-8") == "Hello from Chimera."
    assert "Created and verified the text file" in completed["message"].content
    assert completed["verification"]["sha256"]
    assert provider.calls == 1
    assert runtime.store.session(session_id)["state"] == "idle"


def test_runtime_creates_real_word_document_for_docx_request(tmp_path):
    import zipfile

    class DecidingProvider:
        config = SimpleNamespace(model_path=None)

        def __init__(self):
            self.calls = 0

        def decide(self, messages, catalog, session=None):
            self.calls += 1
            return {
                "type": "tool_call",
                "tool": "workspace.create_document",
                "arguments": {
                    "path": "Cake-Recipe.docx",
                    "title": "Chocolate Cake Recipe",
                    "content": "Ingredients: flour, cocoa, sugar.\n\nMethod: mix and bake.",
                },
                "invocation_id": "create-word-document",
            }

    provider = DecidingProvider()
    runtime = AgentRuntime(AgentSessionStore(tmp_path / "chimera.db"), provider)
    session_id = runtime.create_session(tmp_path, mode="build")
    pending = runtime.send(
        session_id,
        "Create a Word document named Cake-Recipe.docx with a chocolate cake recipe.",
    )

    assert pending["status"] == "awaiting_approval"
    completed = runtime.approve_pending(session_id, True)

    target = tmp_path / "Cake-Recipe.docx"
    assert completed["status"] == "completed"
    assert target.is_file()
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        xml = archive.read("word/document.xml").decode("utf-8")
    assert "Chocolate Cake Recipe" in xml
    assert "mix and bake" in xml
    assert "Created and verified the Word document" in completed["message"].content
    assert provider.calls == 1


def test_terminal_file_creation_detection_preserves_requested_follow_up_work():
    assert AgentRuntime._is_terminal_file_creation_request("Create a text file with hello.")
    assert AgentRuntime._is_terminal_file_creation_request("Create a Word document with a recipe.")
    assert not AgentRuntime._is_terminal_file_creation_request("Create a file and then open it.")
    assert not AgentRuntime._is_terminal_file_creation_request("Create a file, then email it.")


def test_local_model_prompt_routes_docx_requests_to_document_tool():
    provider = LocalModelProvider(LocalModelConfig(None, None, context=4096))
    captured = {}

    def fake_structured(prompt, **kwargs):
        captured["prompt"] = prompt
        return {"type": "message", "content": "Please confirm the document title."}, {"context": 4096}

    provider.structured_with_metrics = fake_structured
    result = provider.decide(
        [SimpleNamespace(role="user", content="Create a Word document on my Desktop.")],
        {"tools": [
            {"id": "workspace.create_document", "description": "Create a Word DOCX document", "arguments": {"path": "string", "title": "string", "content": "string"}, "permission": "execution.write"},
            {"id": "workspace.create_file", "description": "Create plain text files", "arguments": {"path": "string", "content": "string"}, "permission": "execution.write"},
        ]},
        session={"mode": "build", "workspace": r"C:\\Users\\Parth"},
    )

    assert result["type"] == "message"
    assert "workspace.create_document" in captured["prompt"]
    assert "Never silently downgrade a requested .docx to .txt" in captured["prompt"]
    assert "workspace.create_file for a plain .txt" in captured["prompt"]
