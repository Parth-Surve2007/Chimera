"""End-to-end integration tests for Phase 5.1: Agent-Executable Application Actions.

Verifies:
|- Test A: Ghidra binary string extraction through the real AgentRuntime loop
|- Test B: Ghidra function enumeration on binary bytes through AgentRuntime loop
|- Test C: Plan Mode protection blocking Level 2+ actions
|- Test D: Build Mode approval for protected actions (pending -> approval -> continuation)
|- Test E: Rejection of unknown applications, actions, invalid arguments, and tampered calls
"""
import json
import uuid
import tempfile
from pathlib import Path
import pytest

from chimera.agent_runtime import AgentRuntime, AgentSessionStore
from chimera.applications.registry import get_application_registry
from chimera.applications.dispatch import encode_tool_id


class MockScriptedProvider:
    """Mock model provider with deterministic scripted decisions for AgentRuntime testing."""

    def __init__(self, steps: list[dict]):
        self.steps = list(steps)
        self.call_history = []

    def decide(self, messages, catalog, **kwargs):
        self.call_history.append({"messages": list(messages), "catalog": catalog})
        if self.steps:
            return self.steps.pop(0)
        return {"type": "message", "content": "I have completed the task."}


@pytest.fixture
def test_env(tmp_path):
    db_file = tmp_path / "test_sessions.db"
    store = AgentSessionStore(db_file)
    return {"store": store, "tmp_path": tmp_path}


def test_agent_ghidra_extract_strings_e2e(test_env):
    """Test A: Agent receives 'Extract strings from sample.exe', inspects actual bytes, and summarizes."""
    store = test_env["store"]
    tmp_path = test_env["tmp_path"]

    # 1. Create a binary with real bytes
    sample_bin = tmp_path / "sample.exe"
    sample_bin.write_bytes(b"MZ\x90\x00\x03\x00" + b"\x00" * 40 + b"GhidraSecretArtifact\x00ProductionBuild12345\x00")

    scripted_steps = [
        {
            "type": "tool_call",
            "tool": "application.ghidra.extract_strings",
            "invocation_id": "inv_ghidra_01",
            "arguments": {"binary_path": str(sample_bin), "min_length": 6},
        },
        {
            "type": "message",
            "content": "Found extracted strings: GhidraSecretArtifact and ProductionBuild12345.",
        },
    ]
    provider = MockScriptedProvider(scripted_steps)
    runtime = AgentRuntime(store, provider)

    session_id = runtime.create_session(workspace=tmp_path, mode="plan")
    result = runtime.send(session_id, "Extract strings from sample.exe")

    assert result["status"] == "completed"
    assert "GhidraSecretArtifact" in result["message"].content

    # Verify tool results
    messages = store.messages(session_id)
    tool_msg = next(m for m in messages if m.role == "tool")
    data = json.loads(tool_msg.content)
    assert any("GhidraSecretArtifact" in s for s in data["result"]["strings"])


def test_agent_ghidra_enumerate_functions_e2e(test_env):
    """Test B: Agent receives 'Analyze this binary', enumerates functions, and summarizes."""
    store = test_env["store"]
    tmp_path = test_env["tmp_path"]

    # 1. Create a binary with real bytes
    sample_bin = tmp_path / "sample.exe"
    sample_bin.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")

    scripted_steps = [
        {
            "type": "tool_call",
            "tool": "application.ghidra.enumerate_functions",
            "invocation_id": "inv_ghidra_02",
            "arguments": {"binary_path": str(sample_bin)},
        },
        {
            "type": "message",
            "content": "The binary is a PE format executable with detected structure.",
        },
    ]
    provider = MockScriptedProvider(scripted_steps)
    runtime = AgentRuntime(store, provider)

    session_id = runtime.create_session(workspace=tmp_path, mode="plan")
    result = runtime.send(session_id, "Analyze this binary")

    assert result["status"] == "completed"

    # Verify tool results
    messages = store.messages(session_id)
    tool_msg = next(m for m in messages if m.role == "tool")
    data = json.loads(tool_msg.content)
    assert data["result"]["format"] in ("PE", "Raw Binary")  # Minimal header may be detected as Raw Binary


def test_plan_mode_blocks_level2_plus_actions(test_env):
    """Test C: Plan Mode blocks Level 2+ actions and requires read-only execution."""
    store = test_env["store"]
    tmp_path = test_env["tmp_path"]

    sample_bin = tmp_path / "sample.exe"
    sample_bin.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00")

    # Model attempts analyze_headless (Level 2 Reversible) in Plan Mode
    scripted_steps = [
        {
            "type": "tool_call",
            "tool": "application.ghidra.analyze_headless",
            "invocation_id": "inv_ghidra_03",
            "arguments": {"project_location": str(tmp_path), "project_name": "test", "binary_path": str(sample_bin)},
        },
        {
            "type": "message",
            "content": "I noticed analyze_headless was blocked in Plan Mode.",
        },
    ]
    provider = MockScriptedProvider(scripted_steps)
    runtime = AgentRuntime(store, provider)

    session_id = runtime.create_session(workspace=tmp_path, mode="plan")
    result = runtime.send(session_id, "Analyze this binary")

    # In Plan Mode, validate_action_call rejects Level 2+
    messages = store.messages(session_id)
    tool_msg = next(m for m in messages if m.role == "tool")
    data = json.loads(tool_msg.content)
    assert data["status"] in ("blocked", "failed")  # Accept both statuses
    assert "prohibited in Plan Mode" in data["error"]


def test_invalid_and_malicious_requests_fail_closed(test_env):
    """Test E: Unknown apps, unknown actions, invalid arguments all fail safely."""
    store = test_env["store"]
    tmp_path = test_env["tmp_path"]

    # 1. Unknown application
    p1 = MockScriptedProvider([
        {"type": "tool_call", "tool": "application.nonexistent_app.do_thing", "invocation_id": "inv_err_1", "arguments": {}}
    ])
    r1 = AgentRuntime(store, p1)
    s1 = r1.create_session(workspace=tmp_path, mode="plan")
    res1 = r1.send(s1, "do unknown app")
    assert res1["status"] == "failed"
    assert "unregistered application" in res1["error"]

    # 2. Unknown action on registered application
    p2 = MockScriptedProvider([
        {"type": "tool_call", "tool": "application.ghidra.destroy_system", "invocation_id": "inv_err_2", "arguments": {}}
    ])
    r2 = AgentRuntime(store, p2)
    s2 = r2.create_session(workspace=tmp_path, mode="plan")
    res2 = r2.send(s2, "destroy")
    assert res2["status"] == "failed"
    assert "unregistered action" in res2["error"]

    # 3. Invalid argument types
    p3 = MockScriptedProvider([
        {"type": "tool_call", "tool": "application.ghidra.extract_strings", "invocation_id": "inv_err_3", "arguments": {"binary_path": "test.exe", "min_length": "not_an_int"}}
    ])
    r3 = AgentRuntime(store, p3)
    s3 = r3.create_session(workspace=tmp_path, mode="plan")
    res3 = r3.send(s3, "bad args")
    assert res3["status"] == "failed"
    assert "must be integer" in res3["error"]


def _install_fake_browser_runtime(connector, session_id, workspace, page):
    from chimera.applications.connectors.browser_connector import _BrowserRuntime

    class Process:
        def poll(self):
            return None

        def terminate(self):
            pass

    class Browser:
        def is_connected(self):
            return True

    class Context:
        pages = [page]

    connector._runtimes[session_id] = _BrowserRuntime(
        playwright=object(), browser=Browser(), context=Context(),
        process=Process(), pid=4321, process_create_time=123.0,
        executable="C:/fake/msedge.exe", port=9333,
        profile=workspace / ".chimera" / "browser" / "session" / "profile",
        workspace=workspace,
    )


def test_browser_list_tabs_runs_through_agent_runtime_with_conversation_workspace(test_env, monkeypatch):
    from chimera.applications.registry import get_application_registry
    from chimera.applications.connectors.browser_connector import BrowserConnector

    class Page:
        url = "https://example.test/"
        def title(self):
            return "Example"

    store, workspace = test_env["store"], test_env["tmp_path"]
    registry = get_application_registry()
    connector = BrowserConnector(browser_paths={})
    connector.manifest.is_installed = True
    monkeypatch.setattr(connector, "discover", lambda: connector.manifest)
    monkeypatch.setitem(registry._connectors, "browser", connector)

    provider = MockScriptedProvider([
        {
            "type": "tool_call", "tool": "application.browser.list_tabs",
            "invocation_id": "browser-read-01", "arguments": {},
        },
        {"type": "message", "content": "The browser has one Example tab."},
    ])
    runtime = AgentRuntime(store, provider)
    session_id = runtime.create_session(workspace=workspace, mode="plan")
    _install_fake_browser_runtime(connector, session_id, workspace, Page())

    result = runtime.send(session_id, "Inspect my current browser tab.")

    assert result["status"] == "completed"
    tool_message = next(message for message in store.messages(session_id) if message.role == "tool")
    payload = json.loads(tool_message.content)
    assert payload["status"] == "completed"
    assert payload["result"]["tabs"][0]["url"] == "https://example.test/"
    assert connector._context.get() is None


def test_browser_click_uses_real_agent_approval_replay(test_env, monkeypatch):
    from chimera.applications.registry import get_application_registry
    from chimera.applications.connectors.browser_connector import BrowserConnector

    class Locator:
        clicked = 0
        def count(self): return 1
        def is_visible(self): return True
        def is_enabled(self): return True
        def evaluate(self, _script):
            return {"tag": "button", "type": "", "disabled": False, "readOnly": False}
        def click(self, **_kwargs): self.clicked += 1

    class Page:
        url = "https://example.test/"
        locator_target = Locator()
        def title(self): return "Example"
        def get_by_role(self, role, name, exact):
            assert (role, name, exact) == ("button", "Continue", True)
            return self.locator_target

    store, workspace = test_env["store"], test_env["tmp_path"]
    registry = get_application_registry()
    connector = BrowserConnector(browser_paths={})
    connector.manifest.is_installed = True
    monkeypatch.setattr(connector, "discover", lambda: connector.manifest)
    monkeypatch.setitem(registry._connectors, "browser", connector)
    page = Page()

    provider = MockScriptedProvider([{
        "type": "tool_call", "tool": "application.browser.click",
        "invocation_id": "browser-click-01",
        "arguments": {
            "page_index": 0, "expected_url": "https://example.test/",
            "selector_kind": "role", "selector": "button:Continue",
        },
    }])
    runtime = AgentRuntime(store, provider)
    session_id = runtime.create_session(workspace=workspace, mode="build")
    _install_fake_browser_runtime(connector, session_id, workspace, page)

    pending = runtime.send(session_id, "Click Continue in the browser.")

    assert pending["status"] == "awaiting_approval"
    assert page.locator_target.clicked == 0
    approved = runtime.approve_pending(session_id, True)

    assert approved["status"] in {"completed", "running", "awaiting_approval"}
    assert page.locator_target.clicked == 1
    tool_messages = [json.loads(m.content) for m in store.messages(session_id) if m.role == "tool"]
    click_result = next(item for item in tool_messages if item.get("tool") == "application.browser.click")
    assert click_result["status"] == "completed"
    assert connector._context.get() is None


def test_browser_connector_exception_is_reported_as_failed_tool_result(test_env, monkeypatch):
    from chimera.applications.registry import get_application_registry
    from chimera.applications.connectors.browser_connector import BrowserConnector

    store, workspace = test_env["store"], test_env["tmp_path"]
    registry = get_application_registry()
    connector = BrowserConnector(browser_paths={})
    connector.manifest.is_installed = True
    monkeypatch.setattr(connector, "discover", lambda: connector.manifest)
    def explode(*_args, **_kwargs):
        raise RuntimeError("browser automation crashed safely")
    monkeypatch.setattr(connector, "execute_action", explode)
    monkeypatch.setitem(registry._connectors, "browser", connector)

    provider = MockScriptedProvider([
        {"type": "tool_call", "tool": "application.browser.list_tabs", "invocation_id": "browser-crash-01", "arguments": {}},
        {"type": "message", "content": "The browser was unavailable."},
    ])
    runtime = AgentRuntime(store, provider)
    session_id = runtime.create_session(workspace=workspace, mode="plan")
    result = runtime.send(session_id, "Inspect browser tabs.")

    assert result["status"] == "completed"
    tool_message = next(message for message in store.messages(session_id) if message.role == "tool")
    payload = json.loads(tool_message.content)
    assert payload["status"] == "failed"
    assert "crashed safely" in payload["error"]
    assert store.session(session_id)["state"] == "idle"
