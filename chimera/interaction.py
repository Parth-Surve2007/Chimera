"""Adaptive, evidence-based computer interaction contracts.

Backends describe what they can prove before the model selects an action.  This
module deliberately does not treat coordinate input or an installed package as
proof that an interaction is safe or available.
"""
from __future__ import annotations

import importlib.util
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class InteractionBackendInfo:
    name: str
    available: bool
    operations: tuple[str, ...] = ()
    observations: tuple[str, ...] = ()
    application_types: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()


@dataclass(frozen=True)
class StrategyDecision:
    selected: str | None
    reason: str
    evidence: tuple[str, ...] = ()
    alternatives: tuple[InteractionBackendInfo, ...] = ()


class InteractionBackend(Protocol):
    def describe(self) -> InteractionBackendInfo: ...


class UIABackendDescriptor:
    def __init__(self, manager=None): self.manager = manager
    def describe(self):
        available = bool(self.manager and getattr(self.manager, "uia", None))
        return InteractionBackendInfo(
            "uia", available,
            ("read_text", "focus_element", "invoke_element", "set_value", "toggle", "select_item"),
            ("uia_tree", "control_patterns", "focus_state"),
            ("native_windows",),
            () if available else ("UI Automation provider unavailable",),
            ("computer.observe", "computer.input"),
            ("fresh_reference", "post_action_observation"),
        )


class Win32BackendDescriptor:
    def __init__(self, manager=None): self.manager = manager
    def describe(self):
        return InteractionBackendInfo(
            "win32", True,
            ("focus_window", "inspect_controls", "screenshot"),
            ("window_identity", "window_geometry"),
            ("native_windows",),
            ("controls are less semantic than UIA",),
            ("computer.observe", "computer.input", "computer.screenshot"),
            ("window_identity", "fresh_geometry"),
        )


class GroundedInputBackend:
    """Keyboard input bound to a previously observed application window."""
    def __init__(self, manager): self.manager = manager
    def describe(self):
        available = importlib.util.find_spec("pywinauto") is not None
        return InteractionBackendInfo(
            "grounded_input", available,
            ("focus_window", "send_keys", "scroll"),
            ("window_geometry", "foreground_identity"),
            ("native_windows", "browser_windows"),
            ("requires a fresh authorized window observation; no blind coordinates",),
            ("computer.input",),
            ("foreground_process_identity", "post_action_observation"),
        )

    def send_keys(self, session_id: str, keys: str, approved: bool = False) -> dict:
        if not approved:
            return {"status": "awaiting_approval", "operation": "send_keys"}
        identity = self.manager.sessions.get(session_id)
        observation = self.manager.observations.get(session_id, (0, None))[1]
        if identity is None or observation is None:
            return {"status": "blocked", "reason": "fresh authorized observation required"}
        try:
            from pywinauto import Desktop
            window = Desktop(backend="uia").window(handle=observation.windows[0]["hwnd"])
            if window.element_info.process_id != identity.pid:
                return {"status": "blocked", "reason": "foreground window identity changed"}
            window.set_focus()
            window.type_keys(keys, with_spaces=True)
            return {"status": "completed", "verified": True, "backend": "grounded_input", "session_id": session_id}
        except Exception as exc:
            return {"status": "failed", "verified": False, "reason": str(exc), "backend": "grounded_input"}


class BrowserBackendDescriptor:
    """Truthful capability descriptor; browser attachment is opt-in and explicit."""
    def describe(self):
        clients = tuple(name for name in ("playwright", "selenium", "pychrome") if importlib.util.find_spec(name))
        return InteractionBackendInfo(
            "browser", bool(clients),
            ("inspect_dom", "inspect_accessibility", "click", "fill", "screenshot") if clients else (),
            ("dom", "accessibility_tree") if clients else (),
            ("chromium", "firefox"),
            ("no browser client is installed" if not clients else "attachment requires an explicitly authorized debug endpoint",),
            ("computer.observe", "computer.input"),
            ("dom_state", "accessibility_state", "download_exists"),
        )


class VisualPerceptionProvider(Protocol):
    def inspect(self, image_path: str) -> dict[str, Any]: ...


class UnavailableVisualProvider:
    def inspect(self, image_path: str) -> dict[str, Any]:
        return {"status": "unavailable", "reason": "no compatible local vision provider configured", "image": image_path}


class InteractionStrategyEngine:
    def __init__(self, backends: list[InteractionBackend]): self.backends = backends
    def inventory(self) -> list[InteractionBackendInfo]: return [backend.describe() for backend in self.backends]
    def choose(self, operation: str, application_type: str = "native_windows", evidence: dict | None = None) -> StrategyDecision:
        application_type = "native_windows" if application_type == "native" else application_type
        evidence = evidence or {}
        available = [item for item in self.inventory() if item.available and operation in item.operations and application_type in item.application_types]
        if not (evidence.get("fresh_window") and evidence.get("geometry")):
            available = [item for item in available if item.name != "grounded_input"]
        # Semantic evidence wins; grounded input is only eligible when a fresh
        # authorized window and geometry are explicitly present.
        if evidence.get("semantic_control"):
            available.sort(key=lambda item: {"uia": 0, "browser": 0, "win32": 2, "grounded_input": 3}.get(item.name, 9))
        elif evidence.get("fresh_window") and evidence.get("geometry"):
            available.sort(key=lambda item: {"win32": 0, "grounded_input": 1, "uia": 2}.get(item.name, 9))
        if not available:
            return StrategyDecision(None, "no available backend has the requested operation and evidence", (), tuple(self.inventory()))
        selected = available[0]
        reason = "selected from current backend capabilities and observation evidence"
        proof = tuple(str(value) for value in evidence.values()) if evidence else ()
        return StrategyDecision(selected.name, reason, proof, tuple(item for item in self.inventory() if item.name != selected.name))
