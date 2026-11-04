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

    out_of_bounds = dict(valid, strokes=[[[0.1, 0.1], [1.0, 0.9]]])
    with pytest.raises(ValueError, match="normalized"):
        AgentRuntime._validate_arguments("computer.visual_action", out_of_bounds)

    boolean_coordinate = dict(valid, strokes=[[[True, 0.1], [0.9, 0.9]]])
    with pytest.raises(ValueError, match="normalized"):
        AgentRuntime._validate_arguments("computer.visual_action", boolean_coordinate)

    non_integer_duration = dict(valid, duration_ms=True)
    with pytest.raises(ValueError, match="duration_ms must be an integer"):
        AgentRuntime._validate_arguments("computer.visual_action", non_integer_duration)

    mismatched_text = {
        "session_id": "paint-session", "reference_id": "fresh-canvas-reference",
        "action": "click", "points": [[0.5, 0.5]], "text": "unexpected",
    }
    with pytest.raises(ValueError, match="text is only valid"):
        AgentRuntime._validate_arguments("computer.visual_action", mismatched_text)

    mismatched_scroll = {
        "session_id": "paint-session", "reference_id": "fresh-canvas-reference",
        "action": "stroke", "points": [[0.1, 0.1], [0.9, 0.9]], "direction": "down",
    }
    with pytest.raises(ValueError, match="direction and steps"):
        AgentRuntime._validate_arguments("computer.visual_action", mismatched_scroll)


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
    assert "one pen-up path per disconnected shape/component" in prompt
    assert "never repeat the same stroke blindly" in prompt


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


def test_empty_uia_tree_uses_honest_read_only_win32_fallback(tmp_path):
    from chimera.computer_runtime import ApplicationIdentity, ComputerSessionManager

    manager = ComputerSessionManager(tmp_path)
    identity = ApplicationIdentity(
        4321, "C:/Apps/editor.exe", 123.0, "conversation", "app-session", (77,),
    )
    manager.sessions["app-session"] = identity
    manager._identity_is_current = lambda current: current is identity
    manager.backend.window_process_id = lambda hwnd: 4321 if hwnd == 77 else None
    manager.backend.list_applications = lambda: [{
        "pid": 4321,
        "executable": identity.executable,
        "windows": [{"hwnd": 77, "pid": 4321, "title": "Editor"}],
    }]
    manager.backend.controls = lambda hwnd: [{
        "hwnd": 91, "parent_hwnd": hwnd, "class_name": "Button",
        "text": "Open", "control_id": 100,
    }]

    class EmptyUIA:
        def elements(self, hwnd, depth=3, limit=250):
            return []

    manager.uia = EmptyUIA()
    observation = manager.observe("app-session")

    assert observation.backend == "win32"
    assert observation.limitation is not None
    assert observation.elements[0]["text"] == "Open"
    assert "reference_id" not in observation.elements[0]


def test_uia_provider_failure_falls_back_for_one_observation_only(tmp_path):
    from chimera.computer_runtime import ApplicationIdentity, ComputerSessionManager

    manager = ComputerSessionManager(tmp_path)
    identity = ApplicationIdentity(
        4321, "C:/Apps/editor.exe", 123.0, "conversation", "app-session", (77,),
    )
    manager.sessions["app-session"] = identity
    manager._identity_is_current = lambda current: current is identity
    manager.backend.window_process_id = lambda hwnd: 4321 if hwnd == 77 else None
    manager.backend.list_applications = lambda: [{
        "pid": 4321,
        "executable": identity.executable,
        "windows": [{"hwnd": 77, "pid": 4321, "title": "Editor"}],
    }]
    manager.backend.controls = lambda hwnd: [{
        "hwnd": 91, "parent_hwnd": hwnd, "class_name": "Button",
        "text": "Open", "control_id": 100,
    }]

    class FlakyUIA:
        def __init__(self):
            self.calls = 0

        def elements(self, hwnd, depth=3, limit=250):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("UIA provider timed out for this application")
            return [{
                "runtime_id": "editor-root",
                "parent": hwnd,
                "process_id": 4321,
                "name": "Editor surface",
                "control_type": "Document",
                "rectangle": {"left": 0, "top": 0, "right": 500, "bottom": 400},
                "enabled": True,
                "offscreen": False,
                "patterns": ["Text"],
            }]

    provider = FlakyUIA()
    manager.uia = provider
    first = manager.observe("app-session")
    assert first.backend == "win32"
    assert "UIA error" in first.limitation
    assert manager.uia is provider

    second = manager.observe("app-session")
    assert second.backend == "uia"
    assert second.elements[0]["name"] == "Editor surface"
    assert provider.calls == 2


def test_desktop_application_profiles_are_advisory_and_match_live_identity():
    from chimera.computer_runtime import DesktopApplicationProfileRegistry

    profiles = DesktopApplicationProfileRegistry()
    paint = profiles.match(
        r"C:\Program Files\WindowsApps\ApplicationFrameHost.exe",
        [{"hwnd": 77, "pid": 4321, "title": "Untitled - Paint", "class_name": "ApplicationFrameWindow"}],
    )
    word = profiles.match(
        r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",
        [{"hwnd": 88, "pid": 4321, "title": "Report.docx - Word"}],
    )
    excel = profiles.match(
        r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE",
        [{"hwnd": 99, "pid": 4321, "title": "Budget.xlsx - Excel"}],
    )
    notepad = profiles.match(
        r"C:\Windows\System32\notepad.exe",
        [{"hwnd": 100, "pid": 4321, "title": "notes.txt - Notepad"}],
    )

    assert paint["profile_id"] == "windows.paint"
    assert any("largest visible enabled" in hint for hint in paint["target_hints"])
    assert word["profile_id"] == "office.word"
    assert excel["profile_id"] == "office.excel"
    assert notepad["profile_id"] == "windows.notepad"
    for profile in (paint, word, excel, notepad):
        assert profile["advisory_only"] is True
        assert profile["permissions_granted"] == []
        assert "permissions" not in profile


def test_custom_desktop_profiles_discard_permission_and_coordinate_fields():
    from chimera.computer_runtime import DesktopApplicationProfileRegistry

    registry = DesktopApplicationProfileRegistry([{
        "profile_id": "custom-editor",
        "version": 1,
        "display_name": "Custom Editor",
        "match": {"executable": "editor.exe"},
        "target_hints": ["Prefer the visible document editor surface."],
        "preferred_operations": ["read_text"],
        "permissions": ["computer.input", "workspace.write"],
        "coordinates": [[0.1, 0.2], [0.9, 0.8]],
    }])
    profile = registry.match("editor.exe", [{"hwnd": 77, "pid": 4321, "title": "Editor"}])

    assert profile["profile_id"] == "custom-editor"
    assert profile["permissions_granted"] == []
    assert "coordinates" not in profile
    assert "permissions" not in profile


def test_profile_store_persists_sanitized_custom_profiles_atomically(tmp_path):
    import json
    from chimera.computer_runtime import ProfileStore, DesktopApplicationProfileRegistry

    store = ProfileStore(tmp_path / "application_profiles.json")
    profiles = [{
        "profile_id": "custom.editor",
        "version": 1,
        "display_name": "Custom Editor",
        "match": {"executable": [r"C:\\Tools\\editor.exe"], "title_contains": ["Custom Editor"]},
        "target_hints": ["Prefer the main editor document surface."],
        "preferred_operations": ["inspect_elements", "read_text"],
        "limitations": ["Some extension panels are custom-rendered."],
        "permissions": ["computer.input"],
        "coordinates": [[0.1, 0.2], [0.8, 0.9]],
    }]

    saved_path = store.save(profiles)
    loaded = store.load()

    assert saved_path.is_file()
    assert loaded[0]["profile_id"] == "custom.editor"
    assert "permissions" not in loaded[0]
    assert "coordinates" not in loaded[0]
    assert json.loads(saved_path.read_text(encoding="utf-8")) == loaded

    registry = DesktopApplicationProfileRegistry(loaded)
    matched = registry.match(
        r"C:\\Tools\\editor.exe",
        [{"hwnd": 77, "pid": 4321, "title": "Project - Custom Editor"}],
    )
    assert matched["profile_id"] == "custom.editor"
    assert matched["permissions_granted"] == []


def test_profile_store_rejects_unsafe_or_ambiguous_profile_schema(tmp_path):
    from chimera.computer_runtime import ProfileStore

    store = ProfileStore(tmp_path / "application_profiles.json")
    with pytest.raises(ValueError, match="version must be an integer"):
        store.save([{
            "profile_id": "bad.version",
            "version": True,
            "match": {"executable": "editor.exe"},
        }])
    with pytest.raises(ValueError, match="match must use"):
        store.save([{
            "profile_id": "bad.matcher",
            "version": 1,
            "match": {"executable": "editor.exe", "command": "run.exe"},
        }])
    with pytest.raises(ValueError, match="duplicate"):
        store.save([
            {"profile_id": "duplicate", "version": 1, "match": {"executable": "a.exe"}},
            {"profile_id": "duplicate", "version": 1, "match": {"executable": "b.exe"}},
        ])


def test_profile_registry_normalizes_windows_executable_paths_on_non_windows_hosts():
    from chimera.computer_runtime import DesktopApplicationProfileRegistry

    registry = DesktopApplicationProfileRegistry()
    matched = registry.match(
        r"C:\\Program Files\\Microsoft Office\\root\\Office16\\WINWORD.EXE",
        [],
    )
    assert matched is not None
    assert matched["profile_id"] == "office.word"


def test_computer_session_manager_loads_operator_profiles_from_configured_store(tmp_path):
    from chimera.computer_runtime import ComputerSessionManager, ProfileStore

    profile_path = tmp_path / "application_profiles.json"
    ProfileStore(profile_path).save([{
        "profile_id": "custom.paintlike",
        "version": 1,
        "display_name": "Paintlike App",
        "match": {"executable": "paintlike.exe"},
        "target_hints": ["Find the main custom surface."],
    }])

    manager = ComputerSessionManager(profile_store_path=profile_path)
    matched = manager.application_profiles.match("C:/Apps/paintlike.exe", [])
    assert matched["profile_id"] == "custom.paintlike"
    assert manager.profile_store_error is None


def test_custom_profile_full_executable_path_is_normalized_to_basename(tmp_path):
    from chimera.computer_runtime import DesktopApplicationProfileRegistry

    registry = DesktopApplicationProfileRegistry([{
        "profile_id": "custom.fullpath",
        "version": 1,
        "display_name": "Full Path Match",
        "match": {"executable": r"C:\\Program Files\\Studio\\editor.exe"},
        "target_hints": ["Prefer the editor document."],
    }])
    matched = registry.match("C:/Program Files/Studio/editor.exe", [])
    assert matched["profile_id"] == "custom.fullpath"


def test_local_model_prompt_requires_file_evidence_after_canvas_save(tmp_path):
    provider = LocalModelProvider(
        LocalModelConfig(None, None, context=8192, max_tokens=256)
    )
    captured = {}

    def fake_structured(prompt, **kwargs):
        captured["prompt"] = prompt
        return {"type": "message", "content": "I will verify the saved file."}, {"context": 8192}

    provider.structured_with_metrics = fake_structured
    provider.decide(
        [SimpleNamespace(role="user", content="Draw and save a PNG in this workspace.")],
        {"tools": [
            {"id": "computer.visual_action", "description": "Scoped canvas drawing",
             "arguments": {"session_id": "string", "reference_id": "string",
                           "action": "string", "strokes": "array"},
             "permission": "computer.input"},
            {"id": "workspace.inspect_file", "description": "Inspect exact file metadata",
             "arguments": {"path": "string"}, "permission": "workspace.read"},
        ], "authorized_applications": []},
        session={"mode": "build", "workspace": str(tmp_path)},
    )

    prompt = captured["prompt"]
    assert "workspace.inspect_file" in prompt
    assert "A dismissed Save dialog is not proof of success" in prompt
    assert "nonzero size" in prompt
    assert "Reopen the saved image" in prompt
