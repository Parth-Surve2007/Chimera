"""Regression coverage for next-generation desktop authorization and drawing."""

import os
import sys
import time
from types import SimpleNamespace

import pytest

from chimera.agent_runtime import AgentRuntime
from chimera.computer_runtime import ComputerObservation, ComputerSessionManager
from chimera.local_model import LocalModelConfig, LocalModelProvider


@pytest.mark.skipif(os.name != "nt", reason="Windows path alias behavior is platform-specific")
def test_packaged_app_alias_binds_only_the_launch_observed_window(tmp_path, monkeypatch):
    actual = r"C:\Program Files\WindowsApps\Microsoft.Paint_1.0.0.0_x64__8wekyb3d8bbwe\ApplicationFrameHost.exe"
    alias = r"C:\Users\Tester\AppData\Local\Microsoft\WindowsApps\mspaint.exe"

    class FakeProcess:
        def create_time(self):
            return 1234.5

        def exe(self):
            return actual

    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=lambda _pid: FakeProcess()))
    manager = ComputerSessionManager(tmp_path)
    manager.backend.window_process_id = lambda hwnd: 4321 if hwnd == 77 else 9999

    identity = manager.create(
        4321,
        alias,
        owner_session_id="conversation-1",
        authorized_windows=[{"hwnd": 77, "pid": 4321, "title": "Untitled - Paint"}],
        expected_application_name="Paint",
    )

    assert identity.authorized_hwnds == (77,)
    observation = ComputerObservation(
        "observation-1", identity.session_id, 1, time.time(), "uia",
        {"pid": 4321, "executable": actual},
        [{"hwnd": 77, "pid": 4321, "title": "Untitled - Paint"}],
        [],
    )
    manager.observations[identity.session_id] = (1, observation)

    assert manager.validate_window(identity.session_id, 77) is identity
    with pytest.raises(ValueError, match="launch-approved HWND set"):
        manager.validate_window(identity.session_id, 78)


def test_agent_runtime_validates_bounded_multi_stroke_canvas_actions():
    valid = {
        "session_id": "paint-session",
        "reference_id": "fresh-canvas-reference",
        "action": "multi_stroke",
        "strokes": [
            [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]],
            [[0.2, 0.5], [0.8, 0.5]],
        ],
        "duration_ms": 300,
    }
    AgentRuntime._validate_arguments("computer.visual_action", valid)

    invalid = dict(valid, strokes=[[[0.1, 0.1], [0.9, 0.9]]] * 17)
    with pytest.raises(ValueError, match="16 paths"):
        AgentRuntime._validate_arguments("computer.visual_action", invalid)


def test_local_model_prompt_explains_safe_launch_continuation_and_canvas_strokes():
    provider = LocalModelProvider(LocalModelConfig(None, None, context=8192, max_tokens=256))
    captured = {}

    def fake_structured(prompt, **kwargs):
        captured["prompt"] = prompt
        return {"type": "message", "content": "Ready."}, {"context": 8192}

    provider.structured_with_metrics = fake_structured
    provider.decide(
        [SimpleNamespace(role="user", content="Open Paint and draw a house.")],
        {"tools": [
            {"id": "computer.launch", "description": "Launch an app",
             "arguments": {"executable": "string"}, "permission": "computer.launch"},
            {"id": "computer.inspect_elements", "description": "Inspect UIA elements",
             "arguments": {"session_id": "string"}, "permission": "computer.observe"},
            {"id": "computer.visual_action", "description": "Bounded multi-stroke drawing",
             "arguments": {"session_id": "string", "reference_id": "string",
                           "action": "string", "strokes": "array"},
             "permission": "computer.input"},
        ], "authorized_applications": []},
        session={"mode": "build", "workspace": None},
    )

    prompt = captured["prompt"]
    assert "control_authorized=true" in prompt
    assert "Otherwise stop and report the authorization blocker" in prompt
    assert "multi_stroke" in prompt
    assert "<=256 total" in prompt
    assert "verified=true" in prompt


@pytest.mark.skipif(os.name != "nt", reason="Windows path alias behavior is platform-specific")
def test_packaged_app_alias_rejects_unrelated_window_title(tmp_path, monkeypatch):
    actual = r"C:\Program Files\WindowsApps\Microsoft.Paint_1.0.0.0_x64__8wekyb3d8bbwe\ApplicationFrameHost.exe"
    alias = r"C:\Users\Tester\AppData\Local\Microsoft\WindowsApps\mspaint.exe"

    class FakeProcess:
        def create_time(self):
            return 1234.5

        def exe(self):
            return actual

    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=lambda _pid: FakeProcess()))
    manager = ComputerSessionManager(tmp_path)
    manager.backend.window_process_id = lambda hwnd: 4321 if hwnd == 77 else None

    with pytest.raises(RuntimeError, match="executable changed"):
        manager.create(
            4321,
            alias,
            owner_session_id="conversation-1",
            authorized_windows=[{"hwnd": 77, "pid": 4321, "title": "Untitled - Notepad"}],
            expected_application_name="Paint",
        )


def test_interaction_strategy_routes_semantic_and_visual_actions_by_evidence(tmp_path):
    manager = ComputerSessionManager(tmp_path)
    manager.uia = SimpleNamespace()
    manager.visual_input = SimpleNamespace(native=object())

    semantic = manager.choose_interaction(
        "read_text",
        "native_windows",
        evidence={"fresh_window": True, "geometry": True, "semantic_control": True},
    )
    visual = manager.choose_interaction(
        "visual_action",
        "native_windows",
        evidence={"fresh_window": True, "geometry": True},
    )

    assert semantic["selected"] == "uia"
    assert visual["selected"] == "grounded_input"

    manager.uia = None
    unavailable = manager.choose_interaction(
        "visual_action",
        "native_windows",
        evidence={"fresh_window": True, "geometry": True},
    )
    assert unavailable["selected"] is None


def test_ui_automation_observation_targets_exact_authorized_window_and_bounds_tree(tmp_path):
    from chimera.computer_runtime import ApplicationIdentity, ComputerSessionManager

    manager = ComputerSessionManager(tmp_path)
    session_id = "multi-window-conversation"
    computer_session_id = "multi-window-app"
    pid = 4321
    identity = ApplicationIdentity(
        pid, "C:/Apps/editor.exe", 123.0, session_id,
        computer_session_id, (77, 88),
    )
    manager.sessions[computer_session_id] = identity
    manager._identity_is_current = lambda current: current is identity
    manager.backend.window_process_id = lambda hwnd: pid if hwnd in {77, 88} else None
    manager.backend.list_applications = lambda: [{
        "pid": pid,
        "executable": identity.executable,
        "windows": [
            {"hwnd": 77, "pid": pid, "title": "Project A"},
            {"hwnd": 88, "pid": pid, "title": "Project B"},
        ],
    }]

    class FakeUIA:
        def __init__(self):
            self.calls = []

        def elements(self, hwnd, depth=3, limit=250):
            self.calls.append((hwnd, depth, limit))
            return [{
                "runtime_id": f"element-{hwnd}",
                "parent": hwnd,
                "process_id": pid,
                "name": f"Document {hwnd}",
                "control_type": "Document",
                "rectangle": {"left": 0, "top": 0, "right": 300, "bottom": 200},
                "enabled": True,
                "offscreen": False,
                "patterns": ["Text"],
            }]

    fake_uia = FakeUIA()
    manager.uia = fake_uia

    observation = manager.observe(
        computer_session_id, hwnd=88, depth=5, limit=310,
    )

    assert fake_uia.calls == [(88, 5, 310)]
    assert [window["hwnd"] for window in observation.windows] == [88]
    assert observation.elements[0]["name"] == "Document 88"
    assert observation.elements[0]["session_id"] == computer_session_id
    assert len(observation.elements[0]["reference_id"]) == 32

    try:
        manager.observe(computer_session_id, hwnd=99)
    except PermissionError as exc:
        assert "authorized process" in str(exc)
    else:
        raise AssertionError("a foreign HWND was accepted")

    try:
        manager.observe(computer_session_id, hwnd=88, depth=9)
    except ValueError as exc:
        assert "depth" in str(exc)
    else:
        raise AssertionError("unbounded UIA depth was accepted")
