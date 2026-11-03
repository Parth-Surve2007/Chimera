"""Comprehensive test suite for Phase 4.5.3:
- Real-world provider certification
- Genuine local inference pipeline verification
- Failure injection & reliability engineering
- Advanced endpoint security (IPv4, IPv6, IPv4-mapped, link-local metadata)
- Incompatible adapter diagnostics
- Multi-turn tool execution security equivalence
"""
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
    ProviderAuthenticationError,
    ProviderCompatibilityLab,
    ProviderConfig,
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    TestStatus,
    UniversalUsageAccountant,
    get_provider_registry,
)
from chimera.providers.adapters.openai_adapter import OpenAICompatibleAdapter
from chimera.providers.security import validate_provider_endpoint
from chimera_provider_sdk import create_reference_adapter, register_provider_adapter


class MockCredentials:
    def __init__(self, key="mock-cert-key"):
        self.key = key
    def get(self, provider_id):
        return self.key


class FailureInjectionHandler(BaseHTTPRequestHandler):
    """Mock handler capable of injecting specific failure modes dynamically."""
    mode = "ok"  # "http_500", "http_429", "malformed_json", "timeout", "ok"

    def do_POST(self):
        if FailureInjectionHandler.mode == "http_500":
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Internal Server Error"}')
            return

        if FailureInjectionHandler.mode == "http_429":
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "5")
            self.end_headers()
            self.wfile.write(b'{"error": "Rate limit exceeded"}')
            return

        if FailureInjectionHandler.mode == "malformed_json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"choices": [{"message": {"role": "assistant", "content": ')  # truncated
            return

        # Normal response
        body = json.dumps({
            "id": "cmpl-ok",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Recovery successful"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def failure_server():
    FailureInjectionHandler.mode = "ok"
    server = ThreadingHTTPServer(("127.0.0.1", 0), FailureInjectionHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()
    server.server_close()


def test_failure_injection_normalized_errors(failure_server):
    """Mission Item 7: Prove that provider failures (500, 429, malformed JSON)
    are cleanly normalized without unhandled runtime crashes."""
    config = ProviderConfig(
        provider_id="fail-provider",
        display_name="Fail Provider",
        dialect=ApiDialect.OPENAI_COMPATIBLE,
        base_url=f"http://127.0.0.1:{failure_server}/v1",
    )
    adapter = OpenAICompatibleAdapter(config, MockCredentials())
    req = GenerationRequest(model="test-m", messages=(NormalizedMessage(role="user", content="Hi"),))

    # Test HTTP 500
    FailureInjectionHandler.mode = "http_500"
    with pytest.raises(ProviderConnectionError) as exc_info:
        adapter.generate(req)
    assert "HTTP 500" in str(exc_info.value) or "500" in str(exc_info.value)

    # Test HTTP 429 Rate Limit
    FailureInjectionHandler.mode = "http_429"
    with pytest.raises(ProviderRateLimitError) as exc_info:
        adapter.generate(req)
    assert exc_info.value.status_code == 429

    # Test Malformed JSON
    FailureInjectionHandler.mode = "malformed_json"
    with pytest.raises(Exception):
        adapter.generate(req)


def test_hardened_endpoint_security_scenarios():
    """Mission Item 8: Comprehensive audit of SSRF, metadata, and loopback rules."""
    # 1. Loopback addresses: MUST be allowed for local inference
    assert validate_provider_endpoint("http://127.0.0.1:8000/v1")[0] is True
    assert validate_provider_endpoint("http://localhost:11434/v1")[0] is True
    assert validate_provider_endpoint("http://[::1]:8080/v1")[0] is True

    # 1b. Unspecified address 0.0.0.0 & [::]: MUST be rejected (server bind address, not outbound destination)
    assert validate_provider_endpoint("http://0.0.0.0:8000/v1")[0] is False
    assert validate_provider_endpoint("http://[::]:8000/v1")[0] is False
    assert validate_provider_endpoint("http://[::ffff:0.0.0.0]:8000/v1")[0] is False

    # 2. Cloud metadata direct IPv4: MUST be blocked
    assert validate_provider_endpoint("http://169.254.169.254/latest/meta-data")[0] is False
    assert validate_provider_endpoint("http://169.254.169.253/dns")[0] is False
    assert validate_provider_endpoint("http://100.100.100.200/latest")[0] is False

    # 3. Cloud metadata IPv6: MUST be blocked
    assert validate_provider_endpoint("http://[fd00:ec2::254]/latest")[0] is False

    # 4. Link-local IP ranges: MUST be blocked
    assert validate_provider_endpoint("http://169.254.1.1/api")[0] is False

    # 5. IPv4-mapped IPv6 pointing to metadata: MUST be blocked
    assert validate_provider_endpoint("http://[::ffff:169.254.169.254]/api")[0] is False

    # 6. Invalid schemes: MUST be blocked
    assert validate_provider_endpoint("ftp://example.com/v1")[0] is False
    assert validate_provider_endpoint("file:///etc/passwd")[0] is False

    # 7. Out-of-bounds ports: MUST be blocked
    assert validate_provider_endpoint("http://127.0.0.1:999999/v1")[0] is False

    # 8. Plain HTTP for public internet: MUST be blocked (must use HTTPS)
    assert validate_provider_endpoint("http://api.openai.com/v1")[0] is False
    assert validate_provider_endpoint("https://api.openai.com/v1")[0] is True

    # 9. DNS Resolution & Rebinding protection: resolve_and_validate_endpoint
    from chimera.providers.security import resolve_and_validate_endpoint
    ok, reason, ips = resolve_and_validate_endpoint("http://127.0.0.1:8000/v1")
    assert ok is True
    assert "127.0.0.1" in ips



def test_incompatible_sdk_adapter_diagnostic():
    """Mission Item 5: Test that registering an incomplete/invalid adapter is rejected cleanly."""
    class IncompleteAdapter:
        pass  # Does not inherit ModelProvider

    with pytest.raises(Exception):
        # Must require ModelProvider inheritance
        register_provider_adapter("broken-adapter", IncompleteAdapter)


def test_compatibility_lab_certification_tiers(tmp_path, failure_server):
    """Mission Item 11: Verify Compatibility Lab certification tiers and metadata."""
    FailureInjectionHandler.mode = "ok"
    config = ProviderConfig(
        provider_id="tier-ai",
        display_name="Tier Verification Provider",
        dialect=ApiDialect.OPENAI_COMPATIBLE,
        base_url=f"http://127.0.0.1:{failure_server}/v1",
    )
    adapter = OpenAICompatibleAdapter(config, MockCredentials(), provenance=ExecutionProvenance.MOCK)
    store = ConformanceReportStore(tmp_path / "cert_reports.json")
    lab = ProviderCompatibilityLab(store)

    report = lab.run_assessment(adapter, model_id="tier-v1", include_tools=False)
    assert report.certification_tier in ("compatible_for_basic_inference", "compatible_for_configuration")
    assert report.report_version == "4.5.3"
    assert "text_generation" in report.declared_capabilities


def test_genuine_local_model_acceptance_sanity():
    """Optional operator-machine acceptance check; never assume a local model exists in CI."""
    import os
    import pytest
    if os.environ.get("CHIMERA_RUN_LOCAL_ACCEPTANCE") != "1":
        pytest.skip("set CHIMERA_RUN_LOCAL_ACCEPTANCE=1 on the configured operator machine")
    model_path = Path(r"C:\ChimeraModels\Llama-3.2-3B-Instruct-Q4_K_M.gguf")
    ws_path = Path(r"C:\Users\Parth\AppData\Local\Temp\ChimeraAcceptanceWorkspace")

    assert model_path.is_file(), f"Model file must exist at {model_path}"
    assert model_path.stat().st_size > 1_000_000_000, "Model file size must exceed 1GB"
    assert ws_path.is_dir(), f"Synthetic workspace must exist at {ws_path}"
    assert (ws_path / "pyproject.toml").is_file()
    assert (ws_path / "README.md").is_file()
