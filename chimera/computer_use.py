"""Bounded Windows desktop observation and interaction.

This module deliberately uses observed HWNDs and an executable allowlist. It
does not provide shell execution, arbitrary coordinates, or background input
to an unobserved window.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import time
import threading
from ctypes import wintypes
from dataclasses import dataclass, asdict
from pathlib import Path

@dataclass(frozen=True)
class WindowObservation:
    hwnd: int
    pid: int
    title: str
    class_name: str
    visible: bool

@dataclass(frozen=True)
class ControlObservation:
    hwnd: int
    parent_hwnd: int
    class_name: str
    text: str
    control_id: int

class WindowsComputerUse:
    """Small Win32 backend with explicit grounding and approval checks."""

    def __init__(self, workspace: Path | None = None):
        self.workspace = Path(workspace).resolve() if workspace else None
        self._processes: dict[int, subprocess.Popen] = {}
        self._authorized: set[str] = set()
        if os.name == "nt":
            self.user32 = ctypes.windll.user32
            self.kernel32 = ctypes.windll.kernel32

    def _windows(self) -> list[WindowObservation]:
        if os.name != "nt": return []
        result = []
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _):
            if not self.user32.IsWindowVisible(hwnd): return True
            pid = wintypes.DWORD(); self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            title_buf = ctypes.create_unicode_buffer(512); self.user32.GetWindowTextW(hwnd, title_buf, 512)
            if not title_buf.value.strip():
                return True
            class_buf = ctypes.create_unicode_buffer(256); self.user32.GetClassNameW(hwnd, class_buf, 256)
            result.append(WindowObservation(int(hwnd), int(pid.value), title_buf.value, class_buf.value, True)); return True
        self.user32.EnumWindows(enum_proc(callback), 0)
        return result


    def list_applications(self) -> list[dict]:
        """Discover a compact inventory of visible, user-facing Windows apps.

        Discovery is live, not a hard-coded application allowlist. Only the
        process identity needed for explicit authorization and a few window
        titles are returned.
        """
        grouped = {}
        for item in self._windows():
            title = str(item.title or "").strip()
            if not title:
                continue
            executable = self.process_path(item.pid)
            if not executable:
                continue
            entry = grouped.setdefault(item.pid, {
                "pid": item.pid, "executable": executable, "windows": [],
            })
            if len(entry["windows"]) < 3:
                entry["windows"].append({
                    "hwnd": item.hwnd, "pid": item.pid, "title": title[:180],
                })
        return list(grouped.values())[:40]

    def list_installed_applications(self) -> list[dict]:
        """List user-launchable Start-menu apps, including packaged Windows apps."""
        return self._start_menu_apps()[:100]

    def process_path(self, pid: int) -> str | None:
        if os.name != "nt": return None
        access = 0x1000 | 0x0400
        handle = self.kernel32.OpenProcess(access, False, pid)
        if not handle: return None
        try:
            buf = ctypes.create_unicode_buffer(1024); size = wintypes.DWORD(len(buf))
            if self.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)): return buf.value
            return None
        finally: self.kernel32.CloseHandle(handle)

    def authorize_application(self, pid: int, session_id: str) -> dict:
        path = self.process_path(pid)
        if not path: return {"status": "failed", "reason": "process identity could not be verified"}
        token = f"{session_id}:{pid}:{path.casefold()}"; self._authorized.add(token)
        return {"status": "completed", "authorization": token, "pid": pid, "executable": path}

    @staticmethod
    def _resolve_executable(application: str) -> Path | None:
        """Resolve a direct .exe path or a known installed Windows app name."""
        raw = str(application or "").strip().strip('"')
        if not raw:
            return None
        direct = Path(raw).expanduser()
        if direct.is_file() and direct.suffix.casefold() == ".exe":
            return direct.resolve()
        # Do not reinterpret a misspelled absolute/relative path as an app name.
        if direct.is_absolute() or any(separator in raw for separator in ("\\", "/", ":")):
            return None

        aliases = {
            "word": "WINWORD.EXE",
            "microsoft word": "WINWORD.EXE",
            "winword": "WINWORD.EXE",
            "excel": "EXCEL.EXE",
            "microsoft excel": "EXCEL.EXE",
            "powerpoint": "POWERPNT.EXE",
            "power point": "POWERPNT.EXE",
            "outlook": "OUTLOOK.EXE",
            "notepad": "notepad.exe",
            "notepad++": "notepad++.exe",
            "calculator": "calc.exe",
            "paint": "mspaint.exe",
            "mspaint": "mspaint.exe",
            "blender": "blender.exe",
            "chrome": "chrome.exe",
            "google chrome": "chrome.exe",
            "edge": "msedge.exe",
            "microsoft edge": "msedge.exe",
            "firefox": "firefox.exe",
            "visual studio code": "code.exe",
            "vscode": "code.exe",
        }
        key = raw.casefold()
        alias_key = key.removesuffix(".exe")
        # Bare executable names are deliberately restricted to known desktop
        # applications. Arbitrary executables remain launchable only via an
        # exact path supplied by the operator and then approved.
        if alias_key not in aliases:
            return None
        executable_names = [aliases[alias_key]]

        for executable_name in dict.fromkeys(executable_names):
            found = shutil.which(executable_name)
            if found and Path(found).is_file():
                return Path(found).resolve()

            if os.name == "nt":
                try:
                    import winreg
                    app_path_key = (
                        "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\"
                        + executable_name
                    )
                    roots = [
                        (winreg.HKEY_CURRENT_USER, 0),
                        (winreg.HKEY_LOCAL_MACHINE, getattr(winreg, "KEY_WOW64_64KEY", 0)),
                        (winreg.HKEY_LOCAL_MACHINE, getattr(winreg, "KEY_WOW64_32KEY", 0)),
                    ]
                    for root, view in roots:
                        try:
                            with winreg.OpenKey(root, app_path_key, 0, winreg.KEY_READ | view) as registry_key:
                                value, _ = winreg.QueryValueEx(registry_key, "")
                            candidate = Path(os.path.expandvars(str(value).strip().strip('"')))
                            if candidate.is_file() and candidate.suffix.casefold() == ".exe":
                                return candidate.resolve()
                        except OSError:
                            continue
                except ImportError:
                    pass

        if os.name == "nt":
            system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
            for root in (system_root / "System32", system_root / "SysWOW64"):
                for executable_name in dict.fromkeys(executable_names):
                    candidate = root / executable_name
                    if candidate.is_file() and candidate.suffix.casefold() == ".exe":
                        return candidate.resolve()

        if os.name == "nt" and executable_names[0].casefold() == "winword.exe":
            roots = [
                Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
                Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
            ]
            for root in roots:
                for office_path in (
                    root / "Microsoft Office" / "root" / "Office16" / "WINWORD.EXE",
                    root / "Microsoft Office" / "Office16" / "WINWORD.EXE",
                ):
                    if office_path.is_file():
                        return office_path.resolve()
        return None

    @staticmethod
    def _normalise_app_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold().removesuffix(".exe"))

    @staticmethod
    def _start_menu_apps() -> list[dict]:
        """Read Start-menu application names and AUMIDs without executing user input."""
        if os.name != "nt":
            return []
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        candidates = [
            Path(shutil.which("powershell.exe") or ""),
            system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        ]
        powershell = next((item for item in candidates if str(item) and item.is_file()), None)
        if powershell is None:
            return []
        # This is a fixed, read-only inventory command. The requested app name
        # is never interpolated into PowerShell or passed to a shell parser.
        script = "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"
        try:
            result = subprocess.run(
                [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=8,
                shell=False,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return []
            payload = json.loads(result.stdout.lstrip("\\ufeff"))
        except (OSError, subprocess.TimeoutExpired, TypeError, ValueError, json.JSONDecodeError):
            return []
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            return []
        entries = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("Name") or "").strip()
            app_id = str(item.get("AppID") or "").strip()
            if (
                name
                and app_id
                and len(app_id) <= 260
                and re.fullmatch(r"[A-Za-z0-9_.-]+![A-Za-z0-9_.-]+", app_id)
            ):
                entries.append({"name": name, "app_id": app_id})
        return entries

    @classmethod
    def _resolve_start_menu_target(cls, application: str) -> dict | None:
        """Resolve a Start-menu entry to a validated AppsFolder activation target."""
        raw = str(application or "").strip().strip('"')
        if not raw or os.name != "nt":
            return None
        requested_aumid = None
        if raw.casefold().startswith("appsfolder:"):
            requested_aumid = raw[len("appsfolder:"):].strip()
            if not re.fullmatch(r"[A-Za-z0-9_.-]+![A-Za-z0-9_.-]+", requested_aumid):
                return None
        aliases = {
            "paint": {"paint", "microsoftpaint", "mspaint"},
            "microsoftpaint": {"paint", "microsoftpaint", "mspaint"},
            "mspaint": {"paint", "microsoftpaint", "mspaint"},
            "windowspaint": {"paint", "microsoftpaint", "mspaint"},
            "calculator": {"calculator", "calc", "windowscalculator"},
            "calc": {"calculator", "calc", "windowscalculator"},
            "notepad": {"notepad", "windowsnotepad"},
            "photos": {"photos", "microsoftphotos"},
            "camera": {"camera", "windowscamera"},
            "snippingtool": {"snippingtool", "snippingtool"},
            "clock": {"clock", "windowsclock", "alarmsclock"},
            "terminal": {"terminal", "windowsterminal"},
            "windowsterminal": {"terminal", "windowsterminal"},
        }
        key = cls._normalise_app_name(raw)
        expected_names = aliases.get(key, {key})
        entries = cls._start_menu_apps()
        match = next(
            (
                item for item in entries
                if (
                    item["app_id"].casefold() == requested_aumid.casefold()
                    if requested_aumid
                    else cls._normalise_app_name(item["name"]) in expected_names
                )
            ),
            None,
        )
        if match is None:
            return None
        name = match["name"]
        process_aliases = {
            "paint": ["mspaint.exe"],
            "microsoftpaint": ["mspaint.exe"],
            "calculator": ["calculator.exe", "calc.exe"],
            "calc": ["calculator.exe", "calc.exe"],
            "notepad": ["notepad.exe"],
            "photos": ["Microsoft.Photos.exe"],
            "camera": ["WindowsCamera.exe"],
            "snippingtool": ["ScreenClippingHost.exe", "SnippingTool.exe"],
            "clock": ["Time.exe"],
            "terminal": ["WindowsTerminal.exe"],
            "windowsterminal": ["WindowsTerminal.exe"],
        }
        normalized_name = cls._normalise_app_name(name)
        expected_processes = process_aliases.get(normalized_name, [])
        return {
            "kind": "appsfolder",
            "application": "appsfolder:" + match["app_id"],
            "aumid": match["app_id"],
            "display_name": name,
            "expected_process_names": expected_processes,
        }

    @classmethod
    def _resolve_launch_target(cls, application: str) -> dict | None:
        raw = str(application or "").strip().strip('"')
        if raw.casefold().startswith("appsfolder:"):
            return cls._resolve_start_menu_target(raw)
        path = cls._resolve_executable(raw)
        if path is not None:
            return {
                "kind": "executable",
                "application": str(path),
                "path": path,
                "display_name": path.stem,
                "expected_process_names": [path.name],
            }
        return cls._resolve_start_menu_target(raw)

    def launch(self, executable: str, approved: bool = False) -> dict:
        target = self._resolve_launch_target(executable)
        if target is None:
            return {
                "status": "blocked",
                "reason": f"could not resolve an installed executable or Start-menu app for {executable!r}",
            }
        launch_kind = target["kind"]
        if launch_kind == "executable":
            path = Path(target["path"])
            if path.suffix.casefold() != ".exe" or not path.is_file():
                return {"status": "blocked", "reason": "resolved application executable is no longer available"}
            application = str(path)
        elif launch_kind == "appsfolder":
            aumid = str(target.get("aumid") or "")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+![A-Za-z0-9_.-]+", aumid):
                return {"status": "blocked", "reason": "resolved Windows application ID is invalid"}
            application = str(target["application"])
        else:
            return {"status": "blocked", "reason": "unsupported Windows application launch target"}

        if not approved:
            return {
                "status": "awaiting_approval",
                "application": application,
                "requested_application": str(executable),
                "display_name": target.get("display_name"),
                "launch_kind": launch_kind,
                "permission": "computer.launch",
            }

        if launch_kind == "executable":
            command = [application]
        else:
            system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
            explorer = system_root / "explorer.exe"
            if not explorer.is_file():
                explorer_value = shutil.which("explorer.exe")
                if not explorer_value:
                    return {"status": "failed", "reason": "Windows Explorer is unavailable for AppsFolder activation"}
                explorer = Path(explorer_value)
            command = [str(explorer), "shell:AppsFolder\\" + str(target["aumid"])]

        process = subprocess.Popen(command, shell=False)
        self._processes[process.pid] = process
        expected_path = (
            os.path.normcase(os.path.normpath(application)).casefold()
            if launch_kind == "executable" else None
        )
        expected_process_names = {
            str(value).replace("\\", "/").rsplit("/", 1)[-1].casefold()
            for value in target.get("expected_process_names", [])
        }
        display_name = str(target.get("display_name") or Path(application).stem)
        normalized_display_name = self._normalise_app_name(display_name)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            matched = []
            for item in self._windows():
                observed_path = self.process_path(item.pid)
                image_name = (
                    str(observed_path).replace("\\", "/").rsplit("/", 1)[-1].casefold()
                    if observed_path else ""
                )
                normalized = (
                    os.path.normcase(os.path.normpath(str(observed_path))).casefold()
                    if observed_path else ""
                )
                process_match = bool(
                    (expected_path and normalized == expected_path)
                    or (image_name and image_name in expected_process_names)
                )
                title_match = bool(
                    normalized_display_name
                    and normalized_display_name in self._normalise_app_name(item.title)
                )
                if process_match or title_match:
                    matched.append((item, process_match))
            if matched:
                matched.sort(key=lambda value: not value[1])
                first, process_match = matched[0]
                return {
                    "status": "completed",
                    "pid": first.pid,
                    "spawned_pid": process.pid,
                    "executable": application if launch_kind == "executable" else None,
                    "application": application,
                    "display_name": display_name,
                    "launch_kind": launch_kind,
                    "windows": [asdict(item) for item, _ in matched[:5]],
                    "verification": (
                        "visible_window_process_match" if process_match
                        else "visible_window_title_match"
                    ),
                }
            time.sleep(.05)
        return {
            "status": "failed",
            "pid": process.pid,
            "reason": (
                f"{display_name} launch was requested, but no matching application window "
                "appeared within 8 seconds"
            ),
            "application": application,
            "launch_kind": launch_kind,
        }

    def window_process_id(self, hwnd: int) -> int | None:
        """Read the current owner PID for one HWND without interacting with it."""
        if os.name != "nt" or not self.user32.IsWindow(hwnd):
            return None
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        return int(pid.value) if pid.value else None

    def controls(self, hwnd: int) -> list[dict]:
        if os.name != "nt" or not self.user32.IsWindow(hwnd): return []
        result = []; enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(child, _):
            title = ctypes.create_unicode_buffer(1024); cls = ctypes.create_unicode_buffer(256)
            self.user32.GetWindowTextW(child, title, 1024); self.user32.GetClassNameW(child, cls, 256)
            result.append(asdict(ControlObservation(int(child), int(hwnd), cls.value, title.value, self.user32.GetDlgCtrlID(child)))); return True
        self.user32.EnumChildWindows(hwnd, enum_proc(callback), 0); return result

    def inspect_elements(self, hwnd: int) -> dict:
        controls = self.controls(hwnd)
        return {"status": "completed", "accessibility": "win32_fallback", "uia_available": False, "limitation": "No UI Automation binding is installed; controls are Win32 child-window observations only.", "elements": controls}

    def focus(self, hwnd: int) -> dict:
        return {"status": "blocked", "reason": "foreground focus changes are disabled", "manual_handoff_required": True}


    def type_text(self, hwnd: int, text: str, approved: bool = False) -> dict:
        return {"status": "blocked", "reason": "global keyboard injection is disabled; use exact UI Automation ValuePattern or manual operator entry", "manual_handoff_required": True}


    def screenshot(self, hwnd: int, output: Path, approved: bool = False) -> dict:
        if not approved: return {"status": "awaiting_approval", "hwnd": hwnd, "permission": "computer.screenshot"}
        if self.workspace is None: return {"status": "blocked", "reason": "workspace is not selected"}
        target = (self.workspace / output).resolve()
        if target != self.workspace and self.workspace not in target.parents: return {"status": "blocked", "reason": "screenshot path escapes workspace"}
        try:
            from PIL import ImageGrab
            rect = wintypes.RECT(); self.user32.GetWindowRect(hwnd, ctypes.byref(rect)); image = ImageGrab.grab((rect.left, rect.top, rect.right, rect.bottom)); target.parent.mkdir(parents=True, exist_ok=True); image.save(target)
            return {"status": "completed", "path": str(target), "width": image.width, "height": image.height}
        except Exception as exc: return {"status": "failed", "reason": str(exc)}

    def close(self, hwnd: int, approved: bool = False) -> dict:
        return {"status": "blocked", "reason": "remote window closing is disabled; close the application directly", "manual_handoff_required": True}

