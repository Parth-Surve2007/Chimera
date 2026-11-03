from types import SimpleNamespace

from chimera.agent_runtime import AgentRuntime, AgentSessionStore


class GoalAwareProvider:
    def __init__(self, hosted=False):
        self.profile = SimpleNamespace(hosted=hosted)
        self.messages_seen = None

    def decide(self, messages, catalog, **kwargs):
        self.messages_seen = list(messages)
        return {"type": "message", "content": "I have considered the available context."}


def test_active_goals_are_ephemeral_context_for_local_agent_reasoning(tmp_path):
    store = AgentSessionStore(tmp_path / "chimera.db")
    provider = GoalAwareProvider(hosted=False)
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
    context = next(message for message in provider.messages_seen if isinstance(message, dict) and message.get("role") == "system")
    assert "Build a local-first computer agent" in context["content"]
    assert "Application work produces real artifacts" in context["content"]
    assert "read-only context" in context["content"]

    # Goal context is not duplicated into the durable chat transcript.
    assert all(message.role != "system" for message in store.messages(session_id))
    goal = runtime.goal_store.list()[0]
    assert goal.status == "active"
    assert goal.completed_at is None


def test_operator_goals_are_not_sent_to_hosted_providers_by_default(tmp_path):
    store = AgentSessionStore(tmp_path / "chimera.db")
    provider = GoalAwareProvider(hosted=True)
    runtime = AgentRuntime(store, provider)
    runtime.goal_store.create("Private personal ambition", "Keep this local by default.")
    session_id = runtime.create_session(workspace=tmp_path, mode="plan")

    result = runtime.send(session_id, "Answer a general question.")

    assert result["status"] == "completed"
    assert not any(
        isinstance(message, dict) and message.get("role") == "system"
        for message in provider.messages_seen
    )
    assert all("Private personal ambition" not in str(message) for message in provider.messages_seen)
