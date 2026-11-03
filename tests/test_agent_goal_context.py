from types import SimpleNamespace

from chimera.agent_runtime import AgentRuntime, AgentSessionStore
from chimera.local_model import LocalModelConfig, LocalModelProvider


class HostedProbeProvider:
    def __init__(self):
        self.profile = SimpleNamespace(hosted=True)
        self.messages_seen = None

    def decide(self, messages, catalog, **kwargs):
        self.messages_seen = list(messages)
        return {"type": "message", "content": "I answered without private goal context."}


def test_active_goals_reach_the_real_local_provider_prompt_ephemerally(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"test fixture")
    cli = tmp_path / "llama-cli"
    cli.write_bytes(b"test fixture")
    captured = {}

    def runner(prompt, _config):
        captured["prompt"] = prompt
        return '{"type":"message","content":"Local reasoning received operator goals."}'

    provider = LocalModelProvider(LocalModelConfig(model, str(cli)), runner)
    store = AgentSessionStore(tmp_path / "chimera.db")
    runtime = AgentRuntime(store, provider)
    runtime.goal_store.create(
        "Build a local-first computer agent",
        "Chimera should carry out useful work in desktop applications.",
        priority="critical",
        project="Chimera",
        success_criteria=["Local inference is verified", "Application work produces real artifacts"],
    )
    session_id = runtime.create_session(workspace=tmp_path, mode="plan")

    result = runtime.send(session_id, "Help me inspect the project architecture.")

    assert result["status"] == "completed"
    assert "SYSTEM:" in captured["prompt"]
    assert "Build a local-first computer agent" in captured["prompt"]
    assert "Application work produces real artifacts" in captured["prompt"]
    assert "read-only context" in captured["prompt"]

    # Goal context is not duplicated into the durable chat transcript or completed.
    assert all(message.role != "system" for message in store.messages(session_id))
    goal = runtime.goal_store.list()[0]
    assert goal.status == "active"
    assert goal.completed_at is None


def test_operator_goals_are_not_sent_to_hosted_providers_by_default(tmp_path):
    store = AgentSessionStore(tmp_path / "chimera.db")
    provider = HostedProbeProvider()
    runtime = AgentRuntime(store, provider)
    runtime.goal_store.create("Private personal ambition", "Keep this local by default.")
    session_id = runtime.create_session(workspace=tmp_path, mode="plan")

    result = runtime.send(session_id, "Answer a general question.")

    assert result["status"] == "completed"
    assert all(
        getattr(message, "role", None) != "system"
        for message in provider.messages_seen
    )
    assert all(
        "Private personal ambition" not in getattr(message, "content", "")
        for message in provider.messages_seen
    )
