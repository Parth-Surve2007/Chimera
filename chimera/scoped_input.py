"""Foreground-scoped Windows input for fresh, authorized UIA targets.

This is deliberately not a general desktop macro engine. Every action is bound
to a live authorized process, an observed UIA element rectangle, an approval,
and a foreground-owner check before each input packet. There is no clipboard,
shell, global hook, or background-window injection path.
"""
from __future__ import annotations

import ctypes
import hashlib
import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol


MAX_STROKE_POINTS = 64
MAX_TEXT_CHARS = 4000
MAX_DURATION_MS = 2000
MAX_SCROLL_STEPS = 8
_VALID_ACTIONS = {"click", "double_click", "stroke", "type_text", "press_key", "scroll"}
_VALID_BUTTONS = {"left", "right", "middle"}
_KEY_CODES = {
    "ENTER": 0x0D, "TAB": 0x09, "ESC": 0x1B, "ESCAPE": 0x1B,
    "BACKSPACE": 0x08, "DELETE": 0x2E, "INSERT": 0x2D,
    "HOME": 0x24, "END": 0x23, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "SPACE": 0x20, "CTRL": 0x11, "CONTROL": 0x11, "SHIFT": 0x10,
    "ALT": 0x12,
}
_KEY_CODES.update({chr(code): code for code in range(ord("A"), ord("Z") + 1)})
_KEY_CODES.update({str(code): 0x30 + code for code in range(10)})
_KEY_CODES.update({f"F{n}": 0x70 + n - 1 for n in range(1, 13)})


class InputNative(Protocol):
    def window_exists(self, hwnd: int) -> bool: ...
    def window_pid(self, hwnd: int) -> int | None: ...
    def focus_window(self, hwnd: int) -> None: ...
    def foreground_pid(self) -> int | None: ...
    def foreground_hwnd(self) -> int | None: ...
    def send_mouse_move(self, x: int, y: int) -> None: ...
    def send_mouse_button(self, button: str, down: bool) -> None: ...
    def send_mouse_wheel(self, delta: int) -> None: ...
    def send_unicode(self, text: str) -> None: ...
    def send_virtual_key(self, key: int, down: bool) -> None: ...
    def capture(self, rect: tuple[int, int, int, int]) -> Any | None: ...


class Win32InputNative:
    """Thin Win32 SendInput adapter. Instantiated only on Windows."""

    def __init__(self):
        if os.name != "nt":
            raise OSError("scoped Windows input is available only on Windows")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._define_structures()
        # Explicit Win32 prototypes prevent 64-bit HWND truncation and ensure
        # SendInput receives the correct INPUT-array pointer and structure size.
        self.user32.SendInput.argtypes = [
            ctypes.c_uint, ctypes.POINTER(self.INPUT), ctypes.c_int,
        ]
        self.user32.SendInput.restype = ctypes.c_uint
        self.user32.GetForegroundWindow.argtypes = []
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
        ]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user32.SetForegroundWindow.restype = wintypes.BOOL
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.ShowWindow.restype = wintypes.BOOL
        self.user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        self.user32.GetSystemMetrics.restype = ctypes.c_int

    @staticmethod
    def _define_structures():
        ULONG_PTR = ctypes.c_size_t

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ULONG_PTR),
            ]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ULONG_PTR),
            ]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_ushort), ("wParamH", ctypes.c_ushort)]

        class INPUT_UNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("union",)
            _fields_ = [("type", ctypes.c_ulong), ("union", INPUT_UNION)]

        Win32InputNative.MOUSEINPUT = MOUSEINPUT
        Win32InputNative.KEYBDINPUT = KEYBDINPUT
        Win32InputNative.INPUT = INPUT

    def _send(self, item):
        array = (self.INPUT * 1)(item)
        sent = self.user32.SendInput(1, ctypes.byref(array), ctypes.sizeof(self.INPUT))
        if sent != 1:
            raise OSError(ctypes.get_last_error(), "Windows rejected a scoped input event")

    def _mouse(self, flags: int, x: int = 0, y: int = 0, data: int = 0):
        item = self.INPUT()
        item.type = 0
        item.mi = self.MOUSEINPUT(x, y, data, flags, 0, 0)
        self._send(item)

    def _key(self, vk: int = 0, scan: int = 0, flags: int = 0):
        item = self.INPUT()
        item.type = 1
        item.ki = self.KEYBDINPUT(vk, scan, flags, 0, 0)
        self._send(item)

    def window_exists(self, hwnd: int) -> bool:
        return bool(self.user32.IsWindow(ctypes.c_void_p(hwnd)))

    def window_pid(self, hwnd: int) -> int | None:
        pid = ctypes.c_ulong()
        self.user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        return int(pid.value) if pid.value else None

    def foreground_hwnd(self) -> int | None:
        hwnd = self.user32.GetForegroundWindow()
        return int(hwnd) if hwnd else None

    def foreground_pid(self) -> int | None:
        hwnd = self.foreground_hwnd()
        return self.window_pid(hwnd) if hwnd else None

    def focus_window(self, hwnd: int) -> None:
        # Focusing is itself part of the approved action. We never attach input
        # queues or bypass Windows' foreground restrictions.
        SW_RESTORE = 9
        self.user32.ShowWindow(ctypes.c_void_p(hwnd), SW_RESTORE)
        if not self.user32.SetForegroundWindow(ctypes.c_void_p(hwnd)):
            raise PermissionError("Windows did not permit the authorized application to become foreground")

    def _absolute(self, x: int, y: int) -> tuple[int, int]:
        SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
        SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
        left = self.user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        top = self.user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        width = max(1, self.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        height = max(1, self.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        nx = round((x - left) * 65535 / max(1, width - 1))
        ny = round((y - top) * 65535 / max(1, height - 1))
        return max(0, min(65535, nx)), max(0, min(65535, ny))

    def send_mouse_move(self, x: int, y: int) -> None:
        nx, ny = self._absolute(x, y)
        self._mouse(0x0001 | 0x8000 | 0x4000, nx, ny)  # MOVE | ABSOLUTE | VIRTUALDESK

    def send_mouse_button(self, button: str, down: bool) -> None:
        flags = {
            "left": (0x0002, 0x0004),
            "right": (0x0008, 0x0010),
            "middle": (0x0020, 0x0040),
        }[button]
        self._mouse(flags[0] if down else flags[1])

    def send_mouse_wheel(self, delta: int) -> None:
        self._mouse(0x0800, data=delta * 120)

    def send_unicode(self, text: str) -> None:
        for character in text:
            code = ord(character)
            if code <= 0xFFFF:
                self._key(scan=code, flags=0x0004)
                self._key(scan=code, flags=0x0004 | 0x0002)
            else:
                # UTF-16 surrogate pairs are sent as Unicode packets.
                code -= 0x10000
                for unit in (0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)):
                    self._key(scan=unit, flags=0x0004)
                    self._key(scan=unit, flags=0x0004 | 0x0002)

    def send_virtual_key(self, key: int, down: bool) -> None:
        self._key(vk=key, flags=0 if down else 0x0002)

    def capture(self, rect: tuple[int, int, int, int]):
        try:
            from PIL import ImageGrab
            return ImageGrab.grab(bbox=rect, all_screens=True)
        except Exception:
            return None


@dataclass(frozen=True)
class ScopedInputResult:
    status: str
    accepted: bool
    verified: bool
    reason: str | None = None
    action: str | None = None
    foreground_pid: int | None = None
    target_rect: dict | None = None
    before_sha256: str | None = None
    after_sha256: str | None = None
    changed_fraction: float | None = None
    events_sent: int = 0

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "accepted": self.accepted,
            "verified": self.verified,
            "reason": self.reason,
            "action": self.action,
            "foreground_pid": self.foreground_pid,
            "target_rect": self.target_rect,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "changed_fraction": self.changed_fraction,
            "events_sent": self.events_sent,
            "backend": "scoped_win32_input",
        }


class ScopedWindowsInputBackend:
    """Execute a short action only inside the approved foreground application.

    Targets are normalized to the current bounding rectangle of a freshly
    observed UIA element. Every input packet rechecks foreground process
    ownership. A focus change aborts the remaining sequence immediately.
    """

    def __init__(self, native: InputNative | None = None, lock: threading.RLock | None = None):
        self.native = native
        self.lock = lock or threading.RLock()

    @staticmethod
    def _rect(value: dict) -> tuple[int, int, int, int]:
        if not isinstance(value, dict):
            raise ValueError("fresh UI Automation element rectangle is required")
        try:
            left, top = int(value["left"]), int(value["top"])
            right, bottom = int(value["right"]), int(value["bottom"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("UI Automation element rectangle is invalid") from exc
        width, height = right - left, bottom - top
        if width < 8 or height < 8 or width > 12000 or height > 12000:
            raise ValueError("target element rectangle is outside safe size bounds")
        return left, top, right, bottom

    @staticmethod
    def _points(value: Any, minimum: int, maximum: int = MAX_STROKE_POINTS) -> list[tuple[float, float]]:
        if not isinstance(value, list) or not minimum <= len(value) <= maximum:
            raise ValueError(f"points must contain between {minimum} and {maximum} normalized coordinates")
        result = []
        for point in value:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("each point must be an [x, y] pair")
            x, y = point
            if (
                isinstance(x, bool) or isinstance(y, bool)
                or not isinstance(x, (int, float)) or not isinstance(y, (int, float))
                or not math.isfinite(float(x)) or not math.isfinite(float(y))
                or not 0.02 <= float(x) <= 0.98 or not 0.02 <= float(y) <= 0.98
            ):
                raise ValueError("normalized coordinates must be finite and between 0.02 and 0.98")
            result.append((float(x), float(y)))
        return result

    @staticmethod
    def _key_chord(value: str) -> list[int]:
        if not isinstance(value, str) or not value.strip() or len(value) > 48:
            raise ValueError("key must be a short named key or chord")
        parts = [part.strip().upper() for part in value.split("+")]
        if not 1 <= len(parts) <= 4 or any(part not in _KEY_CODES for part in parts):
            raise ValueError("key chord contains an unsupported key")
        modifiers = {"CTRL", "CONTROL", "SHIFT", "ALT"}
        if len(parts) > 1 and any(part not in modifiers for part in parts[:-1]):
            raise ValueError("only CTRL, SHIFT and ALT may precede a chord key")
        return [_KEY_CODES[part] for part in parts]

    @staticmethod
    def _image_evidence(image) -> tuple[str | None, bytes | None]:
        if image is None:
            return None, None
        try:
            payload = image.convert("RGB").tobytes()
            return hashlib.sha256(payload).hexdigest(), payload
        except Exception:
            return None, None

    @staticmethod
    def _changed_fraction(before: bytes | None, after: bytes | None) -> float | None:
        if before is None or after is None or len(before) != len(after) or not before:
            return None
        # Sample every fourth byte to keep verification bounded for 4K windows.
        left, right = before[::4], after[::4]
        changed = sum(a != b for a, b in zip(left, right))
        return changed / len(left)

    def _native(self) -> InputNative:
        if self.native is None:
            self.native = Win32InputNative()
        return self.native

    def execute(
        self,
        *,
        hwnd: int,
        expected_pid: int,
        element_pid: int,
        rectangle: dict,
        action: str,
        points: Any = None,
        button: str = "left",
        duration_ms: int = 250,
        text: str | None = None,
        key: str | None = None,
        scroll_direction: str = "down",
        scroll_steps: int = 3,
        approved: bool = False,
    ) -> dict:
        if not approved:
            return ScopedInputResult("awaiting_approval", False, False, "operator approval is required", action).to_dict()
        if os.name != "nt" and self.native is None:
            return ScopedInputResult("unavailable", False, False, "Windows input is unavailable on this platform", action).to_dict()
        if action not in _VALID_ACTIONS:
            return ScopedInputResult("blocked", False, False, "unsupported visual action", action).to_dict()
        if type(hwnd) is not int or hwnd <= 0 or type(expected_pid) is not int or expected_pid <= 0:
            return ScopedInputResult("blocked", False, False, "target window and process identity are invalid", action).to_dict()
        if element_pid != expected_pid:
            return ScopedInputResult("blocked", False, False, "observed element belongs to a different process", action).to_dict()
        if action not in {"type_text", "press_key"} and button not in _VALID_BUTTONS:
            return ScopedInputResult("blocked", False, False, "mouse button is unsupported", action).to_dict()
        if type(duration_ms) is not int or not 0 <= duration_ms <= MAX_DURATION_MS:
            return ScopedInputResult("blocked", False, False, "duration_ms must be between 0 and 2000", action).to_dict()

        try:
            rect = self._rect(rectangle)
            bounds = {"left": rect[0], "top": rect[1], "right": rect[2], "bottom": rect[3]}
            if action in {"click", "double_click", "scroll"}:
                normalized = self._points(points, 1, 1)
            elif action == "stroke":
                normalized = self._points(points, 2, MAX_STROKE_POINTS)
            else:
                normalized = []
            if action == "type_text" and (not isinstance(text, str) or not text or len(text) > MAX_TEXT_CHARS):
                raise ValueError(f"text must contain 1 to {MAX_TEXT_CHARS} characters")
            chord = self._key_chord(key) if action == "press_key" else []
            if action == "scroll":
                if scroll_direction not in {"up", "down"}:
                    raise ValueError("scroll direction must be up or down")
                if type(scroll_steps) is not int or not 1 <= scroll_steps <= MAX_SCROLL_STEPS:
                    raise ValueError(f"scroll steps must be an integer from 1 to {MAX_SCROLL_STEPS}")
        except ValueError as exc:
            return ScopedInputResult("blocked", False, False, str(exc), action).to_dict()

        native = self._native()
        left, top, right, bottom = rect
        mapped = [
            (round(left + x * (right - left)), round(top + y * (bottom - top)))
            for x, y in normalized
        ]
        with self.lock:
            if not native.window_exists(hwnd) or native.window_pid(hwnd) != expected_pid:
                return ScopedInputResult("blocked", False, False, "HWND no longer belongs to the authorized process", action).to_dict()
            try:
                if native.foreground_hwnd() != hwnd or native.foreground_pid() != expected_pid:
                    native.focus_window(hwnd)
            except Exception as exc:
                return ScopedInputResult("awaiting_operator_focus", False, False, str(exc), action).to_dict()
            if native.foreground_hwnd() != hwnd or native.foreground_pid() != expected_pid:
                return ScopedInputResult("awaiting_operator_focus", False, False, "exact authorized window is not foreground; activate it and retry", action, native.foreground_pid()).to_dict()

            before_image = native.capture(rect)
            before_hash, before_payload = self._image_evidence(before_image)
            events = 0
            button_down = False

            def guard() -> None:
                if native.foreground_hwnd() != hwnd or native.foreground_pid() != expected_pid:
                    raise PermissionError("foreground window changed; remaining input was aborted")
                if not native.window_exists(hwnd) or native.window_pid(hwnd) != expected_pid:
                    raise PermissionError("authorized HWND changed during input; remaining input was aborted")

            def move(point):
                nonlocal events
                guard()
                native.send_mouse_move(point[0], point[1])
                events += 1

            def down():
                nonlocal events, button_down
                guard()
                native.send_mouse_button(button, True)
                events += 1
                button_down = True

            def up():
                nonlocal events, button_down
                if button_down:
                    native.send_mouse_button(button, False)
                    events += 1
                    button_down = False

            try:
                if action in {"click", "double_click"}:
                    move(mapped[0])
                    repeats = 2 if action == "double_click" else 1
                    for index in range(repeats):
                        down()
                        up()
                        if repeats == 2 and index == 0:
                            time.sleep(0.06)
                elif action == "stroke":
                    move(mapped[0])
                    down()
                    interval = min(0.02, (duration_ms / 1000) / max(1, len(mapped) - 1))
                    for point in mapped[1:]:
                        move(point)
                        if interval:
                            time.sleep(interval)
                    up()
                elif action == "scroll":
                    move(mapped[0])
                    guard()
                    native.send_mouse_wheel(scroll_steps if scroll_direction == "down" else -scroll_steps)
                    events += 1
                elif action == "type_text":
                    # The observed target element is clicked only after process
                    # validation and approval; text goes to that foreground app.
                    center = ((left + right) // 2, (top + bottom) // 2)
                    move(center)
                    down()
                    up()
                    guard()
                    native.send_unicode(text)
                    events += len(text) * 2
                elif action == "press_key":
                    guard()
                    for vk in chord:
                        native.send_virtual_key(vk, True)
                        events += 1
                    for vk in reversed(chord):
                        native.send_virtual_key(vk, False)
                        events += 1
            except Exception as exc:
                try:
                    up()
                except Exception:
                    button_down = False
                return ScopedInputResult(
                    "blocked" if isinstance(exc, PermissionError) else "failed",
                    events > 0, False, str(exc), action,
                    native.foreground_pid(), bounds, before_hash, None, None, events,
                ).to_dict()
            finally:
                if button_down:
                    try:
                        up()
                    except Exception:
                        pass

            time.sleep(0.04)
            after_image = native.capture(rect)
            after_hash, after_payload = self._image_evidence(after_image)
            changed = self._changed_fraction(before_payload, after_payload)
            verified = changed is not None and changed > 0.00001
            reason = (
                "input was accepted and the target region changed"
                if verified else
                "input was accepted but a visual change could not be verified; inspect a fresh UIA observation"
            )
            return ScopedInputResult(
                "completed", True, verified, reason, action,
                native.foreground_pid(), bounds, before_hash, after_hash, changed, events,
            ).to_dict()
