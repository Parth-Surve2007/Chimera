# Chimera Universal Model Provider Integration Guide

Chimera is designed as a model-provider-agnostic AI agent platform. It decouples the core conversation loop, execution governance, and tool execution system from any individual inference vendor.

## 1. Architectural Overview

```
                                +-------------------+
                                |    User / UI      |
                                +---------+---------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                                   AgentRuntime                                    |
| - Sole execution authority                                                        |
| - Enforces Plan Mode vs Build Mode permissions                                    |
| - Normalizes tool execution, human approvals, and event audit timeline            |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                         chimera.providers.registry                                |
| - Dynamic provider resolution & capability negotiation                            |
| - Standardized GenerationRequest / GenerationResponse                             |
| - Explicit dialect routing (profile.dialect field overrides URL heuristics)       |
+--------+--------+--------+--------+--------+--------+
         |        |        |        |        |        |
         v        v        v        v        v        v
+--------+ +------+ +------+ +------+ +------+ +------+
|OpenAI  | |Anthr-| |Gemini| |Gemini| |OpenAI| | Meta |
|Compat  | |opic  | |Cont. | |Inter.| |Resp. | |Model |
+--------+ +------+ +------+ +------+ +------+ +------+
                                          |
                                          v
                                    +----------+
                                    |Local llm |
                                    |(llama.cpp)|
                                    +----------+
```

### Core Architecture Rules
1. **AgentRuntime Sole Execution Authority:** Adapters and models NEVER execute tools directly. All tool calls emitted by models are normalized into `ToolCall` and dispatched through Chimera's permission checks.
2. **Local-First Privacy:** Local GGUF execution is the default. Remote hosted providers are accessed only upon explicit user selection.
3. **No Credential Leakage:** API keys are stored in Windows Credential Manager or read from secure environment variables. Keys never enter SQLite databases, logs, or Git repositories.

---

## 2. Standardized Adapter Contract

All model adapters subclass `chimera.providers.base.ModelProvider`:

```python
from chimera.providers.base import ModelProvider, ProviderConfig
from chimera.providers.schemas import GenerationRequest, GenerationResponse
from chimera.providers.capabilities import ModelCapabilities

class MyCustomAdapter(ModelProvider):
    def validate_credentials(self) -> bool:
        """Verify credential presence without exposing secret value."""
        ...

    def check_readiness(self) -> dict:
        """Perform lightweight health/connectivity probe."""
        ...

    def capabilities(self, model: str) -> ModelCapabilities:
        """Return declared/negotiated capabilities (tools, streaming, vision)."""
        ...

    def generate(self, request: GenerationRequest, cancel_event=None, on_delta=None) -> GenerationResponse:
        """Generate response or emit normalized ToolCall instances."""
        ...
```

---

## 3. Supported API Dialects

Each dialect maps to a specific adapter class in `chimera/providers/adapters/`:

| Dialect value | Class | API Pattern |
|---|---|---|
| `openai_compatible` | `OpenAICompatibleAdapter` | `POST /v1/chat/completions` — standard bearer auth |
| `openai_responses` | `OpenAIResponsesAdapter` | `POST /v1/responses` — stateless input/output array format |
| `anthropic_messages` | `AnthropicMessagesAdapter` | `POST /v1/messages` — native Anthropic API with `x-api-key` header |
| `gemini_content` | `GeminiAdapter` | `POST /v1beta/models/...:generateContent` — Google generative language |
| `gemini_interactions` | `GeminiInteractionsAdapter` | `POST /v1beta/interactions` — server-side stateful chaining via `previous_interaction_id` |
| `meta_model_api` | `MetaModelAdapter` | Meta AI endpoint with `X-Meta-Data-Policy` privacy header |
| `llama_cpp` | `LocalModelAdapter` | Local GGUF inference via `llama-server` |

### Dialect Resolution Order

`ProviderRegistry.resolve_from_profile()` resolves the dialect in this order:

1. **Explicit `ModelProfile.dialect` field** (highest priority) — e.g. `dialect="gemini_interactions"`
2. **URL / provider_id heuristic** — `anthropic.com` → Anthropic, `googleapis.com` → Gemini generateContent, etc.
3. **Default to `openai_compatible`**

Always set `dialect` explicitly in `ModelProfile` when using `gemini_interactions` or `openai_responses` to avoid the heuristic defaulting to the wrong adapter.

---

## 4. Endpoint Security

Chimera enforces SSRF protection via `chimera.providers.security.validate_provider_endpoint()`:

- **Blocked:** Cloud metadata endpoints (e.g. `169.254.169.254`, `fd00:ec2::254`)
- **Blocked:** Common private-network hostnames pointing to known cloud metadata patterns
- **Allowed:** `localhost` / `127.0.0.1` / `::1` for local inference servers
- **Allowed:** Any HTTPS public endpoint not on the blocklist

Call `validate_provider_endpoint(url)` before constructing a custom adapter if your integration accepts arbitrary user-supplied URLs.

```python
from chimera.providers.security import validate_provider_endpoint
validate_provider_endpoint("http://169.254.169.254/latest/meta-data")  # raises ProviderConnectionError
validate_provider_endpoint("http://127.0.0.1:11434/v1")  # OK — local Ollama
```

---

## 5. Adding a Custom Provider

### Via the UI (zero-code)

1. In the **Model Intelligence Hub**, enter:
   * **Profile ID:** e.g. `my-vllm-server`
   * **Provider ID:** e.g. `vllm`
   * **Model ID:** e.g. `Qwen/Qwen2.5-7B-Instruct`
   * **Base URL:** e.g. `http://192.168.1.100:8000/v1`
   * **Dialect:** (optional) leave empty for OpenAI-compatible, or specify `gemini_interactions`, `openai_responses`, etc.
   * **Credential:** (Optional API Key)
   * **Enable Tool Calling:** Checked
2. Click **Save API**.
3. Click **Use** to pin the provider to your active session.

### Via Code

```python
from chimera.model_hub import ModelProfile, ModelProfileStore

profile = ModelProfile(
    profile_id="my-gemini-interactions",
    provider_id="gemini",
    model_id="gemini-2.5-flash",
    hosted=True,
    base_url="https://generativelanguage.googleapis.com/v1beta/interactions",
    tool_calling=True,
    dialect="gemini_interactions",  # explicit override
)
store = ModelProfileStore(Path("profiles.json"))
store.save(profile)
```

### Via Custom Adapter Class (Using Adapter SDK)

Third-party developers can build, package, and distribute adapters using `chimera_provider_sdk`:

```python
from chimera_provider_sdk import (
    ModelProvider,
    ProviderConfig,
    ModelCapabilities,
    CapabilityStatus,
    GenerationRequest,
    GenerationResponse,
    register_provider_adapter,
)

class MyCustomInferenceAdapter(ModelProvider):
    def validate_credentials(self) -> bool:
        return True

    def check_readiness(self) -> dict:
        return {"status": "ready"}

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(
            provider_id=self.config.provider_id,
            model_id=model,
            tool_calling=CapabilityStatus.SUPPORTED,
        )

    def generate(self, request: GenerationRequest, cancel_event=None, on_delta=None) -> GenerationResponse:
        # Translate to vendor protocol, receive response, normalize tool calls
        return GenerationResponse(content="Inference result")

# Register with Chimera's universal registry
register_provider_adapter("my-engine", MyCustomInferenceAdapter)
```

---

## 6. Provider Compatibility Lab

The **Provider Compatibility Lab** conducts progressive, non-destructive capability auditing across 5 distinct levels:

1. **Level 1 (Connectivity):** Hostname validation, SSRF blocklist check, authentication handshake, and ping.
2. **Level 2 (Basic Inference):** Single-prompt generation, response schema normalization, latency timing.
3. **Level 3 (Streaming):** Server-Sent Events / chunk decoding, time-to-first-token (TTFT), cancellation safety.
4. **Level 4 (Tool Calling):** JSON schema transmission, model tool call emission, continuation loop with tool results.
5. **Level 5 (Advanced Capabilities):** Context window handling, pricing accounting, and provenance tracking.

```python
from chimera.providers import ProviderCompatibilityLab, ConformanceReportStore, ExecutionProvenance

store = ConformanceReportStore(Path("data/conformance_reports.json"))
lab = ProviderCompatibilityLab(store)

report = lab.run_assessment(adapter, model_id="my-model", provenance=ExecutionProvenance.MOCK)
print(f"Status: {report.overall_status}")  # "fully_compatible" | "partially_compatible" | "incompatible"
for test in report.test_results:
    print(f"[{test.status}] {test.name}: {test.message} ({test.duration_ms} ms)")
```

---

## 7. Synthetic Benchmarking

The benchmark suite measures reproducible performance metrics across adapters:

- **Basic Generation Latency**
- **Streaming Time to First Token (TTFT)**
- **Tool Dispatch Accuracy**

```python
from chimera.providers.benchmark import SyntheticProviderBenchmark

bench = SyntheticProviderBenchmark(adapter, model_id="my-model")
metrics = bench.run_suite()
print(f"Average Latency: {metrics.average_latency_ms} ms")
print(f"Failure Rate: {metrics.failure_rate}")
```

---

## 8. Test Classification

- **Mock Tests:** `tests/test_provider_conformance.py`, `tests/test_provider_architecture.py`, and `tests/test_provider_compatibility_lab.py` spin up local loopback servers. They require zero external credentials or network connectivity and run in CI.
- **Live Integration Tests:** Opt-in remote tests (e.g. `tests/test_meta_integration.py`) require operator credentials and are explicitly isolated with `LIVE_REMOTE` provenance.
