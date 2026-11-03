from chimera.goals import GoalStore


def _text_values(control):
    seen = set()
    stack = [control]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        value = getattr(current, "value", None)
        if isinstance(value, str):
            yield value
        children = getattr(current, "controls", None) or []
        stack.extend(children)
        content = getattr(current, "content", None)
        if content is not None:
            stack.append(content)


def test_goals_screen_renders_empty_state_and_persistent_store(tmp_path):
    from chimera.ui import MainWindow

    window = object.__new__(MainWindow)
    window.goal_store = GoalStore(tmp_path / "chimera.db")
    window._set = lambda controls: setattr(window, "rendered_controls", controls)

    window._show_goals()

    visible_text = [value for control in window.rendered_controls for value in _text_values(control)]
    assert "GOALS & AMBITIONS" in visible_text
    assert "YOUR GOALS" in visible_text
    assert any("No goals recorded yet" in value for value in visible_text)


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

    visible_text = [value for control in window.rendered_controls for value in _text_values(control)]
    assert "Build Chimera" in visible_text
    assert any("CRITICAL PRIORITY" in value for value in visible_text)
    assert any("Complete real application work" in value for value in visible_text)
