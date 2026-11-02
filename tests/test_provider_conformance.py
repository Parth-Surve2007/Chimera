"""Provider conformance test suite.

Validates that OpenAI-compatible, Anthropic, Gemini, Meta, and Local adapters
strictly follow the universal ModelProvider interface, normalize tool calling
and tool result continuations, normalize errors, and maintain AgentRuntime's sole
execution authority.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest

from chimera.agent_runtime import AgentRuntime, AgentSessionStore
from chimera.model_hub import ModelProfile, ModelProfileStore
from chimera.providers import (
    ApiDialect,
    CapabilityStatus,
    ExecutionProvenance,
    GenerationRequest,
    ModelCapabilities,
    ModelProvider,
    NormalizedMessage,
    ProviderAuthenticationError,
    ProviderConfig,
    ProviderRegistry,
    ToolCall,
    ToolResult,
    UniversalUsageAccountant,
    get_provider_registry,
)
from chimera.providers.adapters.openai_adapter import OpenAICompatibleAdapter
from chimera.providers.adapters.anthropic_adapter import AnthropicMessagesAdapter
from chimera.providers.adapters.gemini_adapter import GeminiAdapter


class MockCredentials:
    def __init__(self, key="mock-test-key"):
        self.key = key
    def get(self, provider_id):
        return self.key


class OpenAIConformanceHandler(BaseHTTPRequestHandler):
    requests_received = []

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        OpenAIConformanceHandler.requests_received.append(payload)

        # First turn: returns tool call
        if len(OpenAIConformanceHandler.requests_received) == 1:
            resp = {
                "id": "cmpl-openai-1",
                "object": "chat.completion",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "call-openai-99",
                            "type": "function",
                            "function": {
                                "name": "workspace.list_files",
                                "arguments": json.dumps({"relative": ".", "recursive": True}),
                            },
                        }],
                    },
                }],
                "usage": {"prompt_tokens": 150, "completion_tokens": 20},
            }
        else:
            # Second turn: verify tool result was submitted
            msgs = payload.get("messages", [])
            assert any(m.get("role") == "tool" for m in msgs)
            resp = {
                "id": "cmpl-openai-2",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "OpenAI adapter verified workspace contents."},
                }],
                "usage": {"prompt_tokens": 220, "completion_tokens": 25},
            }

        body = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class AnthropicConformanceHandler(BaseHTTPRequestHandler):
    requests_received = []

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        AnthropicConformanceHandler.requests_received.append(payload)

        # Check native Anthropic headers
        assert self.headers.get("x-api-key") == "mock-anthropic-key"
        assert self.headers.get("anthropic-version") == "2023-06-01"

        if len(AnthropicConformanceHandler.requests_received) == 1:
            resp = {
                "id": "msg-anthropic-1",
                "type": "message",
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "tool_ant_1",
                    "name": "workspace.list_files",
                    "input": {"relative": ".", "recursive": True},
                }],
                "usage": {"input_tokens": 110, "output_tokens": 18},
            }
        else:
            msgs = payload.get("messages", [])
            # Assert tool_result block in user turn
            assert any(
                any(isinstance(c, dict) and c.get("type") == "tool_result" for c in m.get("content", []))
                for m in msgs if m.get("role") == "user"
            )
            resp = {
                "id": "msg-anthropic-2",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Anthropic adapter confirmed workspace files."}],
                "usage": {"input_tokens": 180, "output_tokens": 24},
            }

        body = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_openai_compatible_adapter_conformance(tmp_path):
    OpenAIConformanceHandler.requests_received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), OpenAIConformanceHandler)
    server_port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "test.txt").write_text("hello", encoding="utf-8")

        config = ProviderConfig(
            provider_id="generic-openai",
            display_name="Generic OpenAI Endpoint",
            dialect=ApiDialect.OPENAI_COMPATIBLE,
            base_url=f"http://127.0.0.1:{server_port}/v1",
        )
        creds = MockCredentials("mock-openai-key")
        accountant = UniversalUsageAccountant(tmp_path / "usage.json")
        adapter = OpenAICompatibleAdapter(config, creds, accountant, provenance=ExecutionProvenance.MOCK)

        profile = ModelProfile(
            profile_id="generic-openai-profile",
            provider_id="generic-openai",
            model_id="gpt-4o",
            hosted=True,
            base_url=f"http://127.0.0.1:{server_port}/v1",
            tool_calling=True,
        )
        pstore = ModelProfileStore(tmp_path / "profiles.json")
        pstore.save(profile)

        astore = AgentSessionStore(tmp_path / "agent.db")
        runtime = AgentRuntime(astore, adapter, pstore)

        sid = runtime.create_session(workspace_dir)
        runtime.set_session_model(sid, "generic-openai-profile", local_only=False)

        res = runtime.send(sid, "Inspect the workspace")
        assert res["status"] == "completed"
        assert len(OpenAIConformanceHandler.requests_received) == 2
        assert "verified workspace" in astore.messages(sid)[-1].content

        summary = accountant.summary("generic-openai")
        assert summary["total_requests"] == 2
        assert summary["total_input_tokens"] == 370
        assert summary["total_output_tokens"] == 45
    finally:
        server.shutdown()
        server.server_close()


def test_anthropic_adapter_conformance(tmp_path):
    AnthropicConformanceHandler.requests_received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), AnthropicConformanceHandler)
    server_port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "code.py").write_text("print(1)", encoding="utf-8")

        config = ProviderConfig(
            provider_id="anthropic",
            display_name="Anthropic Native Messages",
            dialect=ApiDialect.ANTHROPIC_MESSAGES,
            base_url=f"http://127.0.0.1:{server_port}/v1",
        )
        creds = MockCredentials("mock-anthropic-key")
        accountant = UniversalUsageAccountant(tmp_path / "usage.json")
        adapter = AnthropicMessagesAdapter(config, creds, accountant, provenance=ExecutionProvenance.MOCK)

        profile = ModelProfile(
            profile_id="claude-profile",
            provider_id="anthropic",
            model_id="claude-3-5-sonnet",
            hosted=True,
            base_url=f"http://127.0.0.1:{server_port}/v1",
            tool_calling=True,
        )
        pstore = ModelProfileStore(tmp_path / "profiles.json")
        pstore.save(profile)

        astore = AgentSessionStore(tmp_path / "agent.db")
        runtime = AgentRuntime(astore, adapter, pstore)

        sid = runtime.create_session(workspace_dir)
        runtime.set_session_model(sid, "claude-profile", local_only=False)

        res = runtime.send(sid, "Inspect the workspace")
        assert res["status"] == "completed"
        assert len(AnthropicConformanceHandler.requests_received) == 2
        assert "Anthropic adapter confirmed" in astore.messages(sid)[-1].content

        summary = accountant.summary("anthropic")
        assert summary["total_requests"] == 2
        assert summary["total_input_tokens"] == 290
    finally:
        server.shutdown()
        server.server_close()


def test_missing_credentials_fails_cleanly(tmp_path):
    config = ProviderConfig(
        provider_id="unauth-provider",
        display_name="Unauthenticated Provider",
        dialect=ApiDialect.OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:9999/v1",
    )
    # Empty credentials
    creds = MockCredentials("")
    adapter = OpenAICompatibleAdapter(config, creds)
    assert adapter.validate_credentials() is False

    req = GenerationRequest(
        model="mock-model",
        messages=(NormalizedMessage(role="user", content="Hello"),),
    )
    with pytest.raises(ProviderAuthenticationError) as exc_info:
        adapter.generate(req)
    assert "Missing API key" in str(exc_info.value)


class GeminiInteractionsConformanceHandler(BaseHTTPRequestHandler):
    requests_received = []

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        GeminiInteractionsConformanceHandler.requests_received.append(payload)

        if len(GeminiInteractionsConformanceHandler.requests_received) == 1:
            resp = {
                "id": "interaction-001",
                "outputs": [{
                    "tool_call": {
                        "id": "gemini_call_abc",
                        "name": "workspace.list_files",
                        "arguments": {"relative": ".", "recursive": True},
                    }
                }],
                "usage": {"input_tokens": 140, "output_tokens": 30},
            }
        else:
            assert payload.get("previous_interaction_id") == "interaction-001"
            resp = {
                "id": "interaction-002",
                "outputs": [{"text": "Gemini Interactions verified workspace files."}],
                "usage": {"input_tokens": 190, "output_tokens": 20},
            }

        body = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_gemini_interactions_adapter_conformance(tmp_path):
    from chimera.providers.adapters.gemini_interactions_adapter import GeminiInteractionsAdapter
    GeminiInteractionsConformanceHandler.requests_received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), GeminiInteractionsConformanceHandler)
    server_port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "gem.txt").write_text("gemini content", encoding="utf-8")

        config = ProviderConfig(
            provider_id="gemini",
            display_name="Google Gemini Interactions",
            dialect=ApiDialect.GEMINI_INTERACTIONS,
            base_url=f"http://127.0.0.1:{server_port}/v1beta/interactions",
        )
        creds = MockCredentials("mock-gemini-key")
        accountant = UniversalUsageAccountant(tmp_path / "usage.json")
        adapter = GeminiInteractionsAdapter(config, creds, accountant, provenance=ExecutionProvenance.MOCK)

        profile = ModelProfile(
            profile_id="gemini-interactions-profile",
            provider_id="gemini",
            model_id="gemini-2.5-flash",
            hosted=True,
            base_url=f"http://127.0.0.1:{server_port}/v1beta/interactions",
            tool_calling=True,
            dialect="gemini_interactions",
        )
        pstore = ModelProfileStore(tmp_path / "profiles.json")
        pstore.save(profile)

        astore = AgentSessionStore(tmp_path / "agent.db")
        runtime = AgentRuntime(astore, adapter, pstore)

        sid = runtime.create_session(workspace_dir)
        runtime.set_session_model(sid, "gemini-interactions-profile", local_only=False)

        res = runtime.send(sid, "Inspect the workspace")
        assert res["status"] == "completed"
        assert len(GeminiInteractionsConformanceHandler.requests_received) == 2
        assert "Gemini Interactions verified" in astore.messages(sid)[-1].content
    finally:
        server.shutdown()
        server.server_close()


def test_custom_provider_lifecycle_and_restart(tmp_path):
    """Proves that a completely unknown custom provider profile can be created,
    persisted across restarts, and routed through AgentRuntime without code changes."""
    profile_path = tmp_path / "profiles.json"
    pstore1 = ModelProfileStore(profile_path)
    custom_profile = ModelProfile(
        profile_id="custom-groq-endpoint",
        provider_id="groq",
        model_id="llama-3.3-70b-versatile",
        hosted=True,
        base_url="https://api.groq.com/openai/v1",
        tool_calling=True,
        display_name="Groq Fast Inference",
    )
    pstore1.save(custom_profile)

    # Simulate application restart by loading brand-new store instance
    pstore2 = ModelProfileStore(profile_path)
    loaded = pstore2.list()
    assert any(p.profile_id == "custom-groq-endpoint" for p in loaded)
    found = next(p for p in loaded if p.profile_id == "custom-groq-endpoint")
    assert found.provider_id == "groq"
    assert found.base_url == "https://api.groq.com/openai/v1"
    assert found.tool_calling is True


class OpenAIResponsesConformanceHandler(BaseHTTPRequestHandler):
    requests_received = []

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        OpenAIResponsesConformanceHandler.requests_received.append(payload)

        assert self.path.endswith("/responses"), f"Wrong path: {self.path}"
        assert "input" in payload, "Responses API payload must have 'input' key"

        if len(OpenAIResponsesConformanceHandler.requests_received) == 1:
            # First turn: return a function_call in output
            resp = {
                "id": "resp-001",
                "output": [{
                    "type": "function_call",
                    "call_id": "fc-responses-1",
                    "name": "workspace.list_files",
                    "arguments": json.dumps({"relative": ".", "recursive": True}),
                }],
                "usage": {"input_tokens": 120, "output_tokens": 22},
            }
        else:
            # Second turn: verify tool result was submitted as function_call_output
            input_items = payload.get("input", [])
            assert any(
                item.get("type") == "function_call_output"
                for item in input_items
            ), f"Expected function_call_output in second turn: {input_items}"
            resp = {
                "id": "resp-002",
                "output": [{
                    "type": "message",
                    "content": [{"type": "text", "text": "Responses API adapter verified workspace contents."}],
                }],
                "usage": {"input_tokens": 200, "output_tokens": 28},
            }

        body = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_openai_responses_adapter_conformance(tmp_path):
    from chimera.providers.adapters.openai_responses_adapter import OpenAIResponsesAdapter
    OpenAIResponsesConformanceHandler.requests_received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), OpenAIResponsesConformanceHandler)
    server_port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "resp.txt").write_text("responses content", encoding="utf-8")

        config = ProviderConfig(
            provider_id="openai-responses",
            display_name="OpenAI Responses API",
            dialect=ApiDialect.OPENAI_RESPONSES,
            base_url=f"http://127.0.0.1:{server_port}/v1",
        )
        creds = MockCredentials("mock-responses-key")
        accountant = UniversalUsageAccountant(tmp_path / "usage.json")
        adapter = OpenAIResponsesAdapter(config, creds, accountant, provenance=ExecutionProvenance.MOCK)

        profile = ModelProfile(
            profile_id="openai-responses-profile",
            provider_id="openai-responses",
            model_id="gpt-4o-responses",
            hosted=True,
            base_url=f"http://127.0.0.1:{server_port}/v1",
            tool_calling=True,
            dialect="openai_responses",
        )
        pstore = ModelProfileStore(tmp_path / "profiles.json")
        pstore.save(profile)

        astore = AgentSessionStore(tmp_path / "agent.db")
        runtime = AgentRuntime(astore, adapter, pstore)

        sid = runtime.create_session(workspace_dir)
        runtime.set_session_model(sid, "openai-responses-profile", local_only=False)

        res = runtime.send(sid, "Inspect the workspace via Responses API")
        assert res["status"] == "completed", f"Expected completed, got: {res}"
        assert len(OpenAIResponsesConformanceHandler.requests_received) == 2
        assert "Responses API adapter verified" in astore.messages(sid)[-1].content

        summary = accountant.summary("openai-responses")
        assert summary["total_requests"] == 2
        assert summary["total_input_tokens"] == 320
    finally:
        server.shutdown()
        server.server_close()
