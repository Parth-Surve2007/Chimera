import json

from chimera.learning import ExperienceLearningEngine, SkillStore


def test_learning_extracts_only_successful_calls_as_argument_schemas(tmp_path):
    store = SkillStore(tmp_path / "skills.json")
    engine = ExperienceLearningEngine(store)
    messages = [
        {
            "role": "tool",
            "content": json.dumps({
                "tool": "workspace.create_document",
                "arguments": {
                    "path": "Desktop/Private-Report.docx",
                    "title": "Private Project Name",
                    "content": "A unique private paragraph that must not leak.",
                    "password": "do-not-store-this",
                },
                "result": {"path": "Desktop/Private-Report.docx", "bytes": 512},
                "status": "completed",
            }),
        },
        {
            "role": "tool",
            "content": json.dumps({
                "tool": "workspace.create_file",
                "arguments": {"path": "failed.txt", "content": "not successful"},
                "result": {},
                "status": "failed",
            }),
        },
        {
            "role": "tool",
            "content": json.dumps({
                "tool": "agent.todo_write",
                "arguments": {"todos": [{"content": "internal bookkeeping"}]},
                "result": {},
                "status": "completed",
            }),
        },
    ]

    skill = engine.extract_skill_from_trace(
        "session-1",
        "Create a report",
        messages,
        events=[],
    )

    assert len(skill.reusable_steps) == 1
    step = skill.reusable_steps[0]
    assert step["action"] == "workspace.create_document"
    assert step["sanitized_arguments"] == {
        "path": "string",
        "title": "string",
        "content": "string",
        "password": "string",
    }
    assert step["expected_result_fields"] == ["bytes", "path"]
    serialized = json.dumps(skill.to_dict())
    assert "Private-Report.docx" not in serialized
    assert "Private Project Name" not in serialized
    assert "unique private paragraph" not in serialized
    assert "do-not-store-this" not in serialized
    assert len(store.list()) == 1
