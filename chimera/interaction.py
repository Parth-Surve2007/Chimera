"""Evidence-based computer interaction capabilities.

Only exact UI Automation patterns on fresh references may mutate a desktop.
Global keyboard and mouse injection are intentionally unavailable.
"""
from __future__ import annotations
import importlib.util
from dataclasses import dataclass
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
    def __init__(self, manager=None):
        self.manager = manager

    def describe(self):
        available = bool(self.manager and getattr(self.manager, "uia", None))
        return InteractionBackendInfo(
            "uia", available,
            ("read_text", "invoke_element", "set_value", "toggle", "select_item", "expand", "collapse"),
            ("uia_tree", "control_patterns", "process_identity", "fresh_reference"),
            ("native_windows",),
            () if available else ("UI Automation provider unavailable",),
            ("computer.observe", "computer.input"),
            ("fresh_reference", "process_identity", "post_action_observation"),
        )


class Win32BackendDescriptor:
    def __init__(self, manager=None):
        self.manager = manager

    def describe(self):
        return InteractionBackendInfo(
            "win32", True,
            ("inspect_controls", "screenshot"),
            ("window_identity", "window_geometry"),
            ("native_windows",),
            ("read-only observation fallback; no keyboard or mouse injection",),
            ("computer.observe", "computer.screenshot"),
            ("window_process_identity", "fresh_geometry"),
        )


class GroundedInputBackend:
    """Disabled compatibility descriptor for the removed global-input backend."""
    def __init__(self, manager):
        self.manager = manager

    def describe(self):
        return InteractionBackendInfo(
            "grounded_input", False, (), (),
            ("native_windows", "browser_windows"),
            ("global keyboard and mouse injection is disabled",), (), (),
        )

    def send_keys(self, session_id: str, keys: str, approved: bool = False) -> dict:
        return {
            "status": "blocked",
            "reason": "global keyboard injection is disabled; use an exact UI Automation ValuePattern or manual operator entry",
            "manual_handoff_required": True,
        }


class BrowserBackendDescriptor:
    """Capability inventory only; browser attachment still requires explicit setup."""
    def describe(self):
        clients = tuple(
            name for name in ("playwright", "selenium", "pychrome")
            if importlib.util.find_spec(name)
        )
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
    def __init__(self, backends: list[InteractionBackend]):
        self.backends = backends

    def inventory(self) -> list[InteractionBackendInfo]:
        return [backend.describe() for backend in self.backends]

    def choose(self, operation: str, application_type: str = "native_windows", evidence: dict | None = None) -> StrategyDecision:
        application_type = "native_windows" if application_type == "native" else application_type
        evidence = evidence or {}
        available = [
            item for item in self.inventory()
            if item.available and operation in item.operations and application_type in item.application_types
        ]
        if not available:
            return StrategyDecision(None, "no available backend has the requested operation and evidence", (), tuple(self.inventory()))
        available.sort(key=lambda item: {"uia": 0 if evidence.get("semantic_control") else 1, "browser": 0, "win32": 2}.get(item.name, 9))
        selected = available[0]
        proof = tuple(str(value) for value in evidence.values()) if evidence else ()
        return StrategyDecision(
            selected.name,
            "selected from current backend capabilities and observation evidence",
            proof,
            tuple(item for item in self.inventory() if item.name != selected.name),
        )
