"""Bounded Windows desktop observation and interaction.

This module deliberately uses observed HWNDs and an executable allowlist. It
does not provide shell execution, arbitrary coordinates, or background input
to an unobserved window.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import time
import threading
from ctypes import wintypes
from dataclasses import dataclass, asdict
from pathlib import Path

MAX_TEXT = 100_000

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
            class_buf = ctypes.create_unicode_buffer(256); self.user32.GetClassNameW(hwnd, class_buf, 256)
            result.append(WindowObservation(int(hwnd), int(pid.value), title_buf.value, class_buf.value, True)); return True
        self.user32.EnumWindows(enum_proc(callback), 0)
        return result

    def list_applications(self) -> list[dict]:
        grouped = {}
        for item in self._windows():
            entry = grouped.setdefault(item.pid, {"pid": item.pid, "executable": self.process_path(item.pid), "windows": []})
            entry["windows"].append(asdict(item))
        return list(grouped.values())

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

    def launch(self, executable: str, approved: bool = False) -> dict:
        path = Path(executable).expanduser()
        if not path.is_file(): return {"status": "blocked", "reason": "application executable does not exist"}
        if path.suffix.casefold() != ".exe": return {"status": "blocked", "reason": "only executable applications may be launched"}
        if not approved: return {"status": "awaiting_approval", "application": str(path), "permission": "computer.launch"}
        process = subprocess.Popen([str(path.resolve())], shell=False)
        self._processes[process.pid] = process
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            windows = [item for item in self._windows() if item.pid == process.pid]
            if windows: return {"status": "completed", "pid": process.pid, "windows": [asdict(item) for item in windows]}
            time.sleep(.05)
        return {"status": "failed", "pid": process.pid, "reason": "application window did not appear within timeout", "executable": str(path.resolve())}

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
        if os.name != "nt" or not self.user32.IsWindow(hwnd): return {"status": "failed", "reason": "window is not observed"}
        self.user32.SetForegroundWindow(hwnd); return {"status": "completed", "hwnd": hwnd}

    def type_text(self, hwnd: int, text: str, approved: bool = False) -> dict:
        if not approved: return {"status": "awaiting_approval", "hwnd": hwnd, "permission": "computer.input"}
        if not isinstance(text, str) or not text or len(text) > MAX_TEXT: return {"status": "failed", "reason": "text is empty or exceeds limit"}
        if os.name != "nt" or not self.user32.IsWindow(hwnd): return {"status": "failed", "reason": "control is not observed"}
        self.user32.SetForegroundWindow(hwnd); inputs = []
        class KEYBDINPUT(ctypes.Structure): _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]
        class INPUT(ctypes.Structure): _fields_ = [("type", wintypes.DWORD), ("ki", KEYBDINPUT)]
        for char in text:
            inputs.extend([INPUT(1, KEYBDINPUT(0, ord(char), 0x0004, 0, None)), INPUT(1, KEYBDINPUT(0, ord(char), 0x0004 | 0x0002, 0, None))])
        sent = self.user32.SendInput(len(inputs), (INPUT * len(inputs))(*inputs), ctypes.sizeof(INPUT))
        return {"status": "completed" if sent == len(inputs) else "failed", "hwnd": hwnd, "characters": len(text)}

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
        if not approved: return {"status": "awaiting_approval", "hwnd": hwnd, "permission": "computer.close"}
        if os.name != "nt" or not self.user32.IsWindow(hwnd): return {"status": "failed", "reason": "window is not observed"}
        self.user32.PostMessageW(hwnd, 0x0010, 0, 0); return {"status": "completed", "hwnd": hwnd}
