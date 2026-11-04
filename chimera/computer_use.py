"""Bounded Windows desktop observation and interaction.

This module deliberately uses observed HWNDs and an executable allowlist. It
does not provide shell execution, arbitrary coordinates, or background input
to an unobserved window.
"""
from __future__ import annotations

import ctypes
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

    def launch(self, executable: str, approved: bool = False) -> dict:
        path = self._resolve_executable(executable)
        if path is None:
            return {
                "status": "blocked",
                "reason": f"could not resolve an installed application executable for {executable!r}",
            }
        if path.suffix.casefold() != ".exe":
            return {"status": "blocked", "reason": "only executable applications may be launched"}
        if not approved:
            return {
                "status": "awaiting_approval",
                "application": str(path),
                "requested_application": str(executable),
                "permission": "computer.launch",
            }
        process = subprocess.Popen([str(path)], shell=False)
        self._processes[process.pid] = process
        expected_path = os.path.normcase(os.path.normpath(str(path))).casefold()
        expected_name = path.name.casefold()
        # Modern Windows apps (notably packaged Notepad) can hand a launch
        # request to an already-running process. The PID returned by Popen may
        # exit immediately while the visible window belongs to that process.
        known_app_images = {
            "winword.exe", "excel.exe", "powerpnt.exe", "outlook.exe",
            "notepad.exe", "notepad++.exe", "calc.exe", "mspaint.exe",
            "blender.exe", "chrome.exe", "msedge.exe", "firefox.exe", "code.exe",
        }
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            matched = []
            for item in self._windows():
                observed_path = self.process_path(item.pid)
                if not observed_path:
                    continue
                normalized = os.path.normcase(os.path.normpath(str(observed_path))).casefold()
                image_name = str(observed_path).replace("\\\\", "/").rsplit("/", 1)[-1].casefold()
                if normalized == expected_path or (
                    expected_name in known_app_images and image_name == expected_name
                ):
                    matched.append(item)
            if matched:
                return {
                    "status": "completed",
                    "pid": matched[0].pid,
                    "spawned_pid": process.pid,
                    "executable": str(path),
                    "windows": [asdict(item) for item in matched[:5]],
                    "verification": "visible_window_executable_match",
                }
            time.sleep(.05)
        return {
            "status": "failed",
            "pid": process.pid,
            "reason": (
                "application process was started, but no visible window with a "
                "matching executable identity appeared within 5 seconds"
            ),
            "executable": str(path),
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

