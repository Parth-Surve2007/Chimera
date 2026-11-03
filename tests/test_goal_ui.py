from chimera.goals import GoalStore


def test_goals_screen_renders_empty_state_and_persistent_store(tmp_path):
    from chimera.ui import MainWindow

    window = object.__new__(MainWindow)
    window.goal_store = GoalStore(tmp_path / "chimera.db")
    window._set = lambda controls: setattr(window, "rendered_controls", controls)

    window._show_goals()

    assert any(
        getattr(control, "value", None) == "GOALS & AMBITIONS"
        for control in window.rendered_controls
    )
    assert any(
        getattr(control, "value", None) == "YOUR GOALS"
        for control in window.rendered_controls
    )


def test_goals_screen_lists_persisted_goal_cards(tmp_path):
    from chimera.ui import MainWindow

    window = object.__new__(MainWindow)
    window.goal_store = GoalStore(tmp_path / "chimera.db")
    window.goal_store.create(
        "Build Chimera",
        "Local-first computer intelligence.",
        priority="critical",
        project="Chimera",
        success_criteria=["Complete real application work"],
    )
    window._set = lambda controls: setattr(window, "rendered_controls", controls)

    window._show_goals()

    # The last control is a card for the persisted goal, rather than the empty state.
    last = window.rendered_controls[-1]
    card_text = str(last)
    assert "Build Chimera" in card_text
    assert "ACTIVE" in card_text
