import pytest

from chimera.scoped_input import ScopedWindowsInputBackend


class FakeImage:
    def __init__(self, payload):
        self.payload = payload

    def convert(self, _mode):
        return self

    def tobytes(self):
        return self.payload


class FakeNative:
    def __init__(self, pid=4321):
        self.pid = pid
        self.foreground = pid
        self.foreground_window = 99
        self.events = []
        self.flip_foreground_after_moves = None
        self.moves = 0

    def window_exists(self, hwnd):
        return hwnd == 99

    def window_pid(self, hwnd):
        return self.pid if hwnd == 99 else None

    def focus_window(self, hwnd):
        self.events.append(("focus", hwnd))
        self.foreground = self.pid
        self.foreground_window = hwnd

    def foreground_pid(self):
        return self.foreground

    def foreground_hwnd(self):
        return self.foreground_window

    def send_mouse_move(self, x, y):
        self.events.append(("move", x, y))
        self.moves += 1
        if self.flip_foreground_after_moves and self.moves >= self.flip_foreground_after_moves:
            self.foreground = 9999
            self.foreground_window = 100

    def send_mouse_button(self, button, down):
        self.events.append(("button", button, down))

    def send_mouse_wheel(self, delta):
        self.events.append(("wheel", delta))

    def send_unicode_unit(self, unit, down):
        self.events.append(("unicode-unit", unit, down))
        if self.flip_foreground_after_moves == "unicode" and down:
            self.foreground = 9999
            self.foreground_window = 100

    def send_virtual_key(self, key, down):
        self.events.append(("key", key, down))
        if self.flip_foreground_after_moves == "key" and down:
            self.foreground = 9999
            self.foreground_window = 100

    def capture(self, _rect):
        return FakeImage(bytes([len(self.events) % 251]) * 128)


RECT = {"left": 100, "top": 200, "right": 500, "bottom": 600}


def test_input_requires_operator_approval_before_any_native_event():
    native = FakeNative()
    backend = ScopedWindowsInputBackend(native=native)

    result = backend.execute(
        hwnd=99, expected_pid=4321, element_pid=4321, rectangle=RECT,
        action="click", points=[[0.5, 0.5]], approved=False,
    )

    assert result["status"] == "awaiting_approval"
    assert result["accepted"] is False
    assert native.events == []


def test_input_rejects_cross_process_hwnd_and_element_before_focus():
    native = FakeNative()
    backend = ScopedWindowsInputBackend(native=native)

    result = backend.execute(
        hwnd=99, expected_pid=4321, element_pid=9999, rectangle=RECT,
        action="click", points=[[0.5, 0.5]], approved=True,
    )

    assert result["status"] == "blocked"
    assert "different process" in result["reason"]
    assert native.events == []


@pytest.mark.parametrize("point", [[-0.1, 0.5], [0.5, 1.1], [float("nan"), 0.2], [0.01, 0.5]])
def test_input_rejects_coordinates_outside_normalized_target(point):
    native = FakeNative()
    backend = ScopedWindowsInputBackend(native=native)

    result = backend.execute(
        hwnd=99, expected_pid=4321, element_pid=4321, rectangle=RECT,
        action="click", points=[point], approved=True,
    )

    assert result["status"] == "blocked"
    assert "normalized" in result["reason"]
    assert not [event for event in native.events if event[0] in {"move", "button"}]


def test_scoped_click_maps_normalized_coordinates_to_observed_element():
    native = FakeNative()
    backend = ScopedWindowsInputBackend(native=native)

    result = backend.execute(
        hwnd=99, expected_pid=4321, element_pid=4321, rectangle=RECT,
        action="click", points=[[0.5, 0.25]], approved=True,
    )

    assert result["status"] == "completed"
    assert result["accepted"] is True
    assert native.events == [
        ("move", 300, 300),
        ("button", "left", True),
        ("button", "left", False),
    ]
    assert result["target_rect"] == RECT
    assert result["events_sent"] == 3


def test_draw_stroke_is_bounded_and_returns_pixel_change_evidence():
    native = FakeNative()
    backend = ScopedWindowsInputBackend(native=native)

    result = backend.execute(
        hwnd=99, expected_pid=4321, element_pid=4321, rectangle=RECT,
        action="stroke", points=[[0.1, 0.1], [0.5, 0.5], [0.9, 0.9]],
        duration_ms=25, approved=True,
    )

    assert result["status"] == "completed"
    assert result["accepted"] is True
    assert result["verified"] is True
    assert result["before_sha256"] != result["after_sha256"]
    assert result["changed_fraction"] > 0
    assert ("button", "left", True) in native.events
    assert ("button", "left", False) in native.events


def test_foreground_change_aborts_remaining_stroke_and_releases_button():
    native = FakeNative()
    native.flip_foreground_after_moves = 2
    backend = ScopedWindowsInputBackend(native=native)

    result = backend.execute(
        hwnd=99, expected_pid=4321, element_pid=4321, rectangle=RECT,
        action="stroke", points=[[0.1, 0.1], [0.5, 0.5], [0.9, 0.9]],
        approved=True,
    )

    assert result["status"] == "blocked"
    assert "foreground window changed" in result["reason"]
    assert native.events[-1] == ("button", "left", False)
    assert len([event for event in native.events if event[0] == "move"]) == 2


def test_keyboard_input_supports_only_bounded_named_chords():
    native = FakeNative()
    backend = ScopedWindowsInputBackend(native=native)

    result = backend.execute(
        hwnd=99, expected_pid=4321, element_pid=4321, rectangle=RECT,
        action="press_key", key="CTRL+S", approved=True,
    )

    assert result["status"] == "completed"
    assert [item for item in native.events if item[0] == "key"] == [
        ("key", 0x11, True), ("key", 0x53, True),
        ("key", 0x53, False), ("key", 0x11, False),
    ]


def test_text_entry_uses_unicode_packets_and_caps_length():
    native = FakeNative()
    backend = ScopedWindowsInputBackend(native=native)

    result = backend.execute(
        hwnd=99, expected_pid=4321, element_pid=4321, rectangle=RECT,
        action="type_text", text="Hello café", approved=True,
    )

    assert result["status"] == "completed"
    expected_units = []
    for character in "Hello café":
        code = ord(character)
        if code <= 0xFFFF:
            expected_units.append(code)
        else:
            code -= 0x10000
            expected_units.extend((0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)))
    assert [
        event for event in native.events if event[0] == "unicode-unit"
    ] == [("unicode-unit", unit, state) for unit in expected_units for state in (True, False)]

    too_long = backend.execute(
        hwnd=99, expected_pid=4321, element_pid=4321, rectangle=RECT,
        action="type_text", text="x" * 4001, approved=True,
    )
    assert too_long["status"] == "blocked"
    assert "4000" in too_long["reason"]


def test_scoped_scroll_is_directional_and_bounded():
    native = FakeNative()
    backend = ScopedWindowsInputBackend(native=native)

    result = backend.execute(
        hwnd=99, expected_pid=4321, element_pid=4321, rectangle=RECT,
        action="scroll", points=[[0.5, 0.5]], scroll_direction="up",
        scroll_steps=2, approved=True,
    )

    assert result["status"] == "completed"
    assert ("wheel", -2) in native.events



def test_text_entry_aborts_between_unicode_packets_after_foreground_changes():
    native = FakeNative()
    native.flip_foreground_after_moves = "unicode"
    backend = ScopedWindowsInputBackend(native=native)

    result = backend.execute(
        hwnd=99, expected_pid=4321, element_pid=4321, rectangle=RECT,
        action="type_text", text="Hello", approved=True,
    )

    assert result["status"] == "blocked"
    assert "foreground window changed" in result["reason"]
    key_packets = [event for event in native.events if event[0] == "unicode-unit"]
    assert len(key_packets) == 2
    assert key_packets[0][2] is True
    assert key_packets[1] == ("unicode-unit", key_packets[0][1], False)



@pytest.mark.parametrize("chord", ["ALT+TAB", "ALT+F4", "CTRL+ESC", "CTRL+ALT+DELETE", "WIN+R"])
def test_system_switching_and_window_close_key_chords_are_rejected(chord):
    from chimera.scoped_input import ScopedWindowsInputBackend

    with pytest.raises(ValueError):
        ScopedWindowsInputBackend._key_chord(chord)


def test_key_chord_foreground_loss_releases_held_modifiers():
    native = FakeNative()
    native.flip_foreground_after_moves = "key"
    backend = ScopedWindowsInputBackend(native=native)

    result = backend.execute(
        hwnd=99, expected_pid=4321, element_pid=4321, rectangle=RECT,
        action="press_key", key="CTRL+S", approved=True,
    )

    assert result["status"] == "blocked"
    assert "foreground window changed" in result["reason"]
    assert native.events[-1] == ("key", 0x11, False)
    assert [event for event in native.events if event[0] == "key"] == [
        ("key", 0x11, True), ("key", 0x11, False),
    ]



@pytest.mark.parametrize(
    "chord",
    ["CTRL+SHIFT+ESC", "ALT+SPACE", "ALT+ESC", "CTRL+ALT+TAB"],
)
def test_windows_shell_navigation_chords_are_rejected(chord):
    from chimera.scoped_input import ScopedWindowsInputBackend

    with pytest.raises(ValueError):
        ScopedWindowsInputBackend._key_chord(chord)


def test_scroll_up_encodes_negative_wheel_delta_for_win32():
    from chimera.scoped_input import Win32InputNative

    native = object.__new__(Win32InputNative)
    seen = []
    native._mouse = lambda flags, x=0, y=0, data=0: seen.append((flags, x, y, data))
    native.send_mouse_wheel(-3)

    assert seen == [(0x0800, 0, 0, (-360) & 0xFFFFFFFF)]
