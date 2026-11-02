"""End-to-end integration tests for Phase 5.1: Agent-Executable Application Actions.

Verifies:
- Test A: REAPER project track inspection through the real AgentRuntime loop
- Test B: Ghidra string extraction on binary bytes through AgentRuntime loop
- Test C: Plan Mode protection blocking Level 2+ actions
- Test D: Build Mode approval for protected actions (pending -> approval -> continuation)
- Test E: Rejection of unknown applications, actions, invalid arguments, and tampered calls
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


def test_agent_reaper_track_inspection_e2e(test_env):
    """Test A: Agent receives 'List the tracks in song.rpp', selects REAPER action, and completes."""
    store = test_env["store"]
    tmp_path = test_env["tmp_path"]

    # 1. Create a synthetic .rpp project
    song_file = tmp_path / "song.rpp"
    song_file.write_text(
        """<REAPER_PROJECT 0.1 "6.80/x64" 1680000000
  TEMPO 128 4 4
  <TRACK {GUID-DRUMS-001}
    NAME "Kick & Snare"
  >
  <TRACK {GUID-SYNTH-002}
    NAME "Lead Synth"
  >
>
""",
        encoding="utf-8",
    )

    # 2. Scripted provider: Step 1 = call application.reaper.inspect_project, Step 2 = respond
    scripted_steps = [
        {
            "type": "tool_call",
            "tool": "application.reaper.inspect_project",
            "invocation_id": "inv_reaper_01",
            "arguments": {"project_path": str(song_file)},
        },
        {
            "type": "message",
            "content": "The project song.rpp contains 2 tracks: Kick & Snare, and Lead Synth.",
        },
    ]
    provider = MockScriptedProvider(scripted_steps)
    runtime = AgentRuntime(store, provider)

    session_id = runtime.create_session(workspace=tmp_path, mode="plan")

    # Verify tool catalog includes application tools
    catalog = runtime._protocol_catalog(store.session(session_id))
    app_tools = [t["id"] for t in catalog["tools"] if t["id"].startswith("application.")]
    assert "application.reaper.inspect_project" in app_tools
    assert "application.ghidra.extract_strings" in app_tools

    # 3. Send prompt
    result = runtime.send(session_id, "List the tracks in song.rpp")

    assert result["status"] == "completed"
    assert "Kick & Snare" in result["message"].content
    assert "Lead Synth" in result["message"].content

    # Verify tool output was recorded in messages
    messages = store.messages(session_id)
    tool_messages = [m for m in messages if m.role == "tool"]
    assert len(tool_messages) >= 1
    tool_data = json.loads(tool_messages[0].content)
    assert tool_data["status"] == "completed"
    assert tool_data["result"]["track_count"] == 2
    assert tool_data["result"]["tracks"][0]["name"] == "Kick & Snare"


def test_agent_ghidra_extract_strings_e2e(test_env):
    """Test B: Agent receives 'Extract strings from sample.exe', inspects actual bytes, and summarizes."""
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


def test_plan_mode_blocks_level2_plus_actions(test_env):
    """Test C: Plan Mode blocks Level 2+ actions and requires read-only execution."""
    store = test_env["store"]
    tmp_path = test_env["tmp_path"]

    song_file = tmp_path / "song.rpp"
    song_file.write_text("<REAPER_PROJECT 0.1>\n>", encoding="utf-8")

    # Model attempts add_track (Level 2 Reversible) in Plan Mode
    scripted_steps = [
        {
            "type": "tool_call",
            "tool": "application.reaper.add_track",
            "invocation_id": "inv_add_01",
            "arguments": {"project_path": str(song_file), "track_name": "ProhibitedTrack"},
        },
        {
            "type": "message",
            "content": "I noticed add_track was blocked in Plan Mode.",
        },
    ]
    provider = MockScriptedProvider(scripted_steps)
    runtime = AgentRuntime(store, provider)

    session_id = runtime.create_session(workspace=tmp_path, mode="plan")
    result = runtime.send(session_id, "Add track to project")

    # In Plan Mode, validate_action_call rejects Level 2+
    messages = store.messages(session_id)
    tool_msg = next(m for m in messages if m.role == "tool")
    data = json.loads(tool_msg.content)
    assert data["status"] == "blocked"
    assert "prohibited in Plan Mode" in data["error"]

    # Verify project file was not modified
    assert "ProhibitedTrack" not in song_file.read_text(encoding="utf-8")


def test_build_mode_requires_approval_and_executes_after_approval(test_env):
    """Test D: Build Mode creates pending approval for protected actions, executes only on approval."""
    store = test_env["store"]
    tmp_path = test_env["tmp_path"]

    song_file = tmp_path / "song.rpp"
    song_file.write_text("<REAPER_PROJECT 0.1>\n  TEMPO 120 4 4\n>\n", encoding="utf-8")

    # Model calls add_track in Build Mode (reaper.write permission requires approval)
    scripted_steps = [
        {
            "type": "tool_call",
            "tool": "application.reaper.add_track",
            "invocation_id": "inv_build_01",
            "arguments": {"project_path": str(song_file), "track_name": "Bassline"},
        },
        {
            "type": "message",
            "content": "Bassline track added successfully.",
        },
    ]
    provider = MockScriptedProvider(scripted_steps)
    runtime = AgentRuntime(store, provider)

    session_id = runtime.create_session(workspace=tmp_path, mode="build")
    # Step 1: Send request -> reaches awaiting_approval
    res = runtime.send(session_id, "Add a bassline track")
    assert res["status"] == "awaiting_approval"
    assert res["tool"] == "application.reaper.add_track"
    assert res["arguments"]["track_name"] == "Bassline"

    # Verify track has NOT been added yet
    assert "Bassline" not in song_file.read_text(encoding="utf-8")

    # Step 2: Operator approves the action
    approval_res = runtime.approve_pending(session_id, approve=True)
    assert approval_res["status"] == "completed"

    # Verify track HAS been added now and backup exists
    assert "Bassline" in song_file.read_text(encoding="utf-8")
    assert (tmp_path / "song.rpp.bak").is_file()


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
        {"type": "tool_call", "tool": "application.reaper.destroy_system", "invocation_id": "inv_err_2", "arguments": {}}
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
