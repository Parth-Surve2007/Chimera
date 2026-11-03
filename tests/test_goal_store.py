import pytest

from chimera.goals import GoalStore


def test_goal_create_persists_across_store_restarts(tmp_path):
    db_path = tmp_path / "chimera.db"
    store = GoalStore(db_path)
    goal = store.create(
        "Build Chimera",
        "Create a local-first computer intelligence.",
        priority="critical",
        project="Chimera",
        success_criteria=["Local inference works", "Application task produces verified output"],
        tags=["ai", "computer-agent"],
    )

    reopened = GoalStore(db_path)
    saved = reopened.get(goal.goal_id)

    assert saved is not None
    assert saved.title == "Build Chimera"
    assert saved.status == "active"
    assert saved.priority == "critical"
    assert saved.project == "Chimera"
    assert saved.success_criteria == ["Local inference works", "Application task produces verified output"]
    assert saved.tags == ["ai", "computer-agent"]
    assert saved.completed_at is None


def test_goal_lifecycle_is_explicit_and_audited(tmp_path):
    store = GoalStore(tmp_path / "chimera.db")
    goal = store.create("Ship first demo")

    paused = store.transition(goal.goal_id, "paused", reason="Waiting for Blender acceptance test")
    assert paused.status == "paused"

    active = store.transition(goal.goal_id, "active", reason="Acceptance test is ready")
    assert active.status == "active"

    completed = store.transition(
        goal.goal_id,
        "completed",
        reason="Demo validated on the target machine",
        evidence=["artifact:demo.blend", "run:acceptance-2026-11-03"],
    )
    assert completed.status == "completed"
    assert completed.completed_at
    assert completed.evidence == ["artifact:demo.blend", "run:acceptance-2026-11-03"]
    assert completed.completion_note == "Demo validated on the target machine"

    events = store.events(goal.goal_id)
    transitions = [event.payload["to"] for event in events if event.event_type == "goal.status_changed"]
    assert transitions == ["completed", "active", "paused"]
    assert events[0].event_type == "goal.status_changed"


def test_goal_cannot_be_completed_without_evidence(tmp_path):
    store = GoalStore(tmp_path / "chimera.db")
    goal = store.create("Complete an actual deliverable")

    with pytest.raises(ValueError, match="at least one evidence reference"):
        store.transition(goal.goal_id, "completed")

    unchanged = store.get(goal.goal_id)
    assert unchanged.status == "active"
    assert unchanged.completed_at is None


def test_goal_completion_evidence_is_normalized_and_deduplicated(tmp_path):
    store = GoalStore(tmp_path / "chimera.db")
    goal = store.create("Produce verified output")
    completed = store.transition(
        goal.goal_id,
        "completed",
        evidence=["run:123", "run:123", "artifact:output.png"],
    )
    assert completed.evidence == ["run:123", "artifact:output.png"]


def test_completed_and_archived_goals_require_reason_to_reopen(tmp_path):
    store = GoalStore(tmp_path / "chimera.db")
    goal = store.create("Finish milestone")
    store.transition(goal.goal_id, "completed", evidence=["test:passed"])

    with pytest.raises(ValueError, match="requires a reason"):
        store.transition(goal.goal_id, "active")

    reopened = store.transition(goal.goal_id, "active", reason="The acceptance criteria changed")
    assert reopened.status == "active"
    assert reopened.completed_at is None
    assert reopened.evidence == ["test:passed"]


def test_goal_update_validation_and_terminal_protection(tmp_path):
    store = GoalStore(tmp_path / "chimera.db")
    goal = store.create("Original title")

    updated = store.update(
        goal.goal_id,
        title="Updated title",
        description="A more precise outcome.",
        priority="high",
        success_criteria=["Criterion A"],
        tags=["engineering"],
    )
    assert updated.title == "Updated title"
    assert updated.priority == "high"
    assert updated.success_criteria == ["Criterion A"]

    with pytest.raises(ValueError, match="priority"):
        store.update(goal.goal_id, priority="urgent")

    store.transition(goal.goal_id, "completed", evidence=["release:1"])
    with pytest.raises(ValueError, match="reopened"):
        store.update(goal.goal_id, title="Do not silently rewrite history")


def test_goal_search_filters_status_and_project(tmp_path):
    store = GoalStore(tmp_path / "chimera.db")
    first = store.create("Build Blender connector", project="Chimera", priority="high")
    store.create("Write presentation", project="Hackathon")
    store.transition(first.goal_id, "paused")

    assert [g.title for g in store.list(status="paused")] == ["Build Blender connector"]
    assert [g.title for g in store.list(project="Hackathon")] == ["Write presentation"]
    assert [g.title for g in store.list(search="Blender")] == ["Build Blender connector"]


def test_goal_stats_count_all_states(tmp_path):
    store = GoalStore(tmp_path / "chimera.db")
    active = store.create("Active")
    paused = store.create("Paused")
    done = store.create("Done")
    store.transition(paused.goal_id, "paused")
    store.transition(done.goal_id, "completed", evidence=["test:passed"])
    stats = store.stats()

    assert stats == {"active": 1, "paused": 1, "completed": 1, "archived": 0, "total": 3}


def test_goal_rejects_invalid_values_and_limits(tmp_path):
    store = GoalStore(tmp_path / "chimera.db")
    with pytest.raises(ValueError, match="title"):
        store.create("   ")
    with pytest.raises(ValueError, match="priority"):
        store.create("Test", priority="urgent")
    with pytest.raises(ValueError, match="success_criteria"):
        store.create("Test", success_criteria="not a list")
    goal = store.create("Valid")
    with pytest.raises(ValueError, match="limit"):
        store.list(limit=0)
    with pytest.raises(ValueError, match="status"):
        store.list(status="done")
    with pytest.raises(ValueError, match="transition"):
        store.transition(goal.goal_id, "completed") if False else store.transition(goal.goal_id, "archived")
    assert store.get(goal.goal_id).status == "active"


def test_goal_events_are_isolated_by_goal(tmp_path):
    store = GoalStore(tmp_path / "chimera.db")
    one = store.create("One")
    two = store.create("Two")
    store.transition(one.goal_id, "paused")

    assert all(event.goal_id == one.goal_id for event in store.events(one.goal_id))
    assert all(event.goal_id == two.goal_id for event in store.events(two.goal_id))
    assert len(store.events(one.goal_id)) == 2
    assert len(store.events(two.goal_id)) == 1


def test_goal_database_can_share_existing_agent_runtime_database(tmp_path):
    from chimera.agent_runtime import AgentSessionStore

    db_path = tmp_path / "chimera.db"
    AgentSessionStore(db_path)
    goals = GoalStore(db_path)
    goal = goals.create("Keep existing runtime history")
    assert goals.get(goal.goal_id) is not None
    assert AgentSessionStore(db_path).list_sessions() == []
