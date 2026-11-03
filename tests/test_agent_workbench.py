import asyncio
from types import SimpleNamespace

from chimera.local_model import LocalModelConfig
from chimera.ui import ACCENT, BG, FOCUS, MainWindow


def test_dark_emperor_palette_uses_full_black_and_warm_accents():
    assert BG == "#000000"
    assert FOCUS == "#E5B35A"
    assert ACCENT == "#F17857"
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



def test_full_workbench_composes_left_chat_and_context_panels():
    from chimera import ui

    class AgentStore:
        def list_sessions(self, limit=16):
            return []

        def session(self, session_id):
            return {"mode": "plan"}

        def session_model(self, session_id):
            return {}

        def messages(self, session_id):
            return []

    window = object.__new__(MainWindow)
    window.agent_store = AgentStore()
    window.agent_session_id = "workbench-smoke"
    window.goal_store = SimpleNamespace(list=lambda **kwargs: [])
    window.profile_store = SimpleNamespace(list=lambda: [])
    window.registry = SimpleNamespace(list=lambda: [])
    window.local_provider = SimpleNamespace(
        config=LocalModelConfig(None, None),
        server=None,
    )
    window.workspace_path = None
    window.task_busy = False
    window.model_boot_status = "missing"
    window.local_verification = None
    window.last_turn_notice = None
    window.pending_agent_approval = None
    window.conversation_messages = []
    window.live_events = []
    window.pending_request = None
    window.task_field = ui.ft.TextField(multiline=True)
    window.body = ui.ft.Container(expand=True)
    window.page = SimpleNamespace(update=lambda: None)

    window._show_overview()

    assert isinstance(window.body.content, ui.ft.Row)
    assert len(window.body.content.controls) == 3
