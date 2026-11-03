import json
from types import SimpleNamespace

def test_first_run_chat_renders_persisted_user_assistant_and_tool_messages():
    from chimera.ui import MainWindow

    class Store:
        def messages(self, _session_id):
            return [
                SimpleNamespace(role="user", content="Inspect this project", created_at=""),
                SimpleNamespace(role="tool", content=json.dumps({"tool":"workspace.list_files","status":"completed","result":{"files":["README.md"]}}), created_at=""),
                SimpleNamespace(role="assistant", content="I found README.md.", created_at=""),
            ]

    window = object.__new__(MainWindow)
    window.agent_store = Store()
    window.agent_session_id = "test-session"
    window.conversation_messages = []
    window.task_busy = False
    window.last_turn_notice = None
    window.pending_agent_approval = None
    controls = window._conversation_content()
    assert len(controls) == 3


def test_chat_ui_exposes_local_protocol_verification_and_no_blue_theme():
    from chimera import ui
    assert ui.BG == "#000000" and ui.ACCENT == "#F17857" and ui.FOCUS == "#E5B35A"
    assert hasattr(ui.MainWindow, "_verify_local_agent")
    assert hasattr(ui.MainWindow, "_chat_sidebar")


def test_empty_conversation_renders_welcome_prompts_without_model():
    from chimera.ui import MainWindow

    class EmptyStore:
        def messages(self, _session_id):
            return []

    window = object.__new__(MainWindow)
    window.agent_store = EmptyStore()
    window.agent_session_id = "empty-session"
    window.conversation_messages = []
    window.task_busy = False
    window.last_turn_notice = None
    window.pending_agent_approval = None
    controls = window._conversation_content()
    assert len(controls) == 1
