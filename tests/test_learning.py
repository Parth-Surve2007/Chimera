import json
import pytest

from chimera.learning import ExperienceLearningEngine, ProposedSkill, SkillStore


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
                "tool": "workspace.list_files",
                "arguments": {"relative": ".", "recursive": True},
                "result": {"files": ["private-file.txt"]},
                "status": "completed",
            }),
        },
        {
            "role": "tool",
            "content": json.dumps({
                "tool": "workspace.inspect_file",
                "arguments": {"path": "private-file.txt"},
                "result": {"path": "private-file.txt", "bytes": 10},
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

    assert skill is not None
    assert skill.status == "proposed"
    assert len(skill.reusable_steps) == 3
    step = next(item for item in skill.reusable_steps if item["action"] == "workspace.create_document")
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



def _tool_message(tool, arguments, result, status="completed", error=None):
    return {
        "role": "tool",
        "content": json.dumps({
            "tool": tool,
            "arguments": arguments,
            "result": result,
            "status": status,
            "error": error,
        }),
    }


def test_learning_requires_repeated_multi_tool_success_and_sanitizes_values(tmp_path):
    store = SkillStore(tmp_path / "skills.json")
    engine = ExperienceLearningEngine(store)
    messages = [
        _tool_message(
            "workspace.list_files",
            {"relative": "Private/ClientA", "recursive": True},
            {"files": ["secret-client-name.txt"]},
        ),
        _tool_message(
            "workspace.inspect_file",
            {"path": "Private/ClientA/secret-client-name.txt"},
            {"path": "Private/ClientA/secret-client-name.txt", "bytes": 123},
        ),
        _tool_message(
            "workspace.create_document",
            {
                "path": "Private/ClientA/report.docx",
                "title": "Confidential Report",
                "content": "PRIVATE BODY TEXT",
                "api_key": "secret-value-must-never-be-stored",
            },
            {"path": "Private/ClientA/report.docx", "bytes": 456},
        ),
        _tool_message(
            "computer.invoke_element",
            {"session_id": "desktop-session", "reference_id": "stale-control"},
            {},
            status="failed",
            error="unsupported control pattern",
        ),
    ]

    skill = engine.extract_skill_from_trace(
        "session-2",
        "Read confidential client report and create a document",
        messages,
        events=[],
        source_task_id="task-2",
    )

    assert skill is not None
    assert skill.status == "proposed"
    assert len(skill.reusable_steps) == 3
    assert skill.lessons[0]["failure_category"] == "unsupported"
    assert "registered supported action" in skill.lessons[0]["rule"]
    serialized = json.dumps(skill.to_dict())
    for private_value in (
        "ClientA", "secret-client-name", "Confidential Report",
        "PRIVATE BODY TEXT", "secret-value-must-never-be-stored",
    ):
        assert private_value not in serialized
    assert store.proposals()[0]["skill_id"] == skill.skill_id
    assert store.approved() == []


def test_learning_rejects_short_or_single_tool_traces(tmp_path):
    store = SkillStore(tmp_path / "skills.json")
    engine = ExperienceLearningEngine(store)
    short_trace = [
        _tool_message("workspace.list_files", {"relative": ".", "recursive": True}, {"files": []}),
        _tool_message("workspace.list_files", {"relative": ".", "recursive": True}, {"files": []}),
    ]
    assert engine.extract_skill_from_trace("s1", "inspect", short_trace, []) is None

    same_tool_trace = [
        _tool_message("workspace.list_files", {"relative": ".", "recursive": True}, {"files": []}),
        _tool_message("workspace.list_files", {"relative": "src", "recursive": True}, {"files": []}),
        _tool_message("workspace.list_files", {"relative": "tests", "recursive": True}, {"files": []}),
    ]
    assert engine.extract_skill_from_trace("s1", "inspect", same_tool_trace, []) is None
    assert store.list() == []


def test_duplicate_trace_adds_evidence_instead_of_duplicate_skill(tmp_path):
    store = SkillStore(tmp_path / "skills.json")
    engine = ExperienceLearningEngine(store)
    trace = [
        _tool_message("workspace.list_files", {"relative": ".", "recursive": True}, {"files": ["a.py"]}),
        _tool_message("workspace.search_files", {"pattern": "*.py"}, {"files": ["a.py"]}),
        _tool_message("workspace.inspect_file", {"path": "a.py"}, {"bytes": 1}),
    ]
    first = engine.extract_skill_from_trace("session-1", "inspect", trace, [], source_task_id="task-1")
    second = engine.extract_skill_from_trace("session-2", "inspect", trace, [], source_task_id="task-2")
    assert first is not None
    assert second is None
    assert len(store.list()) == 1
    assert store.list()[0]["evidence_count"] == 2
    assert store.list()[0]["source_task_ids"] == ["task-1", "task-2"]


def test_skill_governance_history_usage_flags_and_locking(tmp_path):
    store = SkillStore(tmp_path / "skills.json")
    skill = ProposedSkill(
        skill_id="skill-test",
        name="Workflow: list files",
        description="A test proposal.",
        version="1.0.0",
        source_session_id="session-1",
        required_permissions=["workspace.read"],
        parameters_schema={},
        reusable_steps=[{"action": "workspace.list_files", "sanitized_arguments": {}}],
        expected_outputs=["files"],
    )
    store.save(skill)
    assert store.approve("skill-test") is True
    assert store.approve("skill-test") is False

    assert store.record_use("skill-test", "failed") is True
    assert store.record_use("skill-test", "failed") is True
    approved = store.get("skill-test")
    assert approved["usage_count"] == 2
    assert approved["failure_count"] == 2
    assert approved["review_required"] is True

    assert store.set_locked("skill-test", True)
    assert store.set_pinned("skill-test", True)
    with pytest.raises(PermissionError, match="locked"):
        store.save(ProposedSkill(
            skill_id="skill-test",
            name="Changed skill",
            description="Should be blocked.",
            version="2.0.0",
            source_session_id="session-1",
            required_permissions=[],
            parameters_schema={},
            reusable_steps=[],
            expected_outputs=[],
        ))
    history = store.history("skill-test")
    assert any(item["action"] == "approved" for item in history)
    assert any(item["action"] == "usage:failed" for item in history)
    assert store.get("skill-test")["locked"] is True
    assert store.get("skill-test")["pinned"] is True


def test_skill_content_revisions_can_be_rolled_back(tmp_path):
    store = SkillStore(tmp_path / "skills.json")
    original = ProposedSkill(
        skill_id="skill-rollback",
        name="Original name",
        description="Original procedure.",
        version="1.0.0",
        source_session_id="session-1",
        required_permissions=["workspace.read"],
        parameters_schema={},
        reusable_steps=[{"action": "workspace.list_files"}],
        expected_outputs=["files"],
    )
    store.save(original)
    revised = ProposedSkill(
        skill_id="skill-rollback",
        name="Revised name",
        description="Revised procedure.",
        version="2.0.0",
        source_session_id="session-1",
        required_permissions=["workspace.read", "workspace.write"],
        parameters_schema={},
        reusable_steps=[{"action": "workspace.create_file"}],
        expected_outputs=["path"],
    )
    store.save(revised)
    assert store.get("skill-rollback")["name"] == "Revised name"
    assert store.rollback("skill-rollback") is True
    restored = store.get("skill-rollback")
    assert restored["name"] == "Original name"
    assert restored["description"] == "Original procedure."
    assert restored["revision"] == 3


def test_rejected_proposal_never_enters_approved_skill_catalog(tmp_path):
    store = SkillStore(tmp_path / "skills.json")
    skill = ProposedSkill(
        skill_id="skill-rejected",
        name="Unwanted workflow",
        description="Rejected by the operator.",
        version="1.0.0",
        source_session_id="session-1",
        required_permissions=[],
        parameters_schema={},
        reusable_steps=[],
        expected_outputs=[],
    )
    store.save(skill)
    assert store.reject(skill.skill_id) is True
    assert store.proposals() == []
    assert store.approved() == []
    assert store.get(skill.skill_id)["status"] == "rejected"
