from chimera.computer_use import WindowsComputerUse
from chimera.computer_runtime import ComputerBackendSelector, ComputerSessionManager, ProfileStore
from chimera.tools import tool_catalog


def test_computer_tools_are_registered_and_observation_is_read_only(tmp_path):
    catalog = tool_catalog()["operations"]
    assert "computer.list_applications" in catalog
    for disabled in ("computer.focus", "computer.focus_window", "computer.type_text", "computer.close", "computer.focus_element"):
        assert disabled not in catalog
    result = WindowsComputerUse(tmp_path).list_applications()
    assert isinstance(result, list)


def test_application_launch_requires_allowlist_and_approval(tmp_path):
    computer = WindowsComputerUse(tmp_path)
    assert computer.launch("powershell.exe")["status"] == "blocked"
    executable = tmp_path / "sample.exe"
    executable.write_bytes(b"MZ")
    assert computer.launch(str(executable))["status"] == "awaiting_approval"


def test_backend_selection_reports_uia_limit_and_win32_fallback():
    choices = ComputerBackendSelector().choices()
    assert any(item.name == "win32" and item.available for item in choices)
    assert any(item.name == "uia" for item in choices)


def test_profiles_are_validated_and_do_not_imply_permission(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")
    profiles = [{"profile_id": "generic-editor", "version": 1, "match": {"executable": "Code.exe"}, "permissions": []}]
    store.save(profiles)
    assert store.load() == profiles


def test_session_creation_rejects_unverifiable_process_identity(tmp_path):
    manager = ComputerSessionManager(tmp_path)
    try:
        manager.create(999999, "missing.exe")
    except RuntimeError as exc:
        assert "identity could not be verified" in str(exc)
    else:
        raise AssertionError("an unknown process must not be authorized")


def test_global_focus_keyboard_and_window_close_are_disabled(tmp_path):
    computer = WindowsComputerUse(tmp_path)
    assert computer.focus(1)["status"] == "blocked"
    assert computer.type_text(1, "must not be injected")["status"] == "blocked"
    assert computer.close(1, approved=True)["status"] == "blocked"


def test_semantic_action_requires_fresh_reference_and_approval(tmp_path):
    from chimera.computer_runtime import ComputerSessionManager
    manager = ComputerSessionManager(tmp_path)
    try:
        manager.create(999999, "missing.exe")
    except RuntimeError:
        return
    raise AssertionError("an unknown process must not be authorized")
    try:
        manager.act(type("Reference", (), {"session_id": "s", "generation": 1, "expires_at": 0, "hwnd": 1, "runtime_id": "x", "reference_id": "r"})(), "invoke_element")
    except ValueError as exc:
        assert "fresh" in str(exc) or "stale" in str(exc)
    else:
        raise AssertionError("stale semantic references must be rejected")


def test_uia_observation_exposes_runtime_generated_references_for_real_window():
    from chimera.computer_runtime import ComputerSessionManager
    manager = ComputerSessionManager()
    apps = manager.backend.list_applications()
    if not apps: return
    identity = manager.create(apps[0]["pid"], apps[0].get("executable"))
    observation = manager.observe(identity.session_id)
    if observation.backend == "uia" and observation.elements:
        element = observation.elements[0]
        assert element["reference_id"]
        assert element["session_id"] == identity.session_id
        assert element["observation_generation"] == observation.generation
        assert isinstance(element["available_actions"], list)



def test_application_launch_resolves_word_name_before_approval(tmp_path, monkeypatch):
    computer = WindowsComputerUse(tmp_path)
    executable = tmp_path / "WINWORD.EXE"
    executable.write_bytes(b"MZ")
    resolved = []

    def resolve(name):
        resolved.append(name)
        return executable if name.casefold() == "word" else None

    monkeypatch.setattr(computer, "_resolve_executable", resolve)
    result = computer.launch("Word")

    assert resolved == ["Word"]
    assert result["status"] == "awaiting_approval"
    assert result["application"] == str(executable)
    assert result["requested_application"] == "Word"


def test_application_launch_blocks_unknown_application_name(tmp_path, monkeypatch):
    computer = WindowsComputerUse(tmp_path)
    monkeypatch.setattr(computer, "_resolve_executable", lambda _name: None)

    result = computer.launch("DefinitelyNotInstalled")

    assert result["status"] == "blocked"
    assert "could not resolve" in result["reason"]



def test_word_alias_resolves_to_registered_app_path_before_launch(tmp_path, monkeypatch):
    from chimera import computer_use

    executable = tmp_path / "WINWORD.EXE"
    executable.write_bytes(b"MZ")
    resolved_names = []

    def fake_which(name):
        resolved_names.append(name)
        return str(executable) if name == "WINWORD.EXE" else None

    monkeypatch.setattr(computer_use.shutil, "which", fake_which)
    result = WindowsComputerUse._resolve_executable("Word")

    assert resolved_names[0] == "WINWORD.EXE"
    assert result == executable.resolve()



def test_application_launch_detects_existing_process_handoff(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import subprocess
    from chimera.computer_use import WindowObservation

    computer = WindowsComputerUse(tmp_path)
    executable = tmp_path / "notepad.exe"
    executable.write_bytes(b"MZ")
    monkeypatch.setattr(computer, "_resolve_executable", lambda _name: executable)
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda _args, shell=False: SimpleNamespace(pid=111),
    )
    monkeypatch.setattr(computer, "_windows", lambda: [
        WindowObservation(77, 222, "Untitled - Notepad", "Notepad", True)
    ])
    monkeypatch.setattr(computer, "process_path", lambda pid: str(executable) if pid == 222 else None)

    result = computer.launch("Notepad", approved=True)

    assert result["status"] == "completed"
    assert result["pid"] == 222
    assert result["spawned_pid"] == 111
    assert result["verification"] == "visible_window_executable_match"
    assert result["windows"][0]["title"] == "Untitled - Notepad"


def test_application_launch_matches_packaged_app_image_name(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import subprocess
    from chimera.computer_use import WindowObservation

    computer = WindowsComputerUse(tmp_path)
    executable = tmp_path / "notepad.exe"
    executable.write_bytes(b"MZ")
    monkeypatch.setattr(computer, "_resolve_executable", lambda _name: executable)
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda _args, shell=False: SimpleNamespace(pid=111),
    )
    packaged_image = r"C:\Program Files\WindowsApps\Microsoft.WindowsNotepad\Notepad.exe"
    monkeypatch.setattr(computer, "_windows", lambda: [
        WindowObservation(77, 222, "Untitled - Notepad", "Notepad", True)
    ])
    monkeypatch.setattr(computer, "process_path", lambda pid: packaged_image if pid == 222 else None)

    result = computer.launch("Notepad", approved=True)

    assert result["status"] == "completed"
    assert result["pid"] == 222
    assert result["spawned_pid"] == 111



def _seed_uia_reference(manager, monkeypatch, session_id="agent-session"):
    import time
    from chimera.computer_runtime import (
        ApplicationIdentity, ComputerObservation, ElementReference,
    )
    identity = ApplicationIdentity(
        pid=4321, executable=r"C:\\Program Files\\Example\\app.exe",
        process_create_time=1.0, owner_session_id="conversation-1",
        session_id=session_id,
    )
    reference = ElementReference(
        reference_id="element-1", session_id=session_id, hwnd=99,
        runtime_id="42", generation=3, expires_at=time.time() + 60,
    )
    observation = ComputerObservation(
        observation_id="observation-1", session_id=session_id, generation=3,
        created_at=time.time(), backend="uia", application={},
        windows=[{"hwnd": 99}], elements=[],
    )
    manager.sessions[session_id] = identity
    manager.observations[session_id] = (3, observation)
    manager.references[reference.reference_id] = reference
    monkeypatch.setattr(manager, "_identity_is_current", lambda _identity: True)
    monkeypatch.setattr(manager.backend, "window_process_id", lambda _hwnd: 4321)
    return reference


class _FakeUIA:
    def __init__(self):
        self.calls = []

    def action(self, hwnd, runtime_id, operation, value=None, expected_pid=None, options=None):
        self.calls.append({
            "hwnd": hwnd, "runtime_id": runtime_id, "operation": operation,
            "value": value, "expected_pid": expected_pid, "options": options,
        })
        return {"status": "completed", "verified": True, "backend": "uia", "value": value}


def test_uia_action_bridge_executes_read_only_actions_without_approval(tmp_path, monkeypatch):
    manager = ComputerSessionManager(tmp_path)
    reference = _seed_uia_reference(manager, monkeypatch)
    fake = _FakeUIA()
    manager.uia = fake

    result = manager.act_reference("agent-session", reference.reference_id, "read_text")

    assert result["status"] == "completed"
    assert result["verified"] is True
    assert fake.calls == [{
        "hwnd": 99, "runtime_id": "42", "operation": "read_text",
        "value": None, "expected_pid": 4321, "options": {},
    }]


def test_uia_action_bridge_requires_approval_for_state_changes(tmp_path, monkeypatch):
    manager = ComputerSessionManager(tmp_path)
    reference = _seed_uia_reference(manager, monkeypatch)
    fake = _FakeUIA()
    manager.uia = fake

    pending = manager.act_reference(
        "agent-session", reference.reference_id, "set_value", "ready", approved=False,
    )
    assert pending["status"] == "awaiting_approval"
    assert fake.calls == []

    completed = manager.act_reference(
        "agent-session", reference.reference_id, "set_value", "ready", approved=True,
    )
    assert completed["status"] == "completed"
    assert fake.calls[0]["operation"] == "set_value"
    assert fake.calls[0]["value"] == "ready"


def test_uia_action_bridge_never_uses_win32_mutation_when_uia_is_missing(tmp_path, monkeypatch):
    manager = ComputerSessionManager(tmp_path)
    reference = _seed_uia_reference(manager, monkeypatch)
    manager.uia = None

    result = manager.act_reference(
        "agent-session", reference.reference_id, "set_value", "unsafe", approved=True,
    )

    assert result["status"] == "blocked"
    assert "no input or Win32 mutation fallback" in result["reason"]


def test_uia_action_bridge_rejects_stale_references_before_execution(tmp_path, monkeypatch):
    import time
    manager = ComputerSessionManager(tmp_path)
    reference = _seed_uia_reference(manager, monkeypatch)
    fake = _FakeUIA()
    manager.uia = fake
    manager.observations["agent-session"] = (4, manager.observations["agent-session"][1])

    result = manager.act_reference(
        "agent-session", reference.reference_id, "invoke_element", approved=True,
    )

    assert result["status"] == "blocked"
    assert "stale or expired" in result["reason"]
    assert fake.calls == []


def test_available_actions_reflects_range_grid_and_scroll_patterns():
    element = {"patterns": ["RangeValue", "Grid", "Scroll", "ScrollItem", "Text"]}
    actions = ComputerSessionManager._available_actions(element)
    assert {"read_text", "get_range_value", "set_range_value", "read_grid", "scroll_element", "scroll_into_view"} <= set(actions)


def test_uia_range_value_is_bounded_and_verified():
    from types import SimpleNamespace
    from chimera.computer_runtime import WindowsUIABackend

    class RangePattern:
        CurrentValue = 4.0
        CurrentMinimum = 0.0
        CurrentMaximum = 10.0
        CurrentSmallChange = 0.5
        CurrentLargeChange = 2.0
        CurrentIsReadOnly = False

        def SetValue(self, value):
            self.CurrentValue = value

    class Item:
        element_info = SimpleNamespace(process_id=4321)
        iface_range_value = RangePattern()

    backend = object.__new__(WindowsUIABackend)
    backend._find = lambda _hwnd, _runtime_id: Item()
    backend._info = lambda _item, _hwnd: {"name": "Volume"}
    result = backend.action(99, "42", "set_range_value", 7.5, expected_pid=4321)

    assert result["status"] == "completed"
    assert result["verified"] is True
    assert result["value"] == 7.5
    assert result["minimum"] == 0.0 and result["maximum"] == 10.0


def test_uia_range_value_rejects_values_outside_control_bounds():
    from types import SimpleNamespace
    from chimera.computer_runtime import WindowsUIABackend

    class RangePattern:
        CurrentValue = 4.0
        CurrentMinimum = 0.0
        CurrentMaximum = 10.0
        CurrentSmallChange = 1.0
        CurrentLargeChange = 2.0
        CurrentIsReadOnly = False

        def SetValue(self, value):
            raise AssertionError("out-of-range values must not reach SetValue")

    class Item:
        element_info = SimpleNamespace(process_id=4321)
        iface_range_value = RangePattern()

    backend = object.__new__(WindowsUIABackend)
    backend._find = lambda _hwnd, _runtime_id: Item()
    backend._info = lambda _item, _hwnd: {"name": "Volume"}
    result = backend.action(99, "42", "set_range_value", 11.0, expected_pid=4321)

    assert result["status"] == "blocked"
    assert "between 0.0 and 10.0" in result["reason"]


def test_uia_scroll_requires_known_direction_and_bounded_count():
    from types import SimpleNamespace
    from chimera.computer_runtime import WindowsUIABackend

    class ScrollPattern:
        CurrentVerticallyScrollable = True
        CurrentHorizontallyScrollable = True
        CurrentVerticalScrollPercent = 20.0
        CurrentHorizontalScrollPercent = 0.0

    class Item:
        element_info = SimpleNamespace(process_id=4321)
        iface_scroll = ScrollPattern()

        def scroll(self, direction, amount, count=1, retry_interval=0):
            assert direction == "down"
            assert amount == "page"
            self.iface_scroll.CurrentVerticalScrollPercent = 60.0

    item = Item()
    backend = object.__new__(WindowsUIABackend)
    backend._find = lambda _hwnd, _runtime_id: item
    backend._info = lambda _item, _hwnd: {"name": "Results"}
    result = backend.action(
        99, "42", "scroll", expected_pid=4321,
        options={"direction": "down", "amount": "page", "count": 1},
    )

    assert result["status"] == "completed"
    assert result["verified"] is True
    assert result["scroll_before"]["vertical"] == 20.0
    assert result["scroll_after"]["vertical"] == 60.0


def test_uia_text_pattern_is_used_before_simple_window_caption():
    from types import SimpleNamespace
    from chimera.computer_runtime import WindowsUIABackend

    class TextRange:
        def GetText(self, max_length):
            assert max_length == 12000
            return "Accessible document content"

    class Item:
        element_info = SimpleNamespace(process_id=4321)
        iface_text = SimpleNamespace(DocumentRange=TextRange())

        def window_text(self):
            return "Document title"

    item = Item()
    backend = object.__new__(WindowsUIABackend)
    backend._find = lambda _hwnd, _runtime_id: item
    backend._info = lambda _item, _hwnd: {"name": "Document"}
    result = backend.action(99, "42", "read_text", expected_pid=4321)

    assert result["status"] == "completed"
    assert result["value"] == "Accessible document content"
    assert result["source"] == "TextPattern"


def test_grid_pattern_returns_bounded_cells_and_dimensions():
    from types import SimpleNamespace
    from chimera.computer_runtime import WindowsUIABackend

    class GridPattern:
        CurrentRowCount = 2
        CurrentColumnCount = 2

        def GetItem(self, row, column):
            return SimpleNamespace(CurrentName=f"r{row}c{column}")

    class Item:
        element_info = SimpleNamespace(process_id=4321)
        iface_grid = GridPattern()

    item = Item()
    backend = object.__new__(WindowsUIABackend)
    backend._find = lambda _hwnd, _runtime_id: item
    backend._info = lambda _item, _hwnd: {"name": "Spreadsheet grid"}
    result = backend.action(99, "42", "read_grid", expected_pid=4321)

    assert result["status"] == "completed"
    assert result["rows"] == [["r0c0", "r0c1"], ["r1c0", "r1c1"]]
    assert result["row_count"] == 2 and result["column_count"] == 2


def test_computer_tool_schemas_and_argument_validation_cover_new_uia_patterns():
    from chimera.agent_runtime import AgentRuntime
    catalog = tool_catalog()["operations"]
    assert {
        "computer.get_range_value", "computer.set_range_value", "computer.read_grid",
        "computer.scroll_element", "computer.scroll_into_view",
    } <= set(catalog)

    AgentRuntime._validate_arguments(
        "computer.set_range_value",
        {"session_id": "s", "reference_id": "r", "value": 4.5},
    )
    AgentRuntime._validate_arguments(
        "computer.scroll_element",
        {"session_id": "s", "reference_id": "r", "direction": "down", "amount": "page", "count": 2},
    )


def test_packaged_paint_resolves_from_start_menu_when_executable_is_not_on_disk(monkeypatch):
    from chimera import computer_use

    aumid = "Microsoft.Paint_8wekyb3d8bbwe!App"
    monkeypatch.setattr(computer_use, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        computer_use.ctypes,
        "windll",
        SimpleNamespace(user32=object(), kernel32=object()),
        raising=False,
    )
    monkeypatch.setattr(
        WindowsComputerUse,
        "_resolve_executable",
        staticmethod(lambda _name: None),
    )
    monkeypatch.setattr(
        WindowsComputerUse,
        "_start_menu_apps",
        staticmethod(lambda: [{"name": "Paint", "app_id": aumid}]),
    )

    target = WindowsComputerUse._resolve_launch_target("mspaint.exe")

    assert target is not None
    assert target["kind"] == "appsfolder"
    assert target["aumid"] == aumid
    assert target["application"] == "appsfolder:" + aumid
    assert target["display_name"] == "Paint"


def test_packaged_paint_preflight_returns_exact_aumid_for_approval(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from chimera import computer_use

    aumid = "Microsoft.Paint_8wekyb3d8bbwe!App"
    monkeypatch.setattr(computer_use, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        computer_use.ctypes,
        "windll",
        SimpleNamespace(user32=object(), kernel32=object()),
        raising=False,
    )
    monkeypatch.setattr(
        WindowsComputerUse,
        "_resolve_executable",
        staticmethod(lambda _name: None),
    )
    monkeypatch.setattr(
        WindowsComputerUse,
        "_start_menu_apps",
        staticmethod(lambda: [{"name": "Paint", "app_id": aumid}]),
    )

    def unexpected_launch(*_args, **_kwargs):
        raise AssertionError("preflight must not launch before approval")

    monkeypatch.setattr(computer_use.subprocess, "Popen", unexpected_launch)
    result = WindowsComputerUse(tmp_path).launch("Paint", approved=False)

    assert result["status"] == "awaiting_approval"
    assert result["launch_kind"] == "appsfolder"
    assert result["application"] == "appsfolder:" + aumid
    assert result["display_name"] == "Paint"
    assert result["permission"] == "computer.launch"


def test_packaged_paint_launch_uses_apps_folder_activation_without_shell(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import subprocess
    from chimera import computer_use
    from chimera.computer_use import WindowObservation

    aumid = "Microsoft.Paint_8wekyb3d8bbwe!App"
    explorer = tmp_path / "explorer.exe"
    explorer.write_bytes(b"MZ")
    monkeypatch.setattr(computer_use, "_is_windows_platform", lambda: True)
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    monkeypatch.setattr(
        WindowsComputerUse,
        "_resolve_executable",
        staticmethod(lambda _name: None),
    )
    monkeypatch.setattr(
        WindowsComputerUse,
        "_start_menu_apps",
        staticmethod(lambda: [{"name": "Paint", "app_id": aumid}]),
    )
    commands = []

    def fake_popen(command, shell=False):
        commands.append((command, shell))
        return SimpleNamespace(pid=111)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    computer = WindowsComputerUse(tmp_path)
    monkeypatch.setattr(
        computer,
        "_windows",
        lambda: [WindowObservation(77, 222, "Untitled - Paint", "ApplicationFrameWindow", True)],
    )
    monkeypatch.setattr(computer, "process_path", lambda _pid: None)

    result = computer.launch("Paint", approved=True)

    assert result["status"] == "completed"
    assert result["pid"] == 222
    assert result["spawned_pid"] == 111
    assert result["launch_kind"] == "appsfolder"
    assert result["verification"] == "visible_window_title_match"
    assert commands == [([str(explorer), "shell:AppsFolder\\" + aumid], False)]


def test_start_menu_inventory_decodes_bom_and_filters_invalid_application_ids(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    import subprocess
    from chimera import computer_use

    system_root = tmp_path
    powershell = (
        system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    powershell.parent.mkdir(parents=True)
    powershell.write_bytes(b"MZ")
    monkeypatch.setattr(computer_use, "_is_windows_platform", lambda: True)
    monkeypatch.setenv("SystemRoot", str(system_root))
    monkeypatch.setattr(computer_use.shutil, "which", lambda _name: None)
    payload = [
        {"Name": "Paint", "AppID": "Microsoft.Paint_8wekyb3d8bbwe!App"},
        {"Name": "Malformed", "AppID": "C:\\\\unsafe\\\\path.exe"},
        {"Name": "Missing ID", "AppID": ""},
    ]
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="\ufeff" + json.dumps(payload),
        ),
    )

    apps = WindowsComputerUse._start_menu_apps()

    assert apps == [{
        "name": "Paint",
        "app_id": "Microsoft.Paint_8wekyb3d8bbwe!App",
    }]


def test_computer_runtime_catalog_exposes_installed_app_inventory():
    from chimera.tools import tool_catalog

    operations = tool_catalog()["operations"]
    assert operations["computer.list_installed_applications"]["arguments"] == {}
    assert operations["computer.list_installed_applications"]["permission"] == "computer.observe"
