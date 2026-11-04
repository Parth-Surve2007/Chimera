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
from .scoped_input import ScopedWindowsInputBackend

@dataclass(frozen=True)
class ApplicationIdentity:
    pid: int
    executable: str
    process_create_time: float
    owner_session_id: str | None
    session_id: str
    authorized_hwnds: tuple[int, ...] = ()

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
        runtime_id = getattr(info, "runtime_id", None)
        return {"backend": "uia", "runtime_id": str(runtime_id) if runtime_id is not None else None, "parent": parent, "name": name, "automation_id": info.automation_id, "class_name": info.class_name, "control_type": info.control_type, "process_id": info.process_id, "rectangle": bounds, "enabled": safe_bool("is_enabled"), "offscreen": safe_bool("is_offscreen"), "focused": safe_bool("has_focus"), "patterns": patterns}
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

    def visual_target(self, hwnd: int, runtime_id: str, expected_pid: int) -> dict:
        """Resolve fresh element bounds and reject cross-process/offscreen targets."""
        item = self._find(hwnd, runtime_id)
        if item is None:
            raise ValueError("visual target is stale or no longer exposed by UI Automation")
        info = self._info(item, hwnd)
        if int(info.get("process_id") or 0) != int(expected_pid):
            raise PermissionError("visual target belongs to a different process")
        rectangle = info.get("rectangle")
        if not isinstance(rectangle, dict):
            raise ValueError("visual target does not expose a usable bounding rectangle")
        if not info.get("enabled", False) or info.get("offscreen", False):
            raise ValueError("visual target is disabled or offscreen")
        return {
            "rectangle": rectangle,
            "control_type": info.get("control_type"),
            "patterns": info.get("patterns", []),
            "name": info.get("name", ""),
            "process_id": int(info.get("process_id") or 0),
        }

    def focus_element(self, hwnd: int, runtime_id: str, expected_pid: int) -> dict:
        """Focus one exact observed UIA element after the containing app is foreground."""
        item = self._find(hwnd, runtime_id)
        if item is None:
            return {"status": "failed", "reason": "target element is stale"}
        if int(getattr(item.element_info, "process_id", 0) or 0) != int(expected_pid):
            return {"status": "blocked", "reason": "target element belongs to a different process"}
        try:
            item.set_focus()
            focused = bool(item.has_focus())
            return {
                "status": "completed" if focused else "uncertain",
                "verified": focused,
                "focused": focused,
                "element": self._info(item, hwnd),
            }
        except Exception as exc:
            return {"status": "failed", "reason": f"UI Automation could not focus the target element: {exc}"}
    def action(
        self,
        hwnd: int,
        runtime_id: str,
        operation: str,
        value=None,
        expected_pid: int | None = None,
        options: dict | None = None,
    ):
        """Execute one operation through a freshly resolved UI Automation pattern."""
        item = self._find(hwnd, runtime_id)
        if item is None:
            return {
                "status": "failed", "verified": False,
                "reason": "element reference is stale or inaccessible", "backend": "uia",
            }
        before = self._info(item, hwnd)
        options = options or {}
        try:
            element_pid = int(getattr(item.element_info, "process_id", 0) or 0)
            if expected_pid is not None and element_pid != int(expected_pid):
                return {
                    "status": "blocked", "verified": False,
                    "reason": "UI Automation element belongs to a different process",
                    "backend": "uia", "before": before,
                }

            if operation == "read_text":
                observed = ""
                source = "Name"
                try:
                    text_range = item.iface_text.DocumentRange
                    observed = str(text_range.GetText(12000) or "")
                    source = "TextPattern"
                except Exception:
                    pass
                if not observed:
                    try:
                        observed = str(item.window_text() or "")
                        source = "WindowText"
                    except Exception:
                        pass
                if not observed:
                    try:
                        observed = str(item.iface_value.CurrentValue or "")
                        source = "ValuePattern"
                    except Exception:
                        pass
                if not observed:
                    observed = str(before.get("name") or "")
                return {
                    "status": "completed", "verified": True, "backend": "uia",
                    "operation": operation, "before": before, "value": observed[:12000],
                    "source": source, "truncated": len(observed) > 12000,
                }

            if operation == "get_range_value":
                pattern = item.iface_range_value
                return {
                    "status": "completed", "verified": True, "backend": "uia",
                    "operation": operation, "before": before,
                    "value": float(pattern.CurrentValue),
                    "minimum": float(pattern.CurrentMinimum),
                    "maximum": float(pattern.CurrentMaximum),
                    "small_change": float(pattern.CurrentSmallChange),
                    "large_change": float(pattern.CurrentLargeChange),
                    "read_only": bool(pattern.CurrentIsReadOnly),
                }

            if operation == "set_range_value":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return {
                        "status": "blocked", "verified": False,
                        "reason": "range value must be a finite number", "backend": "uia", "before": before,
                    }
                import math
                numeric = float(value)
                if not math.isfinite(numeric):
                    return {
                        "status": "blocked", "verified": False,
                        "reason": "range value must be a finite number", "backend": "uia", "before": before,
                    }
                pattern = item.iface_range_value
                minimum, maximum = float(pattern.CurrentMinimum), float(pattern.CurrentMaximum)
                if bool(pattern.CurrentIsReadOnly):
                    return {
                        "status": "blocked", "verified": False,
                        "reason": "selected range control is read-only", "backend": "uia", "before": before,
                    }
                if numeric < minimum or numeric > maximum:
                    return {
                        "status": "blocked", "verified": False,
                        "reason": f"range value must be between {minimum} and {maximum}",
                        "backend": "uia", "before": before,
                    }
                pattern.SetValue(numeric)
                observed = float(pattern.CurrentValue)
                verified = abs(observed - numeric) <= max(1e-9, abs(numeric) * 1e-9)
                return {
                    "status": "completed" if verified else "uncertain",
                    "verified": verified, "backend": "uia", "operation": operation,
                    "before": before, "after": self._info(item, hwnd), "value": observed,
                    "minimum": minimum, "maximum": maximum,
                }

            if operation == "read_grid":
                grid = item.iface_grid
                row_count, column_count = int(grid.CurrentRowCount), int(grid.CurrentColumnCount)
                max_rows, max_columns = 20, 10
                rows = []
                for row_index in range(min(row_count, max_rows)):
                    row = []
                    for column_index in range(min(column_count, max_columns)):
                        cell = grid.GetItem(row_index, column_index)
                        cell_text = ""
                        try:
                            cell_text = str(cell.CurrentName or "")
                        except Exception:
                            try:
                                cell_text = str(cell.CurrentValue or "")
                            except Exception:
                                cell_text = ""
                        row.append(cell_text[:500])
                    rows.append(row)
                return {
                    "status": "completed", "verified": True, "backend": "uia",
                    "operation": operation, "before": before, "rows": rows,
                    "row_count": row_count, "column_count": column_count,
                    "truncated": row_count > max_rows or column_count > max_columns,
                }

            if operation == "invoke_element":
                item.iface_invoke.Invoke()
                return {
                    "status": "completed", "verified": False,
                    "verification": "InvokePattern was accepted; inspect a fresh observation to verify the resulting UI state",
                    "backend": "uia", "operation": operation, "before": before,
                }

            if operation == "set_value":
                pattern = item.iface_value
                if bool(pattern.CurrentIsReadOnly):
                    return {
                        "status": "blocked", "verified": False,
                        "reason": "selected UI Automation control is read-only", "backend": "uia", "before": before,
                    }
                pattern.SetValue(value)
                observed = str(pattern.CurrentValue)
                verified = observed == value
            elif operation == "toggle":
                pattern = item.iface_toggle
                before_state = pattern.CurrentToggleState
                pattern.Toggle()
                observed = int(pattern.CurrentToggleState)
                verified = observed != int(before_state)
            elif operation == "select_item":
                pattern = item.iface_selection_item
                pattern.Select()
                observed = bool(pattern.CurrentIsSelected)
                verified = observed
            elif operation in {"expand", "collapse"}:
                pattern = item.iface_expand_collapse
                (pattern.Expand if operation == "expand" else pattern.Collapse)()
                observed = int(pattern.CurrentExpandCollapseState)
                verified = observed == (1 if operation == "expand" else 0)
            elif operation == "scroll":
                direction = options.get("direction")
                amount = options.get("amount", "line")
                count = options.get("count", 1)
                if direction not in {"up", "down", "left", "right"} or amount not in {"line", "page"}:
                    return {
                        "status": "blocked", "verified": False,
                        "reason": "scroll direction must be up/down/left/right and amount must be line/page",
                        "backend": "uia", "before": before,
                    }
                if type(count) is not int or not 1 <= count <= 5:
                    return {
                        "status": "blocked", "verified": False,
                        "reason": "scroll count must be an integer from 1 to 5",
                        "backend": "uia", "before": before,
                    }
                pattern = item.iface_scroll
                if direction in {"up", "down"} and not bool(pattern.CurrentVerticallyScrollable):
                    return {
                        "status": "blocked", "verified": False,
                        "reason": "element is not vertically scrollable", "backend": "uia", "before": before,
                    }
                if direction in {"left", "right"} and not bool(pattern.CurrentHorizontallyScrollable):
                    return {
                        "status": "blocked", "verified": False,
                        "reason": "element is not horizontally scrollable", "backend": "uia", "before": before,
                    }
                before_position = {
                    "horizontal": float(pattern.CurrentHorizontalScrollPercent),
                    "vertical": float(pattern.CurrentVerticalScrollPercent),
                }
                item.scroll(direction, amount, count=count, retry_interval=0)
                after_position = {
                    "horizontal": float(pattern.CurrentHorizontalScrollPercent),
                    "vertical": float(pattern.CurrentVerticalScrollPercent),
                }
                verified = before_position != after_position
                return {
                    "status": "completed", "verified": verified, "backend": "uia",
                    "operation": operation, "before": before, "after": self._info(item, hwnd),
                    "scroll_before": before_position, "scroll_after": after_position,
                    "note": None if verified else "ScrollPattern accepted the request but the reported position did not change; the control may be at a boundary",
                }
            elif operation == "scroll_into_view":
                item.iface_scroll_item.ScrollIntoView()
                after = self._info(item, hwnd)
                verified = not bool(after.get("offscreen"))
                return {
                    "status": "completed", "verified": verified, "backend": "uia",
                    "operation": operation, "before": before, "after": after,
                    "note": None if verified else "ScrollIntoView was accepted; a fresh observation is required to confirm visibility",
                }
            else:
                return {
                    "status": "blocked", "verified": False,
                    "reason": f"unsupported UI Automation pattern: {operation}",
                    "backend": "uia", "before": before,
                }

            return {
                "status": "completed" if verified else "uncertain",
                "verified": verified, "backend": "uia", "operation": operation,
                "before": before, "after": self._info(item, hwnd), "value": observed,
            }
        except Exception as exc:
            return {
                "status": "failed", "verified": False, "backend": "uia",
                "reason": str(exc), "before": before,
            }



class ComputerSessionManager:
    def __init__(self, workspace: Path | None = None, observation_ttl: float = 30.0):
        self.backend = WindowsComputerUse(workspace); self.selector = ComputerBackendSelector(); self.observation_ttl = max(.5, observation_ttl); self.sessions = {}; self.observations = {}; self.references = {}
        try: self.uia = WindowsUIABackend() if self.selector.select().name == "uia" else None
        except Exception: self.uia = None
        self.visual_input = ScopedWindowsInputBackend()
        try:
            from .vision import LocalVisionProvider
            self.visual_perception = LocalVisionProvider.from_env()
        except Exception:
            self.visual_perception = None
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
    def create(self, pid: int, executable: str | None = None, owner_session_id: str | None = None, authorized_windows: list[dict] | None = None, expected_application_name: str | None = None) -> ApplicationIdentity:
        """Authorize one live process, optionally restricted to launch-observed HWNDs.

        A per-user WindowsApps execution alias can resolve to a different final
        image path. That exception requires the WindowsApps path shape, exact
        visible HWNDs still owned by the verified PID, and either a matching
        executable basename or matching app title/class evidence. Later
        observations and actions are restricted to that HWND set.
        """
        try:
            import psutil
            process = psutil.Process(int(pid))
            created = float(process.create_time())
            actual = str(Path(process.exe()).resolve())
        except Exception as exc:
            raise RuntimeError(f"application process identity could not be verified: {exc}") from exc
        if not actual:
            raise RuntimeError("application executable path is unavailable")

        allowed_hwnds: tuple[int, ...] = ()
        if authorized_windows is not None:
            verified_hwnds = []
            for item in authorized_windows:
                if not isinstance(item, dict):
                    continue
                hwnd, observed_pid = item.get("hwnd"), item.get("pid")
                if type(hwnd) is not int or hwnd <= 0 or observed_pid != int(pid):
                    continue
                if self.backend.window_process_id(hwnd) == int(pid):
                    verified_hwnds.append(hwnd)
            allowed_hwnds = tuple(dict.fromkeys(verified_hwnds))
            if not allowed_hwnds:
                raise RuntimeError("launch returned no visible window that still belongs to the verified process")

        if executable:
            expected = str(Path(executable).resolve())
            if expected.casefold() != actual.casefold():
                expected_parts = [part.casefold() for part in Path(expected).parts]
                alias_indexes = [
                    index for index, part in enumerate(expected_parts)
                    if part == "appdata"
                ]
                is_windows_apps_alias = any(
                    expected_parts[index:index + 4]
                    == ["appdata", "local", "microsoft", "windowsapps"]
                    for index in alias_indexes
                )
                display_key = "".join(
                    character for character in str(expected_application_name or "").casefold()
                    if character.isalnum()
                )
                launch_window_title_match = bool(
                    display_key
                    and any(
                        display_key in "".join(
                            character for character in (
                                str(item.get("title") or "") + " " + str(item.get("class_name") or "")
                            ).casefold()
                            if character.isalnum()
                        )
                        for item in (authorized_windows or [])
                        if isinstance(item, dict) and item.get("hwnd") in allowed_hwnds
                    )
                )
                alias_exception = (
                    is_windows_apps_alias
                    and bool(allowed_hwnds)
                    and (
                        Path(expected).name.casefold() == Path(actual).name.casefold()
                        or launch_window_title_match
                    )
                )
                if not alias_exception:
                    raise RuntimeError(
                        "application executable changed before authorization "
                        f"(requested {Path(expected).name}, observed {Path(actual).name})"
                    )

        try:
            current = psutil.Process(int(pid))
            if abs(float(current.create_time()) - created) > 0.01:
                raise RuntimeError("application PID was reused during authorization")
            current_executable = str(Path(current.exe()).resolve())
            if current_executable.casefold() != actual.casefold():
                raise RuntimeError("application executable changed during authorization")
        except Exception as exc:
            raise RuntimeError(f"application process changed during authorization: {exc}") from exc

        identity = ApplicationIdentity(
            int(pid), actual, created, owner_session_id, uuid.uuid4().hex, allowed_hwnds
        )
        self.sessions[identity.session_id] = identity
        return identity

    @staticmethod
    def _identity_is_current(identity: ApplicationIdentity) -> bool:
        try:
            import psutil
            process = psutil.Process(identity.pid)
            if abs(float(process.create_time()) - identity.process_create_time) > 0.01:
                return False
            return str(Path(process.exe()).resolve()).casefold() == identity.executable.casefold()
        except Exception:
            return False

    def validate_window(self, session_id: str, hwnd: int) -> ApplicationIdentity:
        identity = self.sessions.get(session_id)
        if identity is None or not self._identity_is_current(identity):
            raise ValueError("authorized process identity is missing or has changed")
        _generation, observation = self.observations.get(session_id, (0, None))
        if observation is None or time.time() - observation.created_at > self.observation_ttl:
            raise ValueError("fresh authorized window observation required")
        if identity.authorized_hwnds and int(hwnd) not in identity.authorized_hwnds:
            raise ValueError("window is outside the launch-approved HWND set")
        if not any(int(window.get("hwnd", 0)) == int(hwnd) and int(window.get("pid", 0)) == identity.pid for window in observation.windows):
            raise ValueError("window does not belong to the exact authorized process")
        if self.backend.window_process_id(int(hwnd)) != identity.pid:
            raise ValueError("current HWND owner does not match the authorized process")
        return identity

    def close_window(self, session_id: str, hwnd: int, approved: bool = False) -> dict:
        """Gracefully close one freshly observed window owned by this conversation."""
        if not approved:
            return {
                "status": "awaiting_approval", "verified": False,
                "session_id": session_id, "hwnd": int(hwnd),
                "permission": "computer.close",
            }
        try:
            identity = self.validate_window(session_id, int(hwnd))
        except (KeyError, ValueError, RuntimeError) as exc:
            return {"status": "blocked", "verified": False, "reason": str(exc)}
        return self.backend.close(int(hwnd), approved=True, expected_pid=identity.pid)

    def observe(
        self,
        session_id: str,
        hwnd: int | None = None,
        depth: int = 3,
        limit: int = 250,
    ) -> ComputerObservation:
        """Inspect one authorized top-level window using bounded UIA traversal."""
        identity = self.sessions.get(session_id)
        if not identity:
            raise KeyError("computer session not found")
        if not self._identity_is_current(identity):
            raise RuntimeError("authorized process identity is no longer valid")
        if type(depth) is not int or not 0 <= depth <= 8:
            raise ValueError("UI Automation depth must be an integer from 0 to 8")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("UI Automation element limit must be an integer from 1 to 500")

        expected_executable = str(Path(identity.executable).expanduser().resolve()).casefold()
        applications = [
            item for item in self.backend.list_applications()
            if item["pid"] == identity.pid
            and bool(item.get("executable"))
            and str(Path(item.get("executable")).expanduser().resolve()).casefold() == expected_executable
        ]
        if not applications:
            raise RuntimeError("authorized application process is no longer visible")
        app = dict(applications[0])
        available_windows = [
            window for window in app.get("windows", [])
            if isinstance(window, dict)
            and int(window.get("pid", 0)) == identity.pid
            and (
                not identity.authorized_hwnds
                or int(window.get("hwnd", 0)) in identity.authorized_hwnds
            )
        ]
        if not available_windows:
            raise RuntimeError("authorized application window is no longer available")

        if hwnd is not None:
            if type(hwnd) is not int or hwnd <= 0:
                raise ValueError("hwnd must be a positive integer")
            if identity.authorized_hwnds and hwnd not in identity.authorized_hwnds:
                raise PermissionError("window is outside the launch-approved HWND set")
            selected = next(
                (window for window in available_windows if int(window.get("hwnd", 0)) == hwnd),
                None,
            )
            if selected is None or self.backend.window_process_id(hwnd) != identity.pid:
                raise PermissionError("selected window does not belong to the exact authorized process")
            app["windows"] = [selected]
        else:
            app["windows"] = available_windows

        target_hwnd = int(app["windows"][0]["hwnd"])
        generation = self.observations.get(session_id, (0, None))[0] + 1
        uia_elements = []
        uia_error = None
        if self.uia:
            try:
                uia_elements = self.uia.elements(target_hwnd, depth=depth, limit=limit)
            except Exception as exc:
                # Provider failures are window/app-specific. Preserve the
                # backend for other sessions and retry it on later observations.
                uia_error = str(exc)[:240]
                uia_elements = []

        # A successful provider query returning no usable runtime IDs is not
        # a semantic observation. Fall back to read-only Win32 controls rather
        # than labelling an empty Win32 tree as UIA.
        uia_elements = [
            item for item in uia_elements
            if isinstance(item, dict) and item.get("runtime_id")
        ]
        backend = "uia" if self.uia is not None and uia_elements else "win32"
        elements = []
        if backend == "uia":
            for element in uia_elements:
                reference = ElementReference(
                    uuid.uuid4().hex,
                    session_id,
                    int(element.get("parent") or target_hwnd),
                    str(element["runtime_id"]),
                    generation,
                    time.time() + self.observation_ttl,
                )
                self.references[reference.reference_id] = reference
                item = dict(element)
                item["reference_id"] = reference.reference_id
                item["session_id"] = session_id
                item["observation_generation"] = generation
                item["available_actions"] = self._available_actions(item)
                elements.append(item)
            if not elements:
                backend = "win32"
        if backend == "win32":
            elements = self.backend.controls(target_hwnd)[:min(limit, 250)]

        observation = ComputerObservation(
            uuid.uuid4().hex,
            session_id,
            generation,
            time.time(),
            backend,
            app,
            app["windows"],
            elements,
            limitation=(
                None if backend == "uia" else
                "UIA unavailable or returned no usable elements; Win32 fallback used"
                + (f" (UIA error: {uia_error})" if uia_error else "")
            ),
        )
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
        patterns = " ".join(str(value) for value in element.get("patterns", [])).casefold()
        actions = ["read_text"]
        if "invoke" in patterns:
            actions.append("invoke_element")
        if "value" in patterns:
            actions.append("set_value")
        if "rangevalue" in patterns:
            actions.extend(["get_range_value", "set_range_value"])
        if "toggle" in patterns:
            actions.append("toggle_element")
        if "selectionitem" in patterns:
            actions.append("select_element")
        if "expandcollapse" in patterns:
            actions.extend(["expand_element", "collapse_element"])
        if "grid" in patterns:
            actions.append("read_grid")
        if "scrollitem" in patterns:
            actions.append("scroll_into_view")
        if "scroll" in patterns:
            actions.append("scroll_element")
        if element.get("rectangle") and not element.get("offscreen", False):
            actions.append("visual_action")
        return actions

    def inspect_visual_target(
        self,
        session_id: str,
        reference_id: str,
        question: str,
        *,
        approved: bool = False,
        cancel_event=None,
    ) -> dict:
        """Inspect a fresh visible element with an explicitly configured local vision model."""
        if not approved:
            return {
                "status": "awaiting_approval",
                "verified": False,
                "permission": "computer.screenshot",
                "reason": "operator approval is required before capturing application pixels",
            }
        if self.visual_perception is None:
            return {
                "status": "unavailable",
                "verified": False,
                "reason": (
                    "no local vision model is configured; set CHIMERA_VISION_MODEL_PATH and "
                    "CHIMERA_VISION_SERVER to user-supplied local assets"
                ),
                "hosted_fallback": False,
            }
        reference = self.references.get(reference_id)
        if reference is None or reference.session_id != session_id:
            return {
                "status": "blocked", "verified": False,
                "reason": "unknown or cross-session visual target reference",
            }
        try:
            self.validate_reference(reference)
            if self.uia is None:
                raise RuntimeError("UI Automation is unavailable; screenshot target cannot be grounded")
            identity = self.sessions.get(session_id)
            if identity is None:
                raise RuntimeError("authorized application session no longer exists")
            target = self.uia.visual_target(
                reference.hwnd, reference.runtime_id, identity.pid
            )
            rectangle = target["rectangle"]
            window_rectangle = self.backend.window_rect(reference.hwnd)
            if not isinstance(window_rectangle, dict):
                raise RuntimeError("authorized application window geometry is unavailable")
            if not (
                window_rectangle["left"] <= rectangle["left"] < rectangle["right"] <= window_rectangle["right"]
                and window_rectangle["top"] <= rectangle["top"] < rectangle["bottom"] <= window_rectangle["bottom"]
            ):
                raise PermissionError("visual target is outside the exact authorized window")
            native = self.visual_input._native()
            if not native.window_exists(reference.hwnd) or native.window_pid(reference.hwnd) != identity.pid:
                raise PermissionError("authorized application window identity changed")
            if native.foreground_hwnd() != reference.hwnd or native.foreground_pid() != identity.pid:
                return {
                    "status": "awaiting_operator_focus", "verified": False,
                    "reason": "bring the exact authorized application window to the foreground before screenshot analysis",
                    "hwnd": reference.hwnd, "pid": identity.pid,
                }
            image = native.capture((
                int(rectangle["left"]), int(rectangle["top"]),
                int(rectangle["right"]), int(rectangle["bottom"]),
            ))
            if image is None:
                raise RuntimeError("Windows could not capture the target element pixels")
            if native.foreground_hwnd() != reference.hwnd or native.foreground_pid() != identity.pid:
                raise PermissionError("foreground application changed during screenshot capture")
            result = self.visual_perception.inspect_image(
                image, question, cancel_event=cancel_event
            )
            return {
                **result,
                "session_id": session_id,
                "reference_id": reference_id,
                "hwnd": reference.hwnd,
                "target_name": target.get("name"),
                "target_control_type": target.get("control_type"),
                "target_rectangle": rectangle,
                "evidence_scope": "fresh_uia_target_inside_authorized_window",
                "verified": False,
                "note": "Visual descriptions are observations, not proof that an action succeeded.",
            }
        except Exception as exc:
            return {
                "status": "failed", "verified": False,
                "reason": f"visual target inspection failed: {exc}",
                "hosted_fallback": False,
            }

    def act_reference(
        self, session_id: str, reference_id: str, operation: str,
        value=None, approved=False, options: dict | None = None,
    ):
        reference = self.references.get(reference_id)
        if not reference or reference.session_id != session_id:
            return {"status": "failed", "verified": False, "reason": "unknown or cross-session reference"}
        aliases = {
            "toggle_element": "toggle",
            "select_element": "select_item",
            "expand_element": "expand",
            "collapse_element": "collapse",
            "scroll_element": "scroll",
        }
        return self.act(reference, aliases.get(operation, operation), value, approved, options)

    def act(
        self,
        reference: ElementReference,
        operation: str,
        value=None,
        approved: bool = False,
        options: dict | None = None,
    ) -> dict:
        """Validate exact process/window/reference and govern UIA side effects."""
        read_only = {"read_text", "get_range_value", "read_grid"}
        state_changing = {
            "invoke_element", "set_value", "set_range_value", "toggle",
            "select_item", "expand", "collapse", "scroll", "scroll_into_view",
            "visual_action",
        }
        if operation not in read_only | state_changing:
            return {"status": "blocked", "verified": False, "reason": f"unsupported desktop action: {operation}"}
        try:
            self.validate_reference(reference)
        except (KeyError, ValueError, RuntimeError) as exc:
            return {"status": "blocked", "verified": False, "reason": str(exc), "backend": "uia"}
        if operation in state_changing and not approved:
            return {
                "status": "awaiting_approval", "verified": False, "backend": "uia",
                "permission": "computer.input", "operation": operation,
                "reference_id": reference.reference_id,
                "reason": "operator approval is required before changing application state",
            }
        if self.uia is None:
            return {
                "status": "blocked", "verified": False, "backend": "uia",
                "reason": "UI Automation is unavailable; no input or Win32 mutation fallback is enabled",
            }
        identity = self.sessions.get(reference.session_id)
        if identity is None:
            return {"status": "blocked", "verified": False, "reason": "authorized application session no longer exists"}
        if operation == "visual_action":
            try:
                target = self.uia.visual_target(reference.hwnd, reference.runtime_id, identity.pid)
                window_rectangle = self.backend.window_rect(reference.hwnd)
                if not isinstance(window_rectangle, dict):
                    return {
                        "status": "blocked", "verified": False,
                        "reason": "live target window geometry is unavailable",
                        "backend": "scoped_win32_input",
                    }
                target_rectangle = target["rectangle"]
                if not (
                    window_rectangle["left"] <= target_rectangle["left"] < target_rectangle["right"] <= window_rectangle["right"]
                    and window_rectangle["top"] <= target_rectangle["top"] < target_rectangle["bottom"] <= window_rectangle["bottom"]
                ):
                    return {
                        "status": "blocked", "verified": False,
                        "reason": "UI Automation target rectangle is not fully contained by the authorized HWND",
                        "backend": "scoped_win32_input",
                    }
                strategy = self.choose_interaction(
                    "visual_action",
                    "native_windows",
                    evidence={"fresh_window": True, "geometry": True},
                )
                if strategy.get("selected") != "grounded_input":
                    return {
                        "status": "blocked", "verified": False,
                        "backend": "strategy",
                        "reason": strategy.get("reason"),
                        "selected_backend": strategy.get("selected"),
                    }
                action = (options or {}).get("action")
                if action == "type_text":
                    control_type = str(target.get("control_type") or "").casefold()
                    patterns = " ".join(str(item) for item in target.get("patterns", [])).casefold()
                    if control_type not in {"edit", "document", "combobox"} and "value" not in patterns and "text" not in patterns:
                        return {
                            "status": "blocked", "verified": False,
                            "reason": "type_text is limited to observed editable UI Automation controls",
                            "backend": "scoped_win32_input",
                        }
                native = self.visual_input._native()
                if native.foreground_hwnd() != reference.hwnd or native.foreground_pid() != identity.pid:
                    try:
                        native.focus_window(reference.hwnd)
                    except Exception as focus_exc:
                        return {
                            "status": "awaiting_operator_focus", "verified": False,
                            "reason": str(focus_exc), "backend": "scoped_win32_input",
                        }
                if native.foreground_hwnd() != reference.hwnd or native.foreground_pid() != identity.pid:
                    return {
                        "status": "awaiting_operator_focus", "verified": False,
                        "reason": "the exact authorized application window could not be made foreground",
                        "backend": "scoped_win32_input",
                    }
                if action in {"type_text", "press_key"}:
                    focus_result = self.uia.focus_element(reference.hwnd, reference.runtime_id, identity.pid)
                    if focus_result.get("status") != "completed":
                        return {
                            "status": "blocked", "verified": False,
                            "reason": focus_result.get("reason") or "UI Automation could not focus the observed target",
                            "backend": "uia",
                        }
                input_result = self.visual_input.execute(
                    hwnd=reference.hwnd,
                    expected_pid=identity.pid,
                    element_pid=target["process_id"],
                    rectangle=target["rectangle"],
                    action=action,
                    points=(options or {}).get("points"),
                    strokes=(options or {}).get("strokes"),
                    button=(options or {}).get("button", "left"),
                    duration_ms=(options or {}).get("duration_ms", 250),
                    text=(options or {}).get("text"),
                    key=(options or {}).get("key"),
                    scroll_direction=(options or {}).get("direction", "down"),
                    scroll_steps=(options or {}).get("steps", 3),
                    approved=True,
                )
                return {**input_result, "strategy": strategy.get("selected")}
            except Exception as exc:
                return {"status": "failed", "verified": False, "backend": "scoped_win32_input", "reason": str(exc)}
        strategy = self.choose_interaction(
            operation,
            "native_windows",
            evidence={"fresh_window": True, "geometry": True, "semantic_control": True},
        )
        if strategy.get("selected") != "uia":
            return {
                "status": "blocked", "verified": False,
                "backend": "strategy",
                "reason": strategy.reason,
                "selected_backend": strategy.get("selected"),
            }
        try:
            result = self.uia.action(
                reference.hwnd, reference.runtime_id, operation, value,
                expected_pid=identity.pid, options=options or {},
            )
        except Exception as exc:
            return {"status": "failed", "verified": False, "backend": "uia", "reason": str(exc)}
        return {
            **result,
            "session_id": reference.session_id,
            "reference_id": reference.reference_id,
            "hwnd": reference.hwnd,
            "strategy": strategy.get("selected"),
        }

    def validate_reference(self, reference: ElementReference) -> None:
        generation, observation = self.observations.get(reference.session_id, (0, None))
        if observation is None or generation != reference.generation or time.time() > reference.expires_at:
            raise ValueError("stale or expired computer element reference")
        if not any(int(window.get("hwnd", 0)) == int(reference.hwnd) for window in observation.windows):
            raise ValueError("target window is no longer in the authorized observation")
        identity = self.sessions.get(reference.session_id)
        if identity is None or not self._identity_is_current(identity):
            raise ValueError("authorized process identity changed")
        if self.backend.window_process_id(reference.hwnd) != identity.pid:
            raise ValueError("target HWND no longer belongs to the authorized process")

