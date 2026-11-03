import asyncio
from types import SimpleNamespace

from chimera.local_model import LocalModelConfig
from chimera.ui import ACCENT, BG, FOCUS, MainWindow


def test_oxide_palette_avoids_default_blue_violet():
    assert BG == "#171B16"
    assert FOCUS == "#D7F16B"
    assert ACCENT == "#E98564"
    assert not any(token in (BG + FOCUS + ACCENT).lower() for token in ("#00f", "#0000ff", "#7c3aed", "#6366f1"))


def test_chat_bubbles_build_with_oxide_chat_treatment():
    user = MainWindow._chat_bubble(object(), "user", "Make the application.")
    assistant = MainWindow._chat_bubble(object(), "assistant", "I will inspect the workspace first.")

    assert user is not None
    assert assistant is not None


def test_startup_warms_explicit_server_before_chat(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    server_exe = tmp_path / "llama-server.exe"
    server_exe.write_bytes(b"fixture")
    config = LocalModelConfig(model, None, server_path=str(server_exe))

    class Server:
        started = False

        def start(self):
            self.started = True

    server = Server()
    window = object.__new__(MainWindow)
    window.local_provider = SimpleNamespace(config=config, server=server)
    window.model_boot_status = "checking"
    window.last_turn_notice = None
    window._show_overview = lambda: None

    asyncio.run(MainWindow._warm_local_model(window))

    assert server.started is True
    assert window.model_boot_status == "ready"


def test_startup_server_failure_is_visible_and_does_not_change_runtime(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    server_exe = tmp_path / "llama-server.exe"
    server_exe.write_bytes(b"fixture")
    config = LocalModelConfig(model, None, server_path=str(server_exe))

    class Server:
        def start(self):
            raise RuntimeError("fixture server failed")

    server = Server()
    window = object.__new__(MainWindow)
    window.local_provider = SimpleNamespace(config=config, server=server)
    window.model_boot_status = "checking"
    window.last_turn_notice = None
    window._show_overview = lambda: None

    asyncio.run(MainWindow._warm_local_model(window))

    assert window.model_boot_status == "failed"
    assert "No CLI or hosted fallback was attempted" in window.last_turn_notice
