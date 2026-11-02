"""Abstract base provider contract for Chimera model adapters."""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator
from .capabilities import ModelCapabilities
from .schemas import ApiDialect, GenerationRequest, GenerationResponse, StreamEvent, ToolCall
from .usage import UniversalUsageAccountant


@dataclass
class ProviderConfig:
    provider_id: str
    display_name: str
    dialect: ApiDialect
    base_url: str | None = None
    hosted: bool = True
    auth_type: str = "bearer"  # "bearer", "api_key", "none", "header"
    credential_id: str | None = None
    custom_headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 60.0
    privacy_mode: str = "standard"


class ModelProvider(abc.ABC):
    """Universal interface that all Chimera model adapters must implement."""

    def __init__(self, config: ProviderConfig, accountant: UniversalUsageAccountant | None = None):
        self.config = config
        self.accountant = accountant or UniversalUsageAccountant()

    @abc.abstractmethod
    def validate_credentials(self) -> bool:
        """Check whether credentials are present and structurally valid. Never logs secret."""
        pass

    @abc.abstractmethod
    def check_readiness(self) -> dict[str, Any]:
        """Perform non-destructive connection and health check."""
        pass

    @abc.abstractmethod
    def capabilities(self, model: str) -> ModelCapabilities:
        """Return the declared and negotiated capabilities of the specified model."""
        pass

    @abc.abstractmethod
    def generate(
        self,
        request: GenerationRequest,
        cancel_event: Any = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> GenerationResponse:
        """Execute text generation or single/multi-tool generation."""
        pass

    def stream(
        self,
        request: GenerationRequest,
        cancel_event: Any = None,
    ) -> Iterator[StreamEvent]:
        """Optional streaming iterator; defaults to emitting single final event."""
        resp = self.generate(request, cancel_event=cancel_event)
        yield StreamEvent(delta_text=resp.content, is_done=True, usage=resp.usage)

    # Backward compatibility with existing AgentRuntime decide() interface
    def decide(
        self,
        messages: list[Any],
        catalog: dict[str, Any],
        session: dict[str, Any] | None = None,
        cancel_event: Any = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Translate AgentRuntime decide call into standardized GenerationRequest."""
        from .normalization import normalize_messages

        norm_msgs = normalize_messages(messages)
        tools = catalog.get("tools", [])
        model = getattr(self.config, "default_model", None) or "default"
        session_id = session.get("session_id") if session else None

        req = GenerationRequest(
            model=model,
            messages=tuple(norm_msgs),
            tools=tuple(tools),
            session_id=session_id,
        )
        resp = self.generate(req, cancel_event=cancel_event, on_delta=on_delta)

        metrics = {
            "provider": self.config.provider_id,
            "model": resp.model,
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "cached_tokens": resp.usage.cached_tokens,
            "estimated_cost": resp.usage.estimated_cost,
            "duration_ms": resp.usage.duration_ms,
            "provenance": resp.usage.provenance.value,
        }

        if resp.tool_calls:
            # Chimera AgentRuntime sequentially handles one tool call per turn
            call = resp.tool_calls[0]
            return {
                "type": "tool_call",
                "tool": call.name,
                "arguments": call.arguments,
                "invocation_id": call.id,
                "_metrics": metrics,
            }

        return {
            "type": "message",
            "content": resp.content,
            "_metrics": metrics,
        }
