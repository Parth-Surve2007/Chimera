"""Backend-neutral computer session, observation, and profile contracts."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from .computer_use import WindowsComputerUse
from .interaction import (BrowserBackendDescriptor, GroundedInputBackend,
                          InteractionStrategyEngine, UIABackendDescriptor,
                          Win32BackendDescriptor)

@dataclass(frozen=True)
class ApplicationIdentity:
    pid: int
    executable: str | None
    session_id: str

@dataclass(frozen=True)
class ElementReference:
    reference_id: str
    session_id: str
    hwnd: int
    runtime_id: str
    generation: int
    expires_at: float

@dataclass(frozen=True)
class ComputerObservation:
    observation_id: str
    session_id: str
    generation: int
    created_at: float
    backend: str
    application: dict
    windows: list[dict]
    elements: list[dict]
    screenshot: str | None = None
    limitation: str | None = None

@dataclass(frozen=True)
class BackendChoice:
    name: str
    available: bool
    reason: str

class ProfileStore:
    """Validated declarative profiles; profiles never grant permissions."""
    def __init__(self, path: Path): self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
    def load(self) -> list[dict]:
        if not self.path.is_file(): return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, list): raise ValueError("application profiles must be an array")
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("profile_id"), str) or not isinstance(item.get("version"), int) or not isinstance(item.get("match"), dict): raise ValueError("profile schema is invalid")
        return data
    def save(self, profiles: list[dict]):
        for item in profiles:
            if not isinstance(item, dict) or not isinstance(item.get("profile_id"), str) or not isinstance(item.get("version"), int) or not isinstance(item.get("match"), dict): raise ValueError("profile schema is invalid")
        self.path.write_text(json.dumps(profiles, indent=2), encoding="utf-8")

class ComputerBackendSelector:
    def choices(self) -> list[BackendChoice]:
        try:
            from pywinauto import Desktop
            windows = Desktop(backend="uia").windows()
            return [BackendChoice("uia", True, f"UI Automation query succeeded ({len(windows)} windows)"), BackendChoice("win32", True, "Win32 fallback available")]
        except ImportError:
            return [BackendChoice("uia", False, "no compatible UI Automation Python binding is installed"), BackendChoice("win32", True, "Win32 fallback available")]
        except Exception as exc:
            return [BackendChoice("uia", False, f"UI Automation query failed: {exc}"), BackendChoice("win32", True, "Win32 fallback available")]
    def select(self) -> BackendChoice:
        return next(choice for choice in self.choices() if choice.available)

class WindowsUIABackend:
    def __init__(self):
        from pywinauto import Desktop
        self.desktop = Desktop(backend="uia")
    def _info(self, item, parent=None):
        try: rect = item.rectangle(); bounds = {"left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom}
        except Exception: bounds = None
        info = item.element_info
        try: patterns = [str(pattern) for pattern in item.get_supported_patterns()]
        except Exception: patterns = []
        def safe_bool(name, default=False):
            try:
                value = getattr(item, name)
                return bool(value() if callable(value) else value)
            except Exception: return default
        try: name = item.window_text()
        except Exception: name = ""
        return {"backend": "uia", "runtime_id": str(getattr(info, "runtime_id", None)), "parent": parent, "name": name, "automation_id": info.automation_id, "class_name": info.class_name, "control_type": info.control_type, "process_id": info.process_id, "rectangle": bounds, "enabled": safe_bool("is_enabled"), "offscreen": safe_bool("is_offscreen"), "focused": safe_bool("has_focus"), "patterns": patterns}
    def elements(self, hwnd: int, depth=3, limit=250):
        root = self.desktop.window(handle=hwnd); result=[]; pending=[(root,0)]
        while pending and len(result)<limit:
            item, level=pending.pop(0); result.append(self._info(item, hwnd))
            if level < min(max(depth,0),8):
                try: pending.extend((child, level+1) for child in item.children()[:limit-len(result)])
                except Exception: pass
        return result
    def _find(self, hwnd: int, runtime_id: str):
        root = self.desktop.window(handle=hwnd); pending=[(root,0)]
        while pending:
            item, level=pending.pop(0)
            if str(getattr(item.element_info, "runtime_id", None)) == runtime_id: return item
            if level < 8:
                try: pending.extend((child, level+1) for child in item.children()[:250])
                except Exception: pass
        return None
    def action(self, hwnd: int, runtime_id: str, operation: str, value=None):
        item = self._find(hwnd, runtime_id)
        if item is None: return {"status": "failed", "verified": False, "reason": "element reference is stale or inaccessible", "backend": "uia"}
        before = self._info(item, hwnd); method = {"invoke_element": "invoke", "set_value": "set_edit_text", "toggle": "toggle", "select_item": "select", "expand": "expand", "collapse": "collapse", "focus_element": "set_focus"}.get(operation)
        if method is None and operation in {"read_text", "get_value"}:
            return {"status": "completed", "verified": True, "backend": "uia", "operation": operation, "before": before, "value": item.window_text()}
        if method is None: return {"status": "failed", "verified": False, "backend": "uia", "reason": f"unsupported semantic operation: {operation}"}
        if not hasattr(item, method): return {"status": "failed", "verified": False, "backend": "uia", "reason": f"required UIA pattern is unavailable: {operation}", "before": before}
        try:
            getattr(item, method)(value) if operation == "set_value" else getattr(item, method)()
            after = self._info(item, hwnd)
            observed_value = item.window_text()
            verified = (
                observed_value == value if operation == "set_value"
                else after.get("focused", False) if operation == "focus_element"
                else False if operation == "invoke_element"
                else True
            )
            return {"status": "completed" if verified else "uncertain", "verified": verified, "backend": "uia", "operation": operation, "before": before, "after": after, "value": observed_value}
        except Exception as exc: return {"status": "failed", "verified": False, "backend": "uia", "reason": str(exc), "before": before}

class ComputerSessionManager:
    def __init__(self, workspace: Path | None = None, observation_ttl: float = 30.0):
        self.backend = WindowsComputerUse(workspace); self.selector = ComputerBackendSelector(); self.observation_ttl = max(.5, observation_ttl); self.sessions = {}; self.observations = {}; self.references = {}
        try: self.uia = WindowsUIABackend() if self.selector.select().name == "uia" else None
        except Exception: self.uia = None
        self.strategy = InteractionStrategyEngine([
            UIABackendDescriptor(self),
            BrowserBackendDescriptor(),
            Win32BackendDescriptor(self),
            GroundedInputBackend(self),
        ])

    def interaction_inventory(self) -> list[dict]:
        """Return backend capabilities without implying that any action succeeded."""
        from dataclasses import asdict
        return [asdict(item) for item in self.strategy.inventory()]

    def choose_interaction(self, operation: str, application_type: str = "native_windows", evidence: dict | None = None) -> dict:
        from dataclasses import asdict
        return asdict(self.strategy.choose(operation, application_type, evidence))
    def create(self, pid: int, executable: str | None = None) -> ApplicationIdentity:
        identity = ApplicationIdentity(pid, executable or self.backend.process_path(pid), uuid.uuid4().hex); self.sessions[identity.session_id] = identity; return identity
    def observe(self, session_id: str) -> ComputerObservation:
        identity = self.sessions.get(session_id)
        if not identity: raise KeyError("computer session not found")
        windows = [item for item in self.backend.list_applications() if item["pid"] == identity.pid]
        if not windows: raise RuntimeError("authorized application is no longer running")
        generation = self.observations.get(session_id, (0, None))[0] + 1; app = windows[0]; elements = []
        if self.uia:
            try: elements = self.uia.elements(app["windows"][0]["hwnd"])
            except Exception: self.uia = None
        if not elements:
            for window in app["windows"][:8]: elements.extend(self.backend.controls(window["hwnd"])[:100])
        backend = "uia" if self.uia else "win32"
        if self.uia:
            enriched = []
            for element in elements:
                runtime_id = element.get("runtime_id")
                if not runtime_id: continue
                reference = ElementReference(uuid.uuid4().hex, session_id, element.get("parent") or app["windows"][0]["hwnd"], runtime_id, generation, time.time() + self.observation_ttl)
                self.references[reference.reference_id] = reference
                item = dict(element); item["reference_id"] = reference.reference_id; item["session_id"] = session_id; item["observation_generation"] = generation
                item["available_actions"] = self._available_actions(item); enriched.append(item)
            elements = enriched
        observation = ComputerObservation(uuid.uuid4().hex, session_id, generation, time.time(), backend, app, app["windows"], elements, limitation=None if backend == "uia" else "UIA unavailable or provider query failed; Win32 fallback used")
        self.observations[session_id] = (generation, observation)
        return observation
    def element_reference(self, session_id: str, hwnd: int) -> ElementReference:
        generation, observation = self.observations.get(session_id, (0, None))
        if observation is None: raise ValueError("fresh observation required")
        if not any(window.get("hwnd") == hwnd for window in observation.windows): raise ValueError("window was not present in the observation")
        runtime_id = next((item.get("runtime_id", "") for item in observation.elements if item.get("parent") == hwnd), "")
        return ElementReference(uuid.uuid4().hex, session_id, hwnd, runtime_id, generation, time.time() + self.observation_ttl)

    @staticmethod
    def _available_actions(element):
        patterns = " ".join(element.get("patterns", [])).lower()
        actions = ["read_text", "focus_element"]
        if "invoke" in patterns: actions.append("invoke_element")
        if "value" in patterns: actions.append("set_value")
        if "toggle" in patterns: actions.append("toggle_element")
        if "selection" in patterns: actions.append("select_element")
        if "expand" in patterns: actions.extend(["expand_element", "collapse_element"])
        return actions

    def act_reference(self, session_id: str, reference_id: str, operation: str, value=None, approved=False):
        reference = self.references.get(reference_id)
        if not reference or reference.session_id != session_id: return {"status": "failed", "verified": False, "reason": "unknown or cross-session reference"}
        return self.act(reference, operation, value, approved)
    def validate_reference(self, reference: ElementReference) -> None:
        generation, observation = self.observations.get(reference.session_id, (0, None))
        if observation is None or generation != reference.generation or time.time() > reference.expires_at: raise ValueError("stale or expired computer element reference")
        if not any(window.get("hwnd") == reference.hwnd for window in observation.windows): raise ValueError("target window identity changed")
    def act(self, reference: ElementReference, operation: str, value=None, approved: bool = False) -> dict:
        self.validate_reference(reference)
        if operation in {"set_value", "toggle", "select_item", "expand", "collapse", "invoke_element"} and not approved:
            return {"status": "awaiting_approval", "operation": operation, "reference_id": reference.reference_id}
        if not self.uia: return {"status": "blocked", "verified": False, "reason": "UI Automation backend is unavailable"}
        result = self.uia.action(reference.hwnd, reference.runtime_id, operation, value)
        try: self.observe(reference.session_id)
        except Exception: pass
        result["reference_id"] = reference.reference_id; result["observation_generation"] = self.observations.get(reference.session_id, (0,))[0]
        return result
