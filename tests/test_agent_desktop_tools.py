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


def test_agent_tool_catalog_hides_unscoped_desktop_inventory(tmp_path):
    runtime = AgentRuntime(AgentSessionStore(tmp_path / "chimera.db"), _UnusedProvider())
    catalog = runtime._protocol_catalog({"session_id": "session-a", "workspace": None})
    ids = {tool["id"] for tool in catalog["tools"]}
    assert "computer.list_applications" not in ids and "computer.list_windows" not in ids
    assert "computer.launch" in ids and "workspace.create_file" not in ids


def test_agent_tool_catalog_exposes_file_creation_only_with_workspace(tmp_path):
    runtime = AgentRuntime(AgentSessionStore(tmp_path / "chimera.db"), _UnusedProvider())
    catalog = runtime._protocol_catalog({"session_id": "session-a", "workspace": str(tmp_path)})
    ids = {tool["id"] for tool in catalog["tools"]}
    assert "workspace.create_file" in ids
    assert "computer.list_applications" not in ids


def test_local_agent_prompt_compacts_history_and_omits_backend_inventory():
    provider = LocalModelProvider(LocalModelConfig(None, None, context=2048))
    captured = {}

    def fake_structured(prompt, **kwargs):
        captured["prompt"] = prompt
        return {"type": "message", "content": "Please attach the target folder first."}, {"context": 2048}

    provider.structured_with_metrics = fake_structured
    messages = [
        SimpleNamespace(role="tool", content='{"tool":"computer.list_applications","result":[{"pid":1,"executable":"notepad.exe","windows":[{"title":"Untitled"}]}]}'),
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
    assert len(captured["prompt"]) < 5000
    assert "interaction_inventory" not in captured["prompt"]
    assert "unused inventory payload" not in captured["prompt"]
    assert "Desktop inventory summary" in captured["prompt"]


def test_new_local_model_context_default_is_four_k():
    assert LocalModelConfig(None, None).context == 4096


def test_ui_removes_verify_controls_and_optional_langflow_setup():
    ui = (Path(__file__).parents[1] / "chimera" / "ui.py").read_text(encoding="utf-8")
    assert "VERIFY MODEL" not in ui
    assert "VERIFY AGENT PROTOCOL" not in ui
    assert "INSTALL LANGFLOW" not in ui and "LangflowService" not in ui
    assert "font_family=self.logo_font_family" in ui
    assert 'tooltip="Return to the Chimera chat"' in ui
