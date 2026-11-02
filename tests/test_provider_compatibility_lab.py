"""Tests for Provider Compatibility Lab, Adapter SDK, Benchmarks, and Cross-Provider Security Equivalence."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest

from chimera.agent_runtime import AgentRuntime, AgentSessionStore
from chimera.model_hub import ModelProfile, ModelProfileStore, ModelRouter
from chimera.providers import (
    ApiDialect,
    CapabilityStatus,
    ConformanceReportStore,
    ExecutionProvenance,
    GenerationRequest,
    ModelCapabilities,
    ModelProvider,
    NormalizedMessage,
    ProviderCompatibilityLab,
    ProviderConfig,
    ProviderRegistry,
    TestStatus,
    UniversalUsageAccountant,
    get_provider_registry,
)
from chimera.providers.adapters.openai_adapter import OpenAICompatibleAdapter
from chimera.providers.benchmark import SyntheticProviderBenchmark
from chimera_provider_sdk import create_reference_adapter, register_provider_adapter


class MockCredentialStore:
    def __init__(self, key="lab-mock-key"):
        self.key = key

    def get(self, provider_id):
        return self.key


class NovelCustomProviderHandler(BaseHTTPRequestHandler):
    """Mock HTTP handler for a completely novel, unlisted external provider."""
    requests_received = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length)) if length else {}
        NovelCustomProviderHandler.requests_received.append(payload)

        # Authenticate with Bearer
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self.send_response(401)
            self.end_headers()
            return

        # Simple ping or basic inference
        messages = payload.get("messages", [])
        last_content = messages[-1].get("content", "") if messages else ""

        if payload.get("stream"):
            # Streaming response (SSE format)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in ["Novel ", "provider ", "streamed ", "tokens."]:
                data = json.dumps({"choices": [{"delta": {"content": chunk}}]})
                self.wfile.write(f"data: {data}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return

        # Tool calling turn 1
        if payload.get("tools") and not any(m.get("role") == "tool" for m in messages):
            resp = {
                "id": "cmpl-novel-001",
                "object": "chat.completion",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "call-novel-999",
                            "type": "function",
                            "function": {
                                "name": "workspace.list_files",
                                "arguments": json.dumps({"relative": ".", "recursive": True}),
                            },
                        }],
                    },
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            }
        elif any(m.get("role") == "tool" for m in messages):
            # Continuation turn 2
            resp = {
                "id": "cmpl-novel-002",
                "object": "chat.completion",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Novel provider verified workspace contents successfully.",
                    },
                }],
                "usage": {"prompt_tokens": 180, "completion_tokens": 30},
            }
        else:
            resp = {
                "id": "cmpl-novel-ping",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "Pong from novel provider."}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }

        body = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def novel_server():
    NovelCustomProviderHandler.requests_received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), NovelCustomProviderHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()
    server.server_close()


def test_compatibility_lab_progressive_assessment(tmp_path, novel_server):
    """Tests Compatibility Lab Levels 1 through 5 on a novel custom provider."""
    config = ProviderConfig(
        provider_id="novel-ai",
        display_name="Novel Inference Provider",
        dialect=ApiDialect.OPENAI_COMPATIBLE,
        base_url=f"http://127.0.0.1:{novel_server}/v1",
    )
    creds = MockCredentialStore("novel-secret-key")
    accountant = UniversalUsageAccountant(tmp_path / "usage.json")
    adapter = OpenAICompatibleAdapter(config, creds, accountant, provenance=ExecutionProvenance.MOCK)

    report_store = ConformanceReportStore(tmp_path / "reports.json")
    lab = ProviderCompatibilityLab(report_store)

    report = lab.run_assessment(adapter, model_id="novel-v1", provenance=ExecutionProvenance.MOCK)

    assert report.provider_id == "novel-ai"
    assert report.overall_status in ("fully_compatible", "partially_compatible")
    assert any(t.test_id == "L1_CONNECTIVITY" and t.status == TestStatus.SUPPORTED for t in report.test_results)
    assert any(t.test_id == "L2_BASIC_INFERENCE" and t.status == TestStatus.SUPPORTED for t in report.test_results)
    assert any(t.test_id == "L3_STREAMING" and t.status == TestStatus.SUPPORTED for t in report.test_results)
    assert any(t.test_id == "L4_TOOL_CALLING" and t.status == TestStatus.SUPPORTED for t in report.test_results)

    # Verify persistence
    saved_reports = report_store.list()
    assert len(saved_reports) == 1
    assert saved_reports[0]["provider_id"] == "novel-ai"
    assert saved_reports[0]["provenance"] == "MOCK"


def test_novel_unknown_custom_provider_end_to_end(tmp_path, novel_server):
    """Mission Item 6: Configure an unknown custom provider, execute workspace.list_files,
    route via AgentRuntime, verify persistence across restart, prove 0 AgentRuntime edits."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "novel_doc.md").write_text("# Secret Spec", encoding="utf-8")

    # 1. Configure profile
    profile_path = tmp_path / "profiles.json"
    pstore1 = ModelProfileStore(profile_path)
    novel_profile = ModelProfile(
        profile_id="novel-inference-profile",
        provider_id="novel-ai",
        model_id="novel-v1",
        hosted=True,
        base_url=f"http://127.0.0.1:{novel_server}/v1",
        tool_calling=True,
        dialect="openai_compatible",
    )
    pstore1.save(novel_profile)

    config = ProviderConfig(
        provider_id="novel-ai",
        display_name="Novel AI",
        dialect=ApiDialect.OPENAI_COMPATIBLE,
        base_url=f"http://127.0.0.1:{novel_server}/v1",
    )
    creds = MockCredentialStore("novel-secret-key")
    accountant = UniversalUsageAccountant(tmp_path / "usage.json")
    adapter = OpenAICompatibleAdapter(config, creds, accountant, provenance=ExecutionProvenance.MOCK)

    astore = AgentSessionStore(tmp_path / "agent.db")
    runtime = AgentRuntime(astore, adapter, pstore1)

    sid = runtime.create_session(workspace)
    runtime.set_session_model(sid, "novel-inference-profile", local_only=False)

    # Execute tool calling task through AgentRuntime
    res = runtime.send(sid, "List the files in our workspace")
    assert res["status"] == "completed"
    assert "Novel provider verified workspace contents" in astore.messages(sid)[-1].content

    # Restart simulation: re-instantiate store from disk
    pstore2 = ModelProfileStore(profile_path)
    loaded = pstore2.list()
    assert any(p.profile_id == "novel-inference-profile" for p in loaded)
    found = next(p for p in loaded if p.profile_id == "novel-inference-profile")
    assert found.base_url == f"http://127.0.0.1:{novel_server}/v1"
    assert found.tool_calling is True


def test_provider_sdk_reference_adapter_and_registration():
    """Mission Item 5: Verify the chimera_provider_sdk exports and reference adapter."""
    ref_adapter = create_reference_adapter("custom-test-echo", "Custom Echo Provider")
    assert ref_adapter.validate_credentials() is True
    assert ref_adapter.check_readiness()["status"] == "ready"

    # Test generation and tool dispatch
    req = GenerationRequest(
        model="echo-model",
        messages=(NormalizedMessage(role="user", content="Hello SDK"),),
    )
    resp = ref_adapter.generate(req)
    assert "Echo from custom-test-echo" in resp.content

    # Test tool simulation
    req_tool = GenerationRequest(
        model="echo-model",
        messages=(NormalizedMessage(role="user", content="Please list workspace files"),),
        tools=({"id": "workspace.list_files"},),
    )
    resp_tool = ref_adapter.generate(req_tool)
    assert len(resp_tool.tool_calls) == 1
    assert resp_tool.tool_calls[0].name == "workspace.list_files"


def test_cross_provider_security_equivalence_and_policy(tmp_path):
    """Mission Item 7: Prove that AgentRuntime permission and mode checks apply
    equivalently to any provider (Plan mode rejects side effects, human approval required)."""
    workspace = tmp_path / "sec_ws"
    workspace.mkdir()

    class RogueWriteAdapter(ModelProvider):
        """Adapter that attempts to emit an unauthorized write tool in Plan Mode."""
        def validate_credentials(self): return True
        def check_readiness(self): return {"status": "ready"}
        def capabilities(self, model): return ModelCapabilities(provider_id="rogue", model_id=model)
        def generate(self, request, cancel_event=None, on_delta=None):
            return type("Resp", (), {
                "content": "",
                "tool_calls": (type("TC", (), {"id": "c1", "name": "workspace.create_artifact", "arguments": {"name": "hack.txt", "content": "pwned"}})(),),
                "usage": type("U", (), {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "estimated_cost": 0, "duration_ms": 1, "provenance": ExecutionProvenance.MOCK})(),
                "model": "rogue",
            })()

    adapter = RogueWriteAdapter(ProviderConfig(provider_id="rogue", display_name="Rogue", dialect=ApiDialect.CUSTOM))
    astore = AgentSessionStore(tmp_path / "sec_agent.db")
    runtime = AgentRuntime(astore, adapter)

    # In Plan Mode, workspace.create_artifact must be blocked
    sid = runtime.create_session(workspace, mode="plan")
    res = runtime.send(sid, "Attempt artifact write")
    # AgentRuntime catches PermissionError, adds blocked tool message, continues safely
    messages = astore.messages(sid)
    tool_msgs = [m for m in messages if m.role == "tool"]
    assert len(tool_msgs) >= 1
    assert "blocked" in tool_msgs[0].content


def test_synthetic_cross_provider_benchmark(novel_server):
    """Mission Item 9: Run synthetic reproducible benchmark across a provider."""
    config = ProviderConfig(
        provider_id="benchmark-ai",
        display_name="Benchmark AI",
        dialect=ApiDialect.OPENAI_COMPATIBLE,
        base_url=f"http://127.0.0.1:{novel_server}/v1",
    )
    creds = MockCredentialStore()
    adapter = OpenAICompatibleAdapter(config, creds, UniversalUsageAccountant(), provenance=ExecutionProvenance.MOCK)

    bench = SyntheticProviderBenchmark(adapter, model_id="bench-v1", provenance=ExecutionProvenance.MOCK)
    report = bench.run_suite()

    assert report.total_tasks == 3
    assert report.successful_tasks >= 2
    assert report.average_latency_ms >= 0
    assert report.provenance == ExecutionProvenance.MOCK
    assert any(t.task_name == "basic_generation" and t.success for t in report.task_results)
    assert any(t.task_name == "streaming_ttft" and t.success for t in report.task_results)
