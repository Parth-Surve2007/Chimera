"""Comprehensive unit and integration tests for Meta Model API and Muse Spark 1.3."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest

from chimera.agent_runtime import AgentRuntime, AgentSessionStore
from chimera.model_hub import ModelProfile, ModelProfileStore
from chimera.meta_provider import MetaModelProvider, UsageAccountant, META_API_BASE_URL
from chimera.sam_perception import SAMVisualPerception, VisualRegion, calculate_iou


class MockMetaCredentials:
    def get(self, provider):
        return "mock-meta-secret-key"


class MetaApiMockHandler(BaseHTTPRequestHandler):
    requests_received = []

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        MetaApiMockHandler.requests_received.append(payload)

        # Confirm authorization header was sent
        auth = self.headers.get("Authorization", "")
        assert "Bearer mock-meta-secret-key" in auth
        assert self.headers.get("X-Meta-Data-Policy") == "standard"

        # Model turn 1: Model emits a tool call for workspace inspection
        if len(MetaApiMockHandler.requests_received) == 1:
            resp = {
                "id": "chatcmpl-mock-turn1",
                "object": "chat.completion",
                "model": "muse-spark-1.3",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "call_meta_001",
                            "type": "function",
                            "function": {
                                "name": "workspace.list_files",
                                "arguments": json.dumps({"relative": ".", "recursive": True}),
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 28,
                    "total_tokens": 148,
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
            }
        # Model turn 2: After tool execution result is returned, model generates final grounded response
        else:
            # Verify tool execution result was returned to model
            messages = payload.get("messages", [])
            assistant_call = next(
                (m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")),
                None,
            )
            tool_result = next((m for m in messages if m.get("role") == "tool"), None)
            assert assistant_call is not None
            assert tool_result is not None
            call = assistant_call["tool_calls"][0]
            assert call["id"] == "call_meta_001"
            assert call["function"]["name"] == "workspace.list_files"
            assert json.loads(call["function"]["arguments"]) == {"relative": ".", "recursive": True}
            assert tool_result["tool_call_id"] == "call_meta_001"
            resp = {
                "id": "chatcmpl-mock-turn2",
                "object": "chat.completion",
                "model": "muse-spark-1.3",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Based on listing the workspace, I verified project files and structure.",
                    },
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": 210,
                    "completion_tokens": 35,
                    "total_tokens": 245,
                    "prompt_tokens_details": {"cached_tokens": 50},
                },
            }

        body = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_meta_usage_accountant(tmp_path):
    accountant = UsageAccountant(tmp_path / "meta_usage.json", spend_limit_usd=5.0)
    rec1 = accountant.record_usage(
        provider="meta",
        model="muse-spark-1.3",
        input_tokens=1000,
        output_tokens=200,
        cached_tokens=0,
        duration_ms=450,
        task_id="task-123",
    )
    assert rec1.estimated_cost > 0.0
    summary = accountant.summary()
    assert summary["total_requests"] == 1
    assert summary["total_input_tokens"] == 1000
    assert summary["total_output_tokens"] == 200
    assert summary["warning_reached"] is False
    assert summary["limit_reached"] is False


def test_meta_provider_agent_loop_with_genuine_tool_calling(tmp_path):
    MetaApiMockHandler.requests_received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), MetaApiMockHandler)
    server_port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        # Create a sample workspace file
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "README.md").write_text("# Project Test\nVersion: 1.0.0", encoding="utf-8")

        profile = ModelProfile(
            profile_id="meta-muse-spark",
            provider_id="meta",
            model_id="muse-spark-1.3",
            hosted=True,
            base_url=f"http://127.0.0.1:{server_port}/v1",
            tool_calling=True,
            streaming=True,
            capability_evidence="tested",
        )
        profiles_store = ModelProfileStore(tmp_path / "profiles.json")
        profiles_store.save(profile)

        accountant = UsageAccountant(tmp_path / "meta_usage.json")
        provider = MetaModelProvider(profile, MockMetaCredentials(), accountant=accountant)

        store = AgentSessionStore(tmp_path / "agent.db")
        runtime = AgentRuntime(store, provider, profiles_store)

        session_id = runtime.create_session(workspace_dir)
        runtime.set_session_model(session_id, "meta-muse-spark", local_only=False)

        # Run conversation turn
        result = runtime.send(session_id, "Inspect this workspace.")

        assert result["status"] == "completed"
        assert len(MetaApiMockHandler.requests_received) == 2

        # Verify AgentRuntime remained sole execution authority
        messages = store.messages(session_id)
        roles = [m.role for m in messages]
        assert "user" in roles
        assert "tool" in roles
        assert "assistant" in roles

        tool_msg = next(m for m in messages if m.role == "tool")
        tool_data = json.loads(tool_msg.content)
        assert tool_data["tool"] == "workspace.list_files"
        assert tool_data["status"] == "completed"

        events = store.events(session_id)
        event_types = [e.event_type for e in events]
        assert "model.request.started" in event_types
        assert "tool.call.requested" in event_types
        assert "tool.started" in event_types
        assert "tool.completed" in event_types
        assert "turn.completed" in event_types

        # Verify accountant tracked both turns
        summary = accountant.summary()
        assert summary["total_requests"] == 2
        assert summary["total_input_tokens"] == 330
        assert summary["total_output_tokens"] == 63
    finally:
        server.shutdown()
        server.server_close()


def test_sam_visual_perception_corroboration():
    sam = SAMVisualPerception(iou_threshold=0.5)

    # Mock SAM 3.1 API response
    api_response = {
        "objects": [
            {"label": "button", "box": [100, 100, 200, 140], "confidence": 0.94, "mask": True},
            {"label": "text_field", "box": [100, 150, 400, 200], "confidence": 0.88, "mask": True},
        ]
    }
    regions = sam.parse_api_response(api_response)
    assert len(regions) == 2
    assert regions[0].label == "button"

    # Mock UIA elements
    uia_elements = [
        {"reference_id": "btn-1", "control_type": "Button", "name": "Submit", "rect": [102, 101, 96, 38]},
        {"reference_id": "txt-1", "control_type": "Edit", "name": "Prompt", "rect": [100, 150, 300, 50]},
        {"reference_id": "menu-1", "control_type": "MenuItem", "name": "File", "rect": [0, 0, 50, 20]},
    ]

    corroborated = sam.corroborate_with_uia(regions, uia_elements)
    assert len(corroborated) == 3

    # btn-1 matches button visual region
    assert corroborated[0].reference_id == "btn-1"
    assert corroborated[0].verified is True
    assert corroborated[0].iou_score > 0.8

    # txt-1 matches text_field
    assert corroborated[1].reference_id == "txt-1"
    assert corroborated[1].verified is True

    # menu-1 has no matching visual region
    assert corroborated[2].reference_id == "menu-1"
    assert corroborated[2].verified is False


def test_meta_tool_formatter_exposes_registered_application_actions():
    """Meta receives app capabilities from AgentRuntime, not only workspace tools."""
    catalog = {
        "tools": [
            {
                "id": "application.rea.list_backends",
                "description": "List registered reverse-engineering analysis backends.",
                "arguments": {},
            },
            {
                "id": "workspace.list_files",
                "description": "List files in the active workspace.",
                "arguments": {"relative": "string", "recursive": "boolean"},
            },
        ]
    }

    formatted = MetaModelProvider._format_tools(catalog)
    names = [item["function"]["name"] for item in formatted]
    assert "application.rea.list_backends" in names
    assert "workspace.list_files" in names
    workspace_tool = next(item for item in formatted if item["function"]["name"] == "workspace.list_files")
    assert workspace_tool["function"]["parameters"]["required"] == ["relative", "recursive"]
    app_tool = next(item for item in formatted if item["function"]["name"] == "application.rea.list_backends")
    assert app_tool["function"]["parameters"]["additionalProperties"] is False
    assert app_tool["function"]["parameters"]["required"] == []


def test_meta_tool_history_reconstructs_assistant_call_and_result_pair():
    """Persisted results become the assistant-call/tool-result pair expected by Meta."""
    history = [
        type("Message", (), {"role": "user", "content": "Inspect the app ecosystem."})(),
        type("Message", (), {
            "role": "tool",
            "content": json.dumps({
                "invocation_id": "call_app_001",
                "tool": "application.rea.list_backends",
                "arguments": {},
                "result": {"backends": ["builtin_static"]},
                "status": "completed",
                "error": None,
            }),
        })(),
    ]

    formatted = MetaModelProvider._format_messages(history)
    assert formatted[0] == {"role": "user", "content": "Inspect the app ecosystem."}
    assert formatted[1]["role"] == "assistant"
    assert formatted[1]["tool_calls"][0]["id"] == "call_app_001"
    assert formatted[1]["tool_calls"][0]["function"]["name"] == "application.rea.list_backends"
    assert formatted[2]["role"] == "tool"
    assert formatted[2]["tool_call_id"] == "call_app_001"
    assert "builtin_static" in formatted[2]["content"]
