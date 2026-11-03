"""Tests for the governed Blender application connector."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chimera.agent_runtime import AgentRuntime, AgentSessionStore
from chimera.applications.connectors.blender_connector import BlenderConnector
from chimera.applications.registry import get_application_registry


def _recipe():
    return json.dumps({
        "title": "Hackathon showcase",
        "objects": [
            {
                "name": "Main Cube",
                "type": "cube",
                "location": [0, 0, 0],
                "rotation": [0, 0, 0],
                "scale": [1.5, 1.5, 1.5],
                "color": [0.9, 0.25, 0.08],
            }
        ],
    })


def _arguments(tmp_path):
    return {
        "output_blend_path": str((tmp_path / "showcase.blend").resolve()),
        "output_render_path": str((tmp_path / "showcase.png").resolve()),
        "recipe_json": _recipe(),
    }


def test_blender_recipe_validation_rejects_arbitrary_code_and_unsafe_values():
    with pytest.raises(ValueError, match="unsupported properties"):
        BlenderConnector.validate_recipe(json.dumps({
            "objects": [{"name": "Injected", "type": "cube", "python": "__import__('os').system('whoami')"}]
        }))

    with pytest.raises(ValueError, match="between -100.0 and 100.0"):
        BlenderConnector.validate_recipe(json.dumps({
            "objects": [{"name": "Out of bounds", "type": "cube", "location": [1000000, 0, 0]}]
        }))

    with pytest.raises(ValueError, match="between 1 and 20 objects"):
        BlenderConnector.validate_recipe(json.dumps({"objects": []}))


def test_blender_scene_action_requires_operator_approval(tmp_path, monkeypatch):
    executable = tmp_path / "blender"
    executable.write_text("test executable", encoding="utf-8")
    monkeypatch.setenv("CHIMERA_BLENDER_EXECUTABLE", str(executable))

    connector = BlenderConnector()
    result = connector.execute_action("create_scene", _arguments(tmp_path), approved=False)

    assert result["status"] == "awaiting_approval"
    assert result["permission"] == "blender.project.write"
    assert not (tmp_path / "showcase.blend").exists()


def test_blender_scene_creation_uses_fixed_script_and_verifies_outputs(tmp_path, monkeypatch):
    executable = tmp_path / "blender"
    executable.write_text("test executable", encoding="utf-8")
    monkeypatch.setenv("CHIMERA_BLENDER_EXECUTABLE", str(executable))
    connector = BlenderConnector()
    captured = {}

    def fake_run(command, **kwargs):
        assert kwargs["shell"] is False
        assert kwargs["timeout"] == 180
        assert command[1:4] == ["--background", "--factory-startup", "--python"]
        script_path = Path(command[command.index("--python") + 1])
        assert script_path.is_file()
        script = script_path.read_text(encoding="utf-8")
        assert "eval(" not in script and "exec(" not in script
        config_path = Path(command[command.index("--config") + 1])
        config = json.loads(config_path.read_text(encoding="utf-8"))
        captured["config"] = config
        Path(config["blend_path"]).write_bytes(b"verified blend artifact")
        Path(config["render_path"]).write_bytes(b"verified png artifact")
        return SimpleNamespace(returncode=0, stdout="render complete", stderr="")

    monkeypatch.setattr(
        "chimera.applications.connectors.blender_connector.subprocess.run",
        fake_run,
    )
    result = connector.execute_action("create_scene", _arguments(tmp_path), approved=True)

    assert result["status"] == "completed"
    assert result["verified"] is True
    assert result["objects_created"] == 1
    assert Path(result["project_path"]).is_file()
    assert Path(result["render_path"]).is_file()
    assert captured["config"]["recipe"]["objects"][0]["type"] == "cube"


def test_blender_connector_rejects_existing_outputs_without_overwriting(tmp_path, monkeypatch):
    executable = tmp_path / "blender"
    executable.write_text("test executable", encoding="utf-8")
    monkeypatch.setenv("CHIMERA_BLENDER_EXECUTABLE", str(executable))
    existing = tmp_path / "showcase.blend"
    existing.write_bytes(b"keep this file")
    connector = BlenderConnector()

    result = connector.execute_action("create_scene", _arguments(tmp_path), approved=True)

    assert result["status"] == "failed"
    assert "already exists" in result["error"]
    assert existing.read_bytes() == b"keep this file"


class ScriptedProvider:
    def __init__(self):
        self.calls = 0
        self.catalog = None

    def decide(self, messages, catalog, session=None, cancel_event=None):
        self.calls += 1
        if self.calls == 1:
            self.catalog = catalog
            return {
                "type": "tool_call",
                "tool": "application.blender.create_scene",
                "invocation_id": "blender-demo-001",
                "arguments": {
                    "output_blend_path": self.output_blend_path,
                    "output_render_path": self.output_render_path,
                    "recipe_json": _recipe(),
                },
            }
        return {"type": "message", "content": "The Blender scene and preview were created and verified."}


def test_agent_runtime_discovers_blender_and_requires_approval_before_creation(tmp_path, monkeypatch):
    executable = tmp_path / "blender"
    executable.write_text("test executable", encoding="utf-8")
    monkeypatch.setenv("CHIMERA_BLENDER_EXECUTABLE", str(executable))

    def fake_run(command, **kwargs):
        config_path = Path(command[command.index("--config") + 1])
        config = json.loads(config_path.read_text(encoding="utf-8"))
        Path(config["blend_path"]).write_bytes(b"blend")
        Path(config["render_path"]).write_bytes(b"png")
        return SimpleNamespace(returncode=0, stdout="done", stderr="")

    monkeypatch.setattr(
        "chimera.applications.connectors.blender_connector.subprocess.run",
        fake_run,
    )
    provider = ScriptedProvider()
    provider.output_blend_path = str((tmp_path / "runtime.blend").resolve())
    provider.output_render_path = str((tmp_path / "runtime.png").resolve())
    runtime = AgentRuntime(AgentSessionStore(tmp_path / "agent.db"), provider)
    session_id = runtime.create_session(workspace=tmp_path, mode="build")

    first = runtime.send(session_id, "Create a simple orange cube in Blender.")
    assert first["status"] == "awaiting_approval"
    assert provider.catalog is not None
    assert any(item["id"] == "application.blender.create_scene" for item in provider.catalog["tools"])
    assert not Path(provider.output_blend_path).exists()

    final = runtime.approve_pending(session_id, approve=True)

    assert final["status"] == "completed"
    assert "created and verified" in final["message"].content
    assert Path(provider.output_blend_path).is_file()
    assert Path(provider.output_render_path).is_file()
    events = runtime.store.events(session_id)
    assert any(event.event_type == "tool.permission.required" for event in events)
    assert any(event.event_type == "tool.permission.granted" for event in events)
    assert any(event.event_type == "tool.completed" for event in events)
