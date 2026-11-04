"""Contract tests for the optional background-first Cua Driver adapter."""
from __future__ import annotations

import base64
import pytest
import io
from enum import Enum
from types import SimpleNamespace

from PIL import Image, ImageDraw

from chimera.cua_backend import CuaDriverBackend


class DeliveryMode(Enum):
    BACKGROUND = "background"
    FOREGROUND = "foreground"


class Effect(Enum):
    CONFIRMED = "confirmed"
    REFUSED = "refused"


class Route(Enum):
    ACCESSIBILITY = "accessibility"


class EscalationTarget(Enum):
    FOREGROUND = "foreground"


class EscalationReason(Enum):
    DELIVERY_FAILED = "delivery_failed"


class FakeAPI:
    class ActionTarget:
        @staticmethod
        def WINDOW(*, pid, window_id):
            return {"kind": "window", "pid": pid, "window_id": window_id}

    class ClickPosition:
        @staticmethod
        def ELEMENT(*, element_token):
            return {"kind": "element", "element_token": element_token}

        @staticmethod
        def COORDINATES(*, x, y):
            return {"kind": "coordinates", "x": x, "y": y}

    class InputDeliveryMode:
        BACKGROUND = DeliveryMode.BACKGROUND
        FOREGROUND = DeliveryMode.FOREGROUND

    class ClickButton:
        LEFT = "left"
        RIGHT = "right"
        MIDDLE = "middle"

    class ListAppsInput:
        pass

    class ListWindowsInput:
        def __init__(self, *, pid, on_screen_only):
            self.pid = pid
            self.on_screen_only = on_screen_only

    class GetWindowStateInput:
        def __init__(self, **kwargs):
            self.values = kwargs

    class ClickInput:
        def __init__(self, **kwargs):
            self.values = kwargs

    class DragInput:
        def __init__(self, **kwargs):
            self.values = kwargs


def _png_bytes(draw: bool = False) -> bytes:
    image = Image.new("RGB", (100, 100), "white")
    if draw:
        painter = ImageDraw.Draw(image)
        painter.line((18, 18, 82, 82), fill="black", width=4)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


class FakeToolRefusal(Exception):
    def __init__(self):
        super().__init__("CUA action refused")
        self.kind = "background_unavailable"
        self.structured_content = {
            "error": {
                "kind": "background_unavailable",
                "message": "background delivery is unavailable for this exact target",
            },
            "escalation": {"recommended": "foreground", "reason": "canvas needs foreground"},
        }


class FakeDriver:
    def __init__(self, *, refuse_background=False):
        self.refuse_background = refuse_background
        self.click_inputs = []
        self.drag_inputs = []
        self.shutdown_called = False
        self.snapshot_number = 0
        self.after_draw = False

    async def list_apps(self, _request):
        return SimpleNamespace(apps=[
            SimpleNamespace(pid=4321, name="Paint", running=True, active=True,
                            launch_path="C:/Windows/mspaint.exe", bundle_id=None,
                            kind="desktop", last_used=None),
        ])

    async def list_windows(self, request):
        return SimpleNamespace(windows=[
            SimpleNamespace(
                pid=request.pid, window_id=77, app_name="Paint",
                title="Untitled - Paint",
                bounds=SimpleNamespace(x=0, y=0, width=100, height=100),
                is_on_screen=True, minimized=False,
            ),
        ])

    async def get_window_state(self, request):
        self.snapshot_number += 1
        number = self.snapshot_number
        token = f"s{number:08x}:1"
        element = SimpleNamespace(
            element_index=1,
            element_token=token,
            role="canvas",
            depth=1,
            label="Drawing Canvas",
            value=None,
            value_description=None,
            enabled=True,
            selected=False,
            in_web_content=False,
            actions=["click", "drag"],
            parent_index=0,
            frame=SimpleNamespace(x=210.0, y=110.0, w=80.0, h=80.0),
            min=None,
            max=None,
        )
        screenshot = _png_bytes(self.after_draw) if request.values["include_screenshot"] else None
        images = []
        if screenshot is not None:
            images = [SimpleNamespace(
                mime_type="image/png",
                data_base64=base64.b64encode(screenshot).decode("ascii"),
            )]
        return SimpleNamespace(
            pid=request.values["pid"],
            window_id=request.values["window_id"],
            snapshot_id=f"native-snapshot-{number}",
            app_name="Paint",
            window_title="Untitled - Paint",
            tree_markdown="Window\n  Canvas: Drawing Canvas",
            elements=[element],
            element_count=1,
            total_element_count=1,
            returned_element_count=1,
            filtered_element_count=0,
            elements_complete=True,
            degraded=False,
            degraded_reason=None,
            truncated=False,
            truncation_reason=None,
            truncation_hint=None,
            tree_format="elements",
            since=None,
            since_status=None,
            tree_diff=None,
            screenshot_width=100 if screenshot else None,
            screenshot_height=100 if screenshot else None,
            screenshot_scale=1.0 if screenshot else None,
            screenshot_mime_type="image/png" if screenshot else None,
            screenshot_file_path=None,
            screenshot_frame_valid=bool(screenshot),
            window_bounds=SimpleNamespace(x=200, y=100, width=100, height=100),
            images=images,
        )

    async def click(self, request):
        self.click_inputs.append(request.values)
        if request.values["delivery_mode"] is DeliveryMode.BACKGROUND and self.refuse_background:
            raise FakeToolRefusal()
        return SimpleNamespace(
            effect=Effect.CONFIRMED,
            route=Route.ACCESSIBILITY,
            summary="The exact UIA element was clicked.",
            error=None,
            hint=None,
            escalation=None,
        )

    async def drag(self, request):
        self.drag_inputs.append(request.values)
        self.after_draw = True
        return SimpleNamespace(is_error=False, text="Background drag was delivered.")

    async def shutdown(self):
        self.shutdown_called = True


def _backend(driver):
    return CuaDriverBackend(driver_factory=lambda: driver, api=FakeAPI, timeout_seconds=5)


def test_cua_snapshot_is_bound_to_exact_conversation_process_window_and_token():
    driver = FakeDriver()
    backend = _backend(driver)
    try:
        inspected = backend.inspect_window("conversation-a", 4321, 77)
        assert inspected["status"] == "completed"
        assert inspected["provider"] == "cua_driver"
        assert inspected["action_tokens_available"] is True
        snapshot_ref = inspected["snapshot_ref"]
        element_token = inspected["elements"][0]["element_token"]

        foreign_owner = backend.click(
            "conversation-b", 4321, 77, snapshot_ref,
            element_token=element_token,
        )
        foreign_window = backend.click(
            "conversation-a", 4321, 78, snapshot_ref,
            element_token=element_token,
        )
        assert foreign_owner["status"] == "blocked"
        assert foreign_window["status"] == "blocked"
        assert driver.click_inputs == []

        result = backend.click(
            "conversation-a", 4321, 77, snapshot_ref,
            element_token=element_token,
        )
        assert result["status"] == "action_returned"
        assert result["verified"] is False
        assert result["snapshot_invalidated"] is True
        assert driver.click_inputs[0]["delivery_mode"] is DeliveryMode.BACKGROUND
    finally:
        backend.close()


def test_cua_background_refusal_never_auto_escalates_and_requires_fresh_approved_retry():
    driver = FakeDriver(refuse_background=True)
    backend = _backend(driver)
    try:
        initial = backend.inspect_window("conversation-a", 4321, 77)
        token = initial["elements"][0]["element_token"]
        snapshot_ref = initial["snapshot_ref"]

        # Foreground is blocked before a background refusal exists.
        blocked = backend.click(
            "conversation-a", 4321, 77, snapshot_ref,
            element_token=token, foreground=True,
        )
        assert blocked["status"] == "blocked"
        assert driver.click_inputs == []

        refused = backend.click(
            "conversation-a", 4321, 77, snapshot_ref,
            element_token=token, foreground=False,
        )
        assert refused["status"] == "background_unavailable"
        assert refused["foreground_escalation_used"] is False
        assert refused["foreground_retry_available"] is True
        assert refused["driver_error_code"] == "background_unavailable"
        assert refused["foreground_retry"] == "computer.cua_click_foreground"
        assert backend.can_foreground_retry("conversation-a", 4321) is True
        assert len(driver.click_inputs) == 1
        assert driver.click_inputs[0]["delivery_mode"] is DeliveryMode.BACKGROUND

        # The refused snapshot is invalidated; fresh inspection is mandatory.
        fresh = backend.inspect_window("conversation-a", 4321, 77)
        fresh_token = fresh["elements"][0]["element_token"]
        foreground = backend.click(
            "conversation-a", 4321, 77, fresh["snapshot_ref"],
            element_token=fresh_token, foreground=True,
        )
        assert foreground["status"] == "action_returned"
        assert foreground["foreground_escalation_used"] is True
        assert len(driver.click_inputs) == 2
        assert driver.click_inputs[1]["delivery_mode"] is DeliveryMode.FOREGROUND
    finally:
        backend.close()


def test_cua_coordinates_require_valid_screenshot_and_remain_window_local():
    driver = FakeDriver()
    backend = _backend(driver)
    try:
        no_image = backend.inspect_window("conversation-a", 4321, 77)
        rejected = backend.click(
            "conversation-a", 4321, 77, no_image["snapshot_ref"],
            x=30, y=30,
        )
        assert rejected["status"] == "blocked"
        assert driver.click_inputs == []

        captured = backend.inspect_window(
            "conversation-a", 4321, 77, include_screenshot=True,
        )
        accepted = backend.click(
            "conversation-a", 4321, 77, captured["snapshot_ref"],
            x=30, y=30,
        )
        assert accepted["status"] == "action_returned"
        position = driver.click_inputs[0]["position"]
        assert position["kind"] == "coordinates"
        assert position["x"] == 30
        assert position["y"] == 30
    finally:
        backend.close()


def test_cua_draw_path_reobserves_each_segment_and_reports_pixel_evidence_not_semantics():
    driver = FakeDriver()
    backend = _backend(driver)
    try:
        state = backend.inspect_window(
            "conversation-a", 4321, 77, include_screenshot=True,
        )
        element_token = state["elements"][0]["element_token"]
        result = backend.draw_path(
            "conversation-a", 4321, 77, state["snapshot_ref"], element_token,
            [[[0.1, 0.1], [0.9, 0.9]]],
        )
        assert result["status"] == "completed"
        assert result["accepted_segments"] == 1
        assert result["verified"] is True
        assert result["verification_type"] == "pixel_diff_only"
        assert result["semantic_verified"] is False
        assert result["snapshot_ref_after"]
        assert driver.drag_inputs[0]["from_x"] == 18
        assert driver.drag_inputs[0]["from_y"] == 18
        assert driver.drag_inputs[0]["to_x"] == 82
        assert driver.drag_inputs[0]["to_y"] == 82
    finally:
        backend.close()


def test_cua_draw_path_rejects_out_of_bounds_and_oversized_strokes():
    driver = FakeDriver()
    backend = _backend(driver)
    try:
        state = backend.inspect_window(
            "conversation-a", 4321, 77, include_screenshot=True,
        )
        token = state["elements"][0]["element_token"]
        bad_point = backend.draw_path(
            "conversation-a", 4321, 77, state["snapshot_ref"], token,
            [[[0.1, 0.1], [1.0, 0.9]]],
        )
        assert bad_point["status"] == "blocked"
        assert driver.drag_inputs == []
    finally:
        backend.close()


def test_cua_refusal_without_explicit_foreground_recommendation_cannot_escalate():
    class NoEscalationDriver(FakeDriver):
        async def click(self, request):
            self.click_inputs.append(request.values)
            exc = FakeToolRefusal()
            exc.structured_content = {
                "error": {"kind": "background_unavailable"},
                "escalation": {"recommended": "stop", "reason": "target is unsupported"},
            }
            raise exc

    driver = NoEscalationDriver(refuse_background=True)
    backend = _backend(driver)
    try:
        state = backend.inspect_window("conversation-a", 4321, 77)
        refusal = backend.click(
            "conversation-a", 4321, 77, state["snapshot_ref"],
            element_token=state["elements"][0]["element_token"],
        )
        assert refusal["status"] == "background_unavailable"
        assert refusal["foreground_retry_available"] is False
        assert "foreground_retry" not in refusal
        assert backend.can_foreground_retry("conversation-a", 4321) is False
    finally:
        backend.close()


def test_cua_discovery_contract_returns_bounded_application_and_exact_window_metadata():
    driver = FakeDriver()
    backend = _backend(driver)
    try:
        apps = backend.list_apps()
        assert apps["status"] == "completed"
        assert apps["apps"][0]["pid"] == 4321
        windows = backend.list_windows(4321)
        assert windows["status"] == "completed"
        assert windows["windows"][0]["window_id"] == 77
        assert windows["windows"][0]["pid"] == 4321
    finally:
        backend.close()


def test_cua_snapshot_image_is_available_only_for_the_exact_fresh_screenshot():
    driver = FakeDriver()
    backend = _backend(driver)
    try:
        state = backend.inspect_window(
            "conversation-a", 4321, 77, include_screenshot=True,
        )
        image, error = backend.snapshot_image(
            "conversation-a", 4321, 77, state["snapshot_ref"],
        )
        assert error is None
        assert image is not None
        assert image.size == (100, 100)

        foreign, foreign_error = backend.snapshot_image(
            "conversation-b", 4321, 77, state["snapshot_ref"],
        )
        assert foreign is None
        assert "different conversation" in foreign_error
    finally:
        backend.close()


def test_agent_runtime_validates_cua_click_forms_before_permission_prompt():
    from chimera.agent_runtime import AgentRuntime

    valid_token = {
        "session_id": "authorized-session",
        "window_id": 77,
        "snapshot_ref": "fresh-snapshot",
        "element_token": "s00000001:1",
    }
    assert AgentRuntime._validate_arguments(
        "computer.cua_click", valid_token,
    ) is None

    valid_pixels = {
        "session_id": "authorized-session",
        "window_id": 77,
        "snapshot_ref": "fresh-snapshot",
        "x": 32.5,
        "y": 48,
    }
    assert AgentRuntime._validate_arguments(
        "computer.cua_click", valid_pixels,
    ) is None

    invalid = [
        {key: value for key, value in valid_token.items() if key != "element_token"},
        {**valid_token, "x": 1, "y": 2},
        {**valid_pixels, "y": True},
        {**valid_token, "window_id": True},
    ]
    for arguments in invalid:
        with pytest.raises(ValueError):
            AgentRuntime._validate_arguments("computer.cua_click", arguments)


def test_cua_mutations_remain_build_mode_only_and_registered_as_approved_actions():
    from chimera.task_execution import TaskPolicy
    from chimera.tools import tool_catalog

    operations = tool_catalog()["operations"]
    assert operations["computer.cua_click"]["permission"] == "computer.input"
    assert operations["computer.cua_draw_path"]["permission"] == "computer.input"
    assert operations["computer.cua_capture_window"]["permission"] == "computer.screenshot"
    assert operations["computer.cua_click_foreground"]["permission"] == "computer.input.foreground"

    with pytest.raises(PermissionError, match="read-only"):
        TaskPolicy.validate(
            "plan", "computer.cua_click_foreground", "computer.input.foreground",
        )
    assert TaskPolicy.validate(
        "build", "computer.cua_click_foreground", "computer.input.foreground",
    )


def test_agent_catalog_hides_foreground_cua_tool_until_matching_refusal(tmp_path):
    from types import SimpleNamespace
    from chimera.agent_runtime import AgentRuntime, AgentSessionStore

    class FakeComputer:
        def __init__(self):
            self.uia = object()
            self.visual_perception = object()
            self.cua = object()
            self.sessions = {
                "cua-session": SimpleNamespace(
                    session_id="cua-session",
                    pid=4321,
                    executable="C:/Windows/mspaint.exe",
                    process_create_time=12.0,
                    owner_session_id="conversation-a",
                )
            }
            self.foreground_ready = False

        def _identity_is_current(self, _identity):
            return True

        def interaction_inventory(self):
            return []

        def cua_foreground_retry_available(self, _app_session_id, _owner_session_id):
            return self.foreground_ready

    runtime = AgentRuntime(
        AgentSessionStore(tmp_path / "agent.sqlite"),
        provider=object(),
    )
    fake_computer = FakeComputer()
    runtime.computer = fake_computer
    session = {"session_id": "conversation-a", "workspace": None, "mode": "build"}

    before = runtime._protocol_catalog(session)
    before_ids = {item["id"] for item in before["tools"]}
    assert "computer.cua_click" in before_ids
    assert "computer.cua_click_foreground" not in before_ids

    fake_computer.foreground_ready = True
    after = runtime._protocol_catalog(session)
    after_ids = {item["id"] for item in after["tools"]}
    assert "computer.cua_click_foreground" in after_ids


def test_manager_restricts_cua_windows_to_launch_approved_hwnd_set():
    from types import SimpleNamespace
    from chimera.computer_runtime import ComputerSessionManager

    class FakeWindowsBackend:
        def window_process_id(self, hwnd):
            return 4321 if hwnd in {77, 78} else None

    class FakeCua:
        def __init__(self):
            self.inspect_calls = []
            self.click_calls = []
            self.draw_calls = []

        def list_windows(self, pid):
            return {
                "status": "completed",
                "windows": [
                    {"pid": pid, "window_id": 77, "title": "Approved"},
                    {"pid": pid, "window_id": 78, "title": "Same process, unapproved window"},
                ],
                "count": 2,
            }

        def inspect_window(self, owner, pid, window_id, **kwargs):
            self.inspect_calls.append((owner, pid, window_id))
            return {"status": "completed", "snapshot_ref": "fresh"}

        def click(self, *args, **kwargs):
            self.click_calls.append((args, kwargs))
            return {"status": "action_returned"}

        def draw_path(self, *args, **kwargs):
            self.draw_calls.append((args, kwargs))
            return {"status": "completed"}

    manager = ComputerSessionManager.__new__(ComputerSessionManager)
    manager.backend = FakeWindowsBackend()
    manager.cua = FakeCua()
    identity = SimpleNamespace(
        pid=4321,
        executable="C:/Windows/mspaint.exe",
        authorized_hwnds=(77,),
    )
    manager._cua_authorized_identity = lambda _app, _owner: identity

    listing = manager.cua_list_windows("app-session", "conversation-a")
    assert listing["status"] == "completed"
    assert [window["window_id"] for window in listing["windows"]] == [77]
    assert listing["rejected_unapproved_windows"] == 1

    approved_inspection = manager.cua_inspect_window(
        "app-session", "conversation-a", 77,
    )
    assert approved_inspection["status"] == "completed"
    rejected_inspection = manager.cua_inspect_window(
        "app-session", "conversation-a", 78,
    )
    assert rejected_inspection["status"] == "blocked"
    assert len(manager.cua.inspect_calls) == 1

    rejected_click = manager.cua_click(
        "app-session", "conversation-a", 78, "snapshot",
        element_token="s00000001:1",
    )
    assert rejected_click["status"] == "blocked"
    rejected_draw = manager.cua_draw_path(
        "app-session", "conversation-a", 78, "snapshot",
        "s00000001:1", [[[0.1, 0.1], [0.9, 0.9]]],
    )
    assert rejected_draw["status"] == "blocked"
    assert manager.cua.click_calls == []
    assert manager.cua.draw_calls == []
