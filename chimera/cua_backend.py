"""Optional background-first desktop control powered by the Cua Driver SDK.

Chimera remains the authority for process ownership, per-action approvals, task
persistence and postcondition checks. Cua Driver is an optional transport and
native UIA/event-delivery backend. No action is silently escalated to foreground.
"""
from __future__ import annotations

import asyncio
import atexit
import base64
import hashlib
import inspect
import os
import threading
import time
import uuid
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from io import BytesIO
from typing import Any


class CuaUnavailable(RuntimeError):
    """The optional Cua Driver runtime is not installed or could not start."""


@dataclass
class _Snapshot:
    reference_id: str
    owner_session_id: str
    pid: int
    window_id: int
    snapshot_id: str
    created_at: float
    state: Any
    elements: dict[str, Any]
    screenshot_bytes: bytes | None
    screenshot_width: int
    screenshot_height: int
    screenshot_frame_valid: bool
    window_bounds: dict[str, float] | None


def _get(value: Any, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _enum_name(value: Any) -> str | None:
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name.casefold()
    if isinstance(value, str):
        return value.casefold()
    return None


def _find_code(value: Any, *, depth: int = 0) -> str | None:
    if depth > 5 or value is None:
        return None
    if isinstance(value, str):
        if "background_unavailable" in value.casefold():
            return "background_unavailable"
        return None
    if isinstance(value, dict):
        for key in ("code", "error_code", "kind", "name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and "background_unavailable" in candidate.casefold():
                return "background_unavailable"
        for key in ("error", "details", "structured", "structured_json",
                    "structured_content", "structuredContent", "payload", "content"):
            found = _find_code(value.get(key), depth=depth + 1)
            if found:
                return found
        return None
    for key in ("code", "error_code", "kind", "structured", "structured_json",
                "structured_content", "structuredContent", "details", "error", "payload"):
        try:
            candidate = getattr(value, key, None)
        except Exception:
            candidate = None
        found = _find_code(candidate, depth=depth + 1)
        if found:
            return found
    return None


def _find_foreground_recommendation(value: Any, *, depth: int = 0) -> bool:
    if depth > 5 or value is None:
        return False
    if isinstance(value, dict):
        escalation = value.get("escalation")
        if isinstance(escalation, dict):
            recommended = escalation.get("recommended") or escalation.get("target")
            if isinstance(recommended, str) and recommended.casefold() == "foreground":
                return True
        return any(
            _find_foreground_recommendation(value.get(key), depth=depth + 1)
            for key in ("error", "details", "structured", "structured_json",
                        "structured_content", "structuredContent", "payload", "content")
        )
    for key in ("structured", "structured_json", "structured_content",
                "structuredContent", "details", "error", "payload"):
        try:
            candidate = getattr(value, key, None)
        except Exception:
            candidate = None
        if _find_foreground_recommendation(candidate, depth=depth + 1):
            return True
    return False


def _finite_number(value: Any) -> bool:
    try:
        import math
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
    except (TypeError, ValueError, OverflowError):
        return False


class CuaDriverBackend:
    """Synchronous Chimera facade over the async, optional Cua Driver SDK.

    The SDK owns one long-lived native runtime on a dedicated asyncio thread.
    This avoids calling asyncio.run() from Flet/agent threads and keeps the
    native driver lifecycle stable for the life of Chimera.
    """

    SNAPSHOT_TTL_SECONDS = 30.0
    MAX_ELEMENTS = 300
    MAX_STROKES = 16
    MAX_POINTS_PER_STROKE = 64
    MAX_TOTAL_POINTS = 256

    def __init__(
        self,
        *,
        driver_factory=None,
        api=None,
        timeout_seconds: float = 45.0,
    ):
        self._driver_factory = driver_factory
        self._api = api
        self.timeout_seconds = max(5.0, float(timeout_seconds))
        self._loop = None
        self._driver = None
        self._thread = None
        self._ready = threading.Event()
        self._start_error: str | None = None
        self._closed = False
        self._lock = threading.RLock()
        self._snapshots: dict[str, _Snapshot] = {}
        self._foreground_escalations: set[tuple[str, int, int]] = set()
        self.version = getattr(api, "__version__", "unknown") if api else "unknown"

    @classmethod
    def from_environment(cls) -> "CuaDriverBackend | None":
        """Return an enabled optional backend without starting its native runtime."""
        enabled = os.environ.get("CHIMERA_CUA_ENABLED", "auto").strip().casefold()
        if enabled in {"0", "false", "no", "off", "disabled"}:
            return None
        if enabled not in {"1", "true", "yes", "on", "auto", ""}:
            return None
        try:
            import cua_driver
            required = (
                "CuaDriver", "ListAppsInput", "ListWindowsInput",
                "GetWindowStateInput", "ActionTarget", "ClickInput",
                "ClickPosition", "InputDeliveryMode", "ClickButton",
                "DragInput",
            )
            if any(not hasattr(cua_driver, name) for name in required):
                return None
            backend = cls(api=cua_driver)
            backend.version = str(getattr(cua_driver, "__version__", "unknown"))
            atexit.register(backend.close)
            return backend
        except Exception:
            return None

    @property
    def available(self) -> bool:
        return self._api is not None or self._driver_factory is not None

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            if self._driver_factory is not None:
                self._driver = self._driver_factory()
            else:
                self._driver = self._api.CuaDriver.create()
        except Exception as exc:
            self._start_error = f"{type(exc).__name__}: {exc}"
        finally:
            self._ready.set()
        if self._start_error:
            loop.close()
            return
        loop.run_forever()
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()

    def _ensure_started(self) -> None:
        if not self.available:
            raise CuaUnavailable("cua-driver is not installed; install Chimera's [computer-use] extra")
        if self._closed:
            raise CuaUnavailable("Cua Driver runtime has already been shut down")
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._thread_main,
                    name="Chimera-CuaDriver",
                    daemon=True,
                )
                self._thread.start()
        if not self._ready.wait(self.timeout_seconds):
            raise CuaUnavailable("timed out while initializing the Cua Driver native runtime")
        if self._start_error:
            raise CuaUnavailable(f"Cua Driver could not initialize: {self._start_error}")
        if self._loop is None or self._driver is None:
            raise CuaUnavailable("Cua Driver runtime did not initialize")

    def _call(self, method_name: str, argument: Any) -> Any:
        self._ensure_started()

        async def invoke():
            method = getattr(self._driver, method_name, None)
            if not callable(method):
                raise CuaUnavailable(f"Cua Driver SDK does not expose {method_name}()")
            result = method(argument)
            return await result if inspect.isawaitable(result) else result

        future = asyncio.run_coroutine_threadsafe(invoke(), self._loop)
        try:
            return future.result(timeout=self.timeout_seconds)
        except FutureTimeout as exc:
            future.cancel()
            raise TimeoutError(f"Cua Driver {method_name} timed out") from exc

    @staticmethod
    def _window_data(item: Any) -> dict:
        bounds = _get(item, "bounds")
        return {
            "window_id": _get(item, "window_id"),
            "pid": _get(item, "pid"),
            "app_name": _get(item, "app_name"),
            "title": _get(item, "title"),
            "bounds": {
                "x": _get(bounds, "x"),
                "y": _get(bounds, "y"),
                "width": _get(bounds, "width"),
                "height": _get(bounds, "height"),
            } if bounds is not None else None,
            "is_on_screen": bool(_get(item, "is_on_screen", False)),
            "minimized": _get(item, "minimized"),
        }

    def list_apps(self) -> dict:
        try:
            result = self._call("list_apps", self._api.ListAppsInput())
            apps = []
            for item in (_get(result, "apps", []) or [])[:300]:
                apps.append({
                    "pid": _get(item, "pid"),
                    "name": _get(item, "name"),
                    "running": bool(_get(item, "running", False)),
                    "active": bool(_get(item, "active", False)),
                    "launch_path": _get(item, "launch_path"),
                    "bundle_id": _get(item, "bundle_id"),
                    "kind": _get(item, "kind"),
                })
            return {
                "status": "completed",
                "provider": "cua_driver",
                "version": self.version,
                "apps": apps,
                "count": len(apps),
            }
        except Exception as exc:
            return {"status": "unavailable" if isinstance(exc, CuaUnavailable) else "failed",
                    "provider": "cua_driver", "reason": f"{type(exc).__name__}: {exc}"}

    def list_windows(self, pid: int) -> dict:
        if type(pid) is not int or pid <= 0:
            return {"status": "blocked", "reason": "pid must be a positive integer"}
        try:
            result = self._call(
                "list_windows",
                self._api.ListWindowsInput(pid=pid, on_screen_only=True),
            )
            windows = [
                self._window_data(item)
                for item in (_get(result, "windows", []) or [])
                if _get(item, "pid") == pid
            ][:100]
            return {"status": "completed", "provider": "cua_driver",
                    "pid": pid, "windows": windows, "count": len(windows)}
        except Exception as exc:
            return {"status": "unavailable" if isinstance(exc, CuaUnavailable) else "failed",
                    "provider": "cua_driver", "pid": pid,
                    "reason": f"{type(exc).__name__}: {exc}"}

    def _fetch_state(self, pid: int, window_id: int, *, screenshot: bool,
                     max_elements: int = MAX_ELEMENTS, max_depth: int = 8) -> Any:
        args = self._api.GetWindowStateInput(
            pid=pid,
            window_id=window_id,
            session=None,
            query=None,
            include_accessibility_tree=True,
            include_screenshot=screenshot,
            screenshot_out_file=None,
            max_elements=max_elements,
            max_depth=max_depth,
            max_dimension=2048,
            max_image_dimension=1280,
            timeout_ms=20000,
            tree_format="elements",
            since=None,
            verbose=False,
            full_output=False,
        )
        return self._call("get_window_state", args)

    @staticmethod
    def _state_elements(state: Any) -> list:
        elements = _get(state, "elements", None)
        return list(elements or [])

    @staticmethod
    def _frame_data(element: Any) -> dict | None:
        frame = _get(element, "frame")
        if frame is None:
            return None
        values = {key: _get(frame, key) for key in ("x", "y", "w", "h")}
        if not all(_finite_number(value) for value in values.values()):
            return None
        if values["w"] <= 1 or values["h"] <= 1:
            return None
        return {key: float(value) for key, value in values.items()}

    @staticmethod
    def _window_bounds(state: Any) -> dict[str, float] | None:
        bounds = _get(state, "window_bounds")
        if bounds is None:
            return None
        values = {
            "x": _get(bounds, "x"),
            "y": _get(bounds, "y"),
            "width": _get(bounds, "width"),
            "height": _get(bounds, "height"),
        }
        if not all(_finite_number(value) for value in values.values()):
            return None
        if values["width"] <= 0 or values["height"] <= 0:
            return None
        return {key: float(value) for key, value in values.items()}

    @classmethod
    def _window_local_frame(
        cls, state: Any, screen_frame: dict, reference_snapshot: _Snapshot,
    ) -> dict[str, float]:
        """Convert screen-absolute UIA frame pixels into this window's screenshot space.

        Cua Driver element frames are screen-absolute, while drag/click pixel
        coordinates are window-local screenshot pixels. The screenshot can be
        downscaled, so scale must be derived from the exact captured window
        bounds and screenshot dimensions, never guessed.
        """
        original_bounds = reference_snapshot.window_bounds
        current_bounds = cls._window_bounds(state)
        if original_bounds is None or current_bounds is None:
            raise ValueError("window geometry is unavailable; cannot convert UIA frame to screenshot pixels")
        if (
            abs(current_bounds["width"] - original_bounds["width"]) > 2.0
            or abs(current_bounds["height"] - original_bounds["height"]) > 2.0
        ):
            raise ValueError("window size changed since the reference screenshot; take a new snapshot")
        if (
            reference_snapshot.screenshot_width <= 0
            or reference_snapshot.screenshot_height <= 0
        ):
            raise ValueError("reference snapshot has no valid screenshot dimensions")
        scale_x = reference_snapshot.screenshot_width / original_bounds["width"]
        scale_y = reference_snapshot.screenshot_height / original_bounds["height"]
        local = {
            "x": (screen_frame["x"] - current_bounds["x"]) * scale_x,
            "y": (screen_frame["y"] - current_bounds["y"]) * scale_y,
            "w": screen_frame["w"] * scale_x,
            "h": screen_frame["h"] * scale_y,
        }
        if not all(_finite_number(value) for value in local.values()):
            raise ValueError("converted canvas frame is not finite")
        if (
            local["x"] < 0 or local["y"] < 0
            or local["x"] + local["w"] > reference_snapshot.screenshot_width + 1
            or local["y"] + local["h"] > reference_snapshot.screenshot_height + 1
            or local["w"] < 8 or local["h"] < 8
        ):
            raise ValueError("canvas frame falls outside the exact window screenshot")
        return local

    @staticmethod
    def _image_bytes(state: Any) -> tuple[bytes | None, str | None]:
        images = _get(state, "images", []) or []
        if not images:
            return None, None
        item = images[0]
        encoded = _get(item, "data_base64")
        mime = _get(item, "mime_type")
        if not isinstance(encoded, str) or len(encoded) > 20 * 1024 * 1024:
            return None, mime
        try:
            return base64.b64decode(encoded, validate=True), mime
        except (ValueError, TypeError):
            return None, mime

    def _compact_state(self, state: Any, snapshot_ref: str | None) -> dict:
        elements = []
        for item in self._state_elements(state)[:self.MAX_ELEMENTS]:
            elements.append({
                "element_index": _get(item, "element_index"),
                "element_token": _get(item, "element_token"),
                "role": _get(item, "role"),
                "label": _get(item, "label"),
                "value": _get(item, "value"),
                "value_description": _get(item, "value_description"),
                "enabled": _get(item, "enabled"),
                "selected": _get(item, "selected"),
                "in_web_content": _get(item, "in_web_content"),
                "actions": list(_get(item, "actions", []) or [])[:16],
                "frame": self._frame_data(item),
                "depth": _get(item, "depth"),
            })
        return {
            "pid": _get(state, "pid"),
            "window_id": _get(state, "window_id"),
            "snapshot_id": _get(state, "snapshot_id"),
            "snapshot_ref": snapshot_ref,
            "app_name": _get(state, "app_name"),
            "window_title": _get(state, "window_title"),
            "tree_markdown": str(_get(state, "tree_markdown") or "")[:12000],
            "elements": elements,
            "element_count": _get(state, "element_count"),
            "total_element_count": _get(state, "total_element_count"),
            "elements_complete": _get(state, "elements_complete"),
            "degraded": bool(_get(state, "degraded", False)),
            "degraded_reason": _get(state, "degraded_reason"),
            "truncated": bool(_get(state, "truncated", False)),
            "truncation_reason": _get(state, "truncation_reason"),
            "screenshot_width": _get(state, "screenshot_width"),
            "screenshot_height": _get(state, "screenshot_height"),
            "screenshot_scale": _get(state, "screenshot_scale"),
            "screenshot_frame_valid": bool(_get(state, "screenshot_frame_valid", False)),
            "screenshot_mime_type": _get(state, "screenshot_mime_type"),
        }

    def inspect_window(self, owner_session_id: str, pid: int, window_id: int,
                       *, include_screenshot: bool = False) -> dict:
        if type(pid) is not int or pid <= 0 or type(window_id) is not int or window_id <= 0:
            return {"status": "blocked", "reason": "pid and window_id must be positive integers"}
        try:
            windows_result = self._call(
                "list_windows",
                self._api.ListWindowsInput(pid=pid, on_screen_only=True),
            )
            matches = [
                item for item in (_get(windows_result, "windows", []) or [])
                if _get(item, "pid") == pid and _get(item, "window_id") == window_id
                and bool(_get(item, "is_on_screen", False))
            ]
            if len(matches) != 1:
                return {"status": "blocked", "reason": "the exact CUA window is not uniquely visible for this authorized PID"}

            state = self._fetch_state(pid, window_id, screenshot=include_screenshot)
            snapshot_id = _get(state, "snapshot_id")
            if not isinstance(snapshot_id, str) or not snapshot_id:
                return {"status": "failed", "reason": "Cua Driver returned no snapshot_id; actions are not permitted"}
            now = time.time()
            snapshot_ref = uuid.uuid4().hex
            image_bytes, image_mime = self._image_bytes(state)
            snap = _Snapshot(
                snapshot_ref, owner_session_id, pid, window_id,
                snapshot_id, now,
                state,
                {
                    str(_get(item, "element_token")): item
                    for item in self._state_elements(state)
                    if isinstance(_get(item, "element_token"), str)
                    and _get(item, "element_token")
                },
                image_bytes,
                int(_get(state, "screenshot_width") or 0),
                int(_get(state, "screenshot_height") or 0),
                bool(_get(state, "screenshot_frame_valid", False)),
                self._window_bounds(state),
            )
            # A new snapshot invalidates every earlier token for this exact window.
            with self._lock:
                for key, previous in list(self._snapshots.items()):
                    if previous.owner_session_id == owner_session_id and previous.pid == pid and previous.window_id == window_id:
                        self._snapshots.pop(key, None)
                self._snapshots[snapshot_ref] = snap

            compact = self._compact_state(state, snapshot_ref)
            compact.update({
                "status": "completed" if not compact["degraded"] and not compact["truncated"] else "degraded",
                "provider": "cua_driver",
                "screenshot_available": image_bytes is not None,
                "screenshot_mime_type": image_mime,
                "action_tokens_available": bool(snap.elements) and not compact["degraded"] and not compact["truncated"],
                "limitations": [
                    "CUA element tokens are scoped to this exact snapshot, PID and window ID.",
                    "Take a fresh snapshot after every action; never reuse these tokens afterwards.",
                    "A returned action result is not proof of the task's postcondition.",
                ],
            })
            return compact
        except Exception as exc:
            return {"status": "unavailable" if isinstance(exc, CuaUnavailable) else "failed",
                    "provider": "cua_driver", "pid": pid, "window_id": window_id,
                    "reason": f"{type(exc).__name__}: {exc}"}

    def can_foreground_retry(self, owner_session_id: str, pid: int) -> bool:
        """True only after an explicit CUA refusal recommended foreground for this PID."""
        with self._lock:
            return any(
                owner == owner_session_id and process_id == pid
                for owner, process_id, _window_id in self._foreground_escalations
            )

    def _snapshot_for_action(self, owner_session_id: str, pid: int, window_id: int,
                             snapshot_ref: str) -> tuple[_Snapshot | None, dict | None]:
        with self._lock:
            snap = self._snapshots.get(snapshot_ref)
        if snap is None:
            return None, {"status": "blocked", "reason": "snapshot_ref is stale or unknown; inspect the exact window again"}
        if (
            snap.owner_session_id != owner_session_id
            or snap.pid != pid
            or snap.window_id != window_id
        ):
            return None, {"status": "blocked", "reason": "snapshot_ref belongs to a different conversation, process or window"}
        if time.time() - snap.created_at > self.SNAPSHOT_TTL_SECONDS:
            with self._lock:
                self._snapshots.pop(snapshot_ref, None)
            return None, {"status": "blocked", "reason": "snapshot_ref expired; inspect the exact window again"}
        if bool(_get(snap.state, "degraded", False)) or bool(_get(snap.state, "truncated", False)):
            return None, {"status": "blocked", "reason": "degraded or truncated snapshots cannot authorize CUA actions"}
        return snap, None

    @staticmethod
    def _action_summary(result: Any) -> dict:
        effect = _enum_name(_get(result, "effect")) or "unknown"
        route = _enum_name(_get(result, "route")) or "unknown"
        escalation = _get(result, "escalation")
        return {
            "driver_effect": effect,
            "driver_route": route,
            "driver_summary": str(_get(result, "summary") or "")[:1000],
            "driver_error": str(_get(result, "error") or "")[:1000] if _get(result, "error") else None,
            "driver_hint": str(_get(result, "hint") or "")[:800] if _get(result, "hint") else None,
            "escalation": {
                "target": _enum_name(_get(escalation, "target")),
                "reason": _enum_name(_get(escalation, "reason")),
            } if escalation is not None else None,
        }

    @staticmethod
    def _exception_result(exc: Exception) -> dict:
        message = f"{type(exc).__name__}: {exc}"
        code = _find_code(exc)
        if code == "background_unavailable":
            recommended = _find_foreground_recommendation(exc)
            result = {
                "status": "background_unavailable",
                "verified": False,
                "driver_error_code": code,
                "reason": message[:1200],
                "foreground_retry_available": recommended,
                "note": (
                    "Take a fresh snapshot and request a separate operator approval for foreground delivery."
                    if recommended else
                    "The driver refused background delivery but did not recommend foreground escalation. Stop and inspect the blocker."
                ),
            }
            if recommended:
                result["foreground_retry"] = "computer.cua_click_foreground"
            return result
        return {"status": "failed", "verified": False, "reason": message[:1200]}

    def click(self, owner_session_id: str, pid: int, window_id: int, snapshot_ref: str,
              *, element_token: str | None = None, x: float | None = None,
              y: float | None = None, foreground: bool = False,
              button: str = "left", count: int = 1) -> dict:
        snap, blocked = self._snapshot_for_action(owner_session_id, pid, window_id, snapshot_ref)
        if blocked:
            return blocked
        if button not in {"left", "right", "middle"} or type(count) is not int or not 1 <= count <= 2:
            return {"status": "blocked", "reason": "button must be left/right/middle and count must be 1 or 2"}
        escalation_key = (owner_session_id, pid, window_id)
        if foreground:
            with self._lock:
                if escalation_key not in self._foreground_escalations:
                    return {
                        "status": "blocked",
                        "verified": False,
                        "reason": "foreground delivery is available only after an explicit background_unavailable refusal for this exact authorized window",
                    }
                self._foreground_escalations.discard(escalation_key)
        if element_token:
            if element_token not in snap.elements:
                return {"status": "blocked", "reason": "element_token is not present in this exact snapshot"}
            position = self._api.ClickPosition.ELEMENT(element_token=element_token)
        else:
            if not (_finite_number(x) and _finite_number(y)):
                return {"status": "blocked", "reason": "coordinates must be finite window-screenshot pixels"}
            if not snap.screenshot_frame_valid or not snap.screenshot_width or not snap.screenshot_height:
                return {"status": "blocked", "reason": "coordinate clicks require a valid screenshot from this exact snapshot"}
            if not (0 <= float(x) < snap.screenshot_width and 0 <= float(y) < snap.screenshot_height):
                return {"status": "blocked", "reason": "coordinates fall outside the captured window screenshot"}
            position = self._api.ClickPosition.COORDINATES(x=float(x), y=float(y))
        try:
            result = self._call(
                "click",
                self._api.ClickInput(
                    target=self._api.ActionTarget.WINDOW(pid=pid, window_id=window_id),
                    position=position,
                    delivery_mode=(
                        self._api.InputDeliveryMode.FOREGROUND
                        if foreground else self._api.InputDeliveryMode.BACKGROUND
                    ),
                    session=None,
                    button={
                        "left": self._api.ClickButton.LEFT,
                        "right": self._api.ClickButton.RIGHT,
                        "middle": self._api.ClickButton.MIDDLE,
                    }[button],
                    count=count,
                ),
            )
            summary = self._action_summary(result)
            # The click invalidates its source snapshot. A new observation is
            # mandatory before another token can be used.
            with self._lock:
                self._snapshots.pop(snapshot_ref, None)
            if summary["driver_effect"] == "refused" and not foreground:
                escalation = summary.get("escalation") or {}
                if escalation.get("target") == "foreground":
                    with self._lock:
                        self._foreground_escalations.add(escalation_key)
                    return {
                        "status": "background_unavailable",
                        "verified": False,
                        "foreground_escalation_used": False,
                        "snapshot_invalidated": True,
                        **summary,
                        "foreground_retry": "computer.cua_click_foreground",
                        "next_step": "Take a fresh snapshot. Ask for a separate approval for the exact foreground retry; do not retry automatically.",
                    }
            return {
                "status": "action_returned",
                "verified": False,
                "foreground_escalation_used": bool(foreground),
                "snapshot_invalidated": True,
                **summary,
                "next_step": "Take a fresh CUA window snapshot and verify the intended UI state before reporting success.",
            }
        except Exception as exc:
            with self._lock:
                self._snapshots.pop(snapshot_ref, None)
            error_result = self._exception_result(exc)
            if (
                not foreground
                and error_result.get("status") == "background_unavailable"
                and error_result.get("foreground_retry_available") is True
            ):
                with self._lock:
                    self._foreground_escalations.add(escalation_key)
            return error_result

    @staticmethod
    def _decode_image(snapshot: _Snapshot):
        if not snapshot.screenshot_bytes:
            return None
        try:
            from PIL import Image
            return Image.open(BytesIO(snapshot.screenshot_bytes)).convert("RGB")
        except Exception:
            return None

    @staticmethod
    def _canvas_signature(element: Any) -> tuple[str, str, dict]:
        return (
            str(_get(element, "role") or ""),
            str(_get(element, "label") or ""),
            CuaDriverBackend._frame_data(element) or {},
        )

    @staticmethod
    def _find_canvas(state: Any, signature: tuple[str, str, dict]) -> Any | None:
        role, label, frame = signature
        candidates = []
        for item in CuaDriverBackend._state_elements(state):
            if str(_get(item, "role") or "") != role or str(_get(item, "label") or "") != label:
                continue
            current = CuaDriverBackend._frame_data(item)
            if not current:
                continue
            # Refuse a geometry shift; the agent must re-inspect and plan again.
            if all(abs(current[key] - frame[key]) <= 2.0 for key in ("x", "y", "w", "h")):
                candidates.append(item)
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _crop_frame(image, frame: dict):
        x1 = int(round(frame["x"]))
        y1 = int(round(frame["y"]))
        x2 = int(round(frame["x"] + frame["w"]))
        y2 = int(round(frame["y"] + frame["h"]))
        if x1 < 0 or y1 < 0 or x2 > image.width or y2 > image.height or x2 - x1 < 8 or y2 - y1 < 8:
            raise ValueError("canvas frame is outside the screenshot coordinate space")
        return image.crop((x1, y1, x2, y2))

    @staticmethod
    def _changed_fraction(before, after) -> float | None:
        if before is None or after is None or before.size != after.size:
            return None
        try:
            from PIL import ImageChops
            difference = ImageChops.difference(before, after).convert("L")
            thresholded = difference.point(lambda value: 255 if value > 12 else 0)
            changed = sum(thresholded.histogram()[1:])
            return changed / max(1, before.width * before.height)
        except Exception:
            return None

    def draw_path(self, owner_session_id: str, pid: int, window_id: int,
                  snapshot_ref: str, canvas_element_token: str,
                  strokes: list[list[list[float]]], *, duration_ms: int = 240,
                  steps: int = 12) -> dict:
        original, blocked = self._snapshot_for_action(owner_session_id, pid, window_id, snapshot_ref)
        if blocked:
            return blocked
        if not isinstance(strokes, list) or not 1 <= len(strokes) <= self.MAX_STROKES:
            return {"status": "blocked", "reason": f"strokes must contain 1 to {self.MAX_STROKES} disconnected paths"}
        total = 0
        normalized = []
        for path in strokes:
            if not isinstance(path, list) or not 2 <= len(path) <= self.MAX_POINTS_PER_STROKE:
                return {"status": "blocked", "reason": f"each path must contain 2 to {self.MAX_POINTS_PER_STROKE} points"}
            clean = []
            for point in path:
                if not isinstance(point, list) or len(point) != 2 or not all(_finite_number(value) and 0.02 <= float(value) <= 0.98 for value in point):
                    return {"status": "blocked", "reason": "points must be normalized [x,y] pairs between 0.02 and 0.98"}
                clean.append((float(point[0]), float(point[1])))
            total += len(clean)
            normalized.append(clean)
        if total > self.MAX_TOTAL_POINTS:
            return {"status": "blocked", "reason": f"draw path is limited to {self.MAX_TOTAL_POINTS} total points"}
        if type(duration_ms) is not int or not 60 <= duration_ms <= 1200:
            return {"status": "blocked", "reason": "duration_ms must be an integer from 60 to 1200"}
        if type(steps) is not int or not 2 <= steps <= 40:
            return {"status": "blocked", "reason": "steps must be an integer from 2 to 40"}
        if canvas_element_token not in original.elements:
            return {"status": "blocked", "reason": "canvas_element_token is not present in this snapshot"}

        initial_element = original.elements[canvas_element_token]
        signature = self._canvas_signature(initial_element)
        frame = signature[2]
        if not frame:
            return {"status": "blocked", "reason": "canvas element does not expose a usable frame"}
        before_image = self._decode_image(original)
        if before_image is None or not original.screenshot_frame_valid:
            return {"status": "blocked", "reason": "drawing requires a valid before screenshot from the exact window snapshot"}
        try:
            local_canvas_frame = self._window_local_frame(original.state, frame, original)
            before_crop = self._crop_frame(before_image, local_canvas_frame)
        except ValueError as exc:
            return {"status": "blocked", "reason": str(exc)}
        current_state = original.state
        current_ref = snapshot_ref
        segment_results = []
        last_snapshot = None

        try:
            for stroke_index, path in enumerate(normalized):
                for point_index in range(len(path) - 1):
                    element = self._find_canvas(current_state, signature)
                    if element is None:
                        return {
                            "status": "partial" if segment_results else "blocked",
                            "verified": False,
                            "segments_sent": len(segment_results),
                            "reason": "canvas disappeared, became ambiguous, or changed geometry; remaining strokes were stopped",
                            "segment_results": segment_results[-12:],
                        }
                    live_frame = self._frame_data(element)
                    try:
                        local_live_frame = self._window_local_frame(
                            current_state, live_frame, original,
                        )
                    except ValueError as exc:
                        return {
                            "status": "partial" if segment_results else "blocked",
                            "verified": False,
                            "segments_sent": len(segment_results),
                            "reason": str(exc),
                            "segment_results": segment_results[-12:],
                        }
                    x1 = local_live_frame["x"] + path[point_index][0] * local_live_frame["w"]
                    y1 = local_live_frame["y"] + path[point_index][1] * local_live_frame["h"]
                    x2 = local_live_frame["x"] + path[point_index + 1][0] * local_live_frame["w"]
                    y2 = local_live_frame["y"] + path[point_index + 1][1] * local_live_frame["h"]
                    if not (
                        0 <= x1 < original.screenshot_width and 0 <= x2 < original.screenshot_width
                        and 0 <= y1 < original.screenshot_height and 0 <= y2 < original.screenshot_height
                    ):
                        return {
                            "status": "partial" if segment_results else "blocked",
                            "verified": False,
                            "segments_sent": len(segment_results),
                            "reason": "canvas coordinates are outside the screenshot; stopped before sending this segment",
                        }

                    drag_result = self._call(
                        "drag",
                        self._api.DragInput(
                            from_x=x1, from_y=y1, to_x=x2, to_y=y2,
                            target=self._api.ActionTarget.WINDOW(pid=pid, window_id=window_id),
                            scope=None, session=None,
                            duration_ms=duration_ms,
                            steps=steps,
                            button=self._api.ClickButton.LEFT,
                            modifier=None,
                        ),
                    )
                    drag_error = bool(_get(drag_result, "is_error", False))
                    drag_text = str(_get(drag_result, "text") or "")
                    segment_results.append({
                        "stroke_index": stroke_index,
                        "segment_index": point_index,
                        "is_error": drag_error,
                        "text": drag_text[:500],
                    })
                    if drag_error or "background_unavailable" in drag_text.casefold():
                        with self._lock:
                            self._snapshots.pop(current_ref, None)
                        return {
                            "status": "background_unavailable" if "background_unavailable" in drag_text.casefold() else "partial",
                            "verified": False,
                            "segments_sent": len(segment_results),
                            "reason": drag_text[:1000] or "Cua Driver refused a background drag segment",
                            "segment_results": segment_results[-12:],
                            "note": "No foreground drag was attempted. Do not retry blindly; inspect the current window first.",
                        }

                    # Snapshot after every drag. Tokens are refreshed and the
                    # same unique canvas role/label/frame must still be present.
                    last_segment = (
                        stroke_index == len(normalized) - 1
                        and point_index == len(path) - 2
                    )
                    state = self._fetch_state(
                        pid, window_id,
                        screenshot=last_segment,
                        max_elements=self.MAX_ELEMENTS,
                        max_depth=8,
                    )
                    if (
                        bool(_get(state, "degraded", False))
                        or bool(_get(state, "truncated", False))
                        or not _get(state, "snapshot_id")
                    ):
                        with self._lock:
                            self._snapshots.pop(current_ref, None)
                        return {
                            "status": "partial",
                            "verified": False,
                            "segments_sent": len(segment_results),
                            "reason": "the post-drag CUA snapshot was degraded/truncated; stopped without replay",
                            "segment_results": segment_results[-12:],
                        }
                    current_state = state
                    next_ref = uuid.uuid4().hex
                    image_bytes, _mime = self._image_bytes(state)
                    snap = _Snapshot(
                        next_ref, owner_session_id, pid, window_id,
                        _get(state, "snapshot_id"), time.time(), state,
                        {
                            str(_get(item, "element_token")): item
                            for item in self._state_elements(state)
                            if isinstance(_get(item, "element_token"), str) and _get(item, "element_token")
                        },
                        image_bytes,
                        int(_get(state, "screenshot_width") or 0),
                        int(_get(state, "screenshot_height") or 0),
                        bool(_get(state, "screenshot_frame_valid", False)),
                        self._window_bounds(state),
                    )
                    with self._lock:
                        self._snapshots.pop(current_ref, None)
                        self._snapshots[next_ref] = snap
                    current_ref = next_ref
                    last_snapshot = self._compact_state(state, next_ref)
                    if last_segment and image_bytes is not None and snap.screenshot_frame_valid:
                        after_image = self._decode_image(snap)
                        try:
                            after_crop = self._crop_frame(after_image, local_canvas_frame)
                            changed_fraction = self._changed_fraction(before_crop, after_crop)
                        except (TypeError, ValueError):
                            changed_fraction = None
                    else:
                        changed_fraction = None

            pixel_verified = changed_fraction is not None and changed_fraction > 0.00001
            return {
                "status": "completed",
                "accepted_segments": len(segment_results),
                "verified": bool(pixel_verified),
                "verification_type": "pixel_diff_only" if pixel_verified else "not_verified",
                "semantic_verified": False,
                "changed_fraction": changed_fraction,
                "before_sha256": hashlib.sha256(before_crop.tobytes()).hexdigest(),
                "after_sha256": hashlib.sha256(after_crop.tobytes()).hexdigest() if 'after_crop' in locals() else None,
                "snapshot_ref_after": current_ref,
                "verification_snapshot": last_snapshot,
                "segment_results": segment_results[-12:],
                "note": "Pixel change does not establish that the requested drawing is semantically correct. Inspect the fresh canvas and verify the requested content.",
            }
        except Exception as exc:
            with self._lock:
                self._snapshots.pop(current_ref, None)
            return {
                "status": "partial" if segment_results else "failed",
                "verified": False,
                "accepted_segments": len(segment_results),
                "reason": f"{type(exc).__name__}: {exc}",
                "segment_results": segment_results[-12:],
                "note": "Some segments may have been delivered. Observe current application state before attempting any further input.",
            }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        loop, thread, driver = self._loop, self._thread, self._driver
        if loop is not None and thread is not None and thread.is_alive() and driver is not None:
            shutdown = getattr(driver, "shutdown", None)
            if callable(shutdown):
                async def invoke_shutdown():
                    result = shutdown()
                    if inspect.isawaitable(result):
                        await result
                try:
                    asyncio.run_coroutine_threadsafe(invoke_shutdown(), loop).result(timeout=8)
                except Exception:
                    pass
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
        with self._lock:
            self._snapshots.clear()
            self._foreground_escalations.clear()
        self._driver = None
        self._loop = None
