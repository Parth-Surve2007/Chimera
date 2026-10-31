from chimera.computer_use import WindowsComputerUse
from chimera.computer_runtime import ComputerBackendSelector, ComputerSessionManager, ProfileStore
from chimera.tools import tool_catalog


def test_computer_tools_are_registered_and_observation_is_read_only(tmp_path):
    catalog = tool_catalog()["operations"]
    assert "computer.list_applications" in catalog
    result = WindowsComputerUse(tmp_path).list_applications()
    assert isinstance(result, list)


def test_application_launch_requires_allowlist_and_approval(tmp_path):
    computer = WindowsComputerUse(tmp_path)
    assert computer.launch("powershell.exe")["status"] == "blocked"
    assert computer.launch(r"C:\Windows\System32\notepad.exe")["status"] == "awaiting_approval"


def test_backend_selection_reports_uia_limit_and_win32_fallback():
    choices = ComputerBackendSelector().choices()
    assert any(item.name == "win32" and item.available for item in choices)
    assert any(item.name == "uia" for item in choices)


def test_profiles_are_validated_and_do_not_imply_permission(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")
    profiles = [{"profile_id": "generic-editor", "version": 1, "match": {"executable": "Code.exe"}, "permissions": []}]
    store.save(profiles)
    assert store.load() == profiles


def test_session_observation_requires_fresh_identity(tmp_path):
    manager = ComputerSessionManager(tmp_path)
    session = manager.create(999999, "missing.exe")
    try:
        manager.observe(session.session_id)
    except RuntimeError as exc:
        assert "no longer running" in str(exc)
    else:
        raise AssertionError("missing process must not produce an observation")


def test_semantic_action_requires_fresh_reference_and_approval(tmp_path):
    from chimera.computer_runtime import ComputerSessionManager
    manager = ComputerSessionManager(tmp_path)
    session = manager.create(999999, "missing.exe")
    try:
        manager.act(type("Reference", (), {"session_id": session.session_id, "generation": 1, "expires_at": 0, "hwnd": 1, "runtime_id": "x", "reference_id": "r"})(), "invoke_element")
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
