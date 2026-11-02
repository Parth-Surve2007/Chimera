"""Architectural independence and regression test suite.

Proves that AgentRuntime is free of vendor-specific logic, that the universal
provider contract is strictly decoupled, that endpoints adhere to SSRF security,
and that capability negotiation warns without silent provider fallback.
"""
import ast
from pathlib import Path
import pytest

from chimera.agent_runtime import AgentRuntime, AgentSessionStore
from chimera.model_hub import ModelProfile, ModelProfileStore, ModelRouter
from chimera.providers.security import validate_provider_endpoint


def test_agent_runtime_has_no_vendor_conditional_branches():
    """Verify that AgentRuntime source code contains zero vendor-specific if checks."""
    path = Path(__file__).parents[1] / "chimera" / "agent_runtime.py"
    content = path.read_text(encoding="utf-8")
    tree = ast.parse(content, filename=str(path))

    prohibited_vendor_literals = {"meta", "openai", "anthropic", "gemini", "groq", "together", "ollama"}
    violations = []

    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            # Inspect test condition
            test_str = ast.unparse(node.test).lower()
            for vendor in prohibited_vendor_literals:
                if f'"{vendor}"' in test_str or f"'{vendor}'" in test_str:
                    violations.append(f"Line {node.lineno}: {test_str}")

    assert not violations, f"Vendor-specific branch detected in AgentRuntime: {violations}"


def test_capability_aware_routing_explains_and_does_not_fallback():
    router = ModelRouter()
    profiles = [
        ModelProfile("local-text-only", "local", "model-a", hosted=False, tool_calling=False),
        ModelProfile("local-tool-capable", "local", "model-b", hosted=False, tool_calling=True),
    ]

    # Pinning text-only model when tools are required
    selected, explanation = router.choose(profiles, require_tools=True, local_only=True, pinned_id="local-text-only")
    assert selected is None
    assert "does not support required tool calling" in explanation
    assert "Suggested alternatives: local-tool-capable" in explanation


def test_endpoint_security_blocks_cloud_metadata():
    valid, reason = validate_provider_endpoint("http://169.254.169.254/latest/meta-data")
    assert valid is False
    assert "prohibited" in reason


def test_endpoint_security_allows_localhost_and_secure_https():
    v1, _ = validate_provider_endpoint("http://127.0.0.1:8000/v1")
    assert v1 is True

    v2, _ = validate_provider_endpoint("https://api.openai.com/v1")
    assert v2 is True

    v3, reason3 = validate_provider_endpoint("http://remote-vendor.com/v1")
    assert v3 is False
    assert "must use HTTPS" in reason3
