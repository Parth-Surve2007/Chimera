from chimera.goals import GoalStore


def test_goals_screen_builds_capture_form_and_empty_state(tmp_path):
    from chimera import ui
    from chimera.ui import MainWindow

    window = object.__new__(MainWindow)
    window.goal_store = GoalStore(tmp_path / "chimera.db")
    window._set = lambda controls: setattr(window, "rendered_controls", controls)
    window._goal_stat = lambda label, value: ui.ft.Text(f"{label}:{value}")
    window._button = lambda label, handler: ui.ft.TextButton(label, on_click=handler)

    window._show_goals()

    assert window.goal_title_field.label == "GOAL / DESIRED OUTCOME"
    assert window.goal_description_field.multiline is True
    assert window.goal_criteria_field.label == "SUCCESS CRITERIA (ONE PER LINE)"
    assert window.goal_priority_field.value == "normal"
    assert len(window.rendered_controls) >= 5


def test_goals_screen_passes_persisted_records_to_card_renderer(tmp_path):
    from chimera import ui
    from chimera.ui import MainWindow

    window = object.__new__(MainWindow)
    window.goal_store = GoalStore(tmp_path / "chimera.db")
    goal = window.goal_store.create(
        "Build Chimera",
        "Local-first computer intelligence.",
        priority="critical",
        project="Chimera",
        success_criteria=["Complete real application work"],
    )
    window._set = lambda controls: setattr(window, "rendered_controls", controls)
    window._goal_stat = lambda label, value: ui.ft.Text(f"{label}:{value}")
    window._button = lambda label, handler: ui.ft.TextButton(label, on_click=handler)
    rendered_goals = []
    window._goal_card = lambda item: (rendered_goals.append(item), ui.ft.Text(item.title))[1]

    window._show_goals()

    assert len(rendered_goals) == 1
    assert rendered_goals[0].goal_id == goal.goal_id
    assert rendered_goals[0].title == "Build Chimera"
