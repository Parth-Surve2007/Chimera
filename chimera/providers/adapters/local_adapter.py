"""Local model adapter wrapping llama-server or llama.cpp cli."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..base import ModelProvider, ProviderConfig
from ..capabilities import ModelCapabilities
from ..schemas import (
    CapabilityStatus,
    ExecutionProvenance,
    GenerationRequest,
    GenerationResponse,
)
from ..usage import UniversalUsageAccountant


class LocalModelAdapter(ModelProvider):
    """Adapter for local GGUF execution via LocalModelProvider."""

    def __init__(
        self,
        config: ProviderConfig,
        local_provider: Any,
        accountant: UniversalUsageAccountant | None = None,
    ):
        super().__init__(config, accountant)
        self.local_provider = local_provider

    def validate_credentials(self) -> bool:
        return True

    def check_readiness(self) -> dict[str, Any]:
        server = getattr(self.local_provider, "server", None)
        if server:
            return server.status()
        return {"state": "ready"}

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(
            provider_id=self.config.provider_id,
            model_id=model,
            text_generation=CapabilityStatus.SUPPORTED,
            streaming=CapabilityStatus.SUPPORTED,
            tool_calling=CapabilityStatus.SUPPORTED,
            declared_by="local_gguf",
        )

    def generate(
        self,
        request: GenerationRequest,
        cancel_event: Any = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> GenerationResponse:
        # Delegate to local_provider decide/generate
        # When decide is called on local provider, it formats grammar-constrained JSON
        messages_dicts = [
            {"role": m.role, "content": m.content}
            for m in request.messages
        ]
        result = self.local_provider.generate(
            messages_dicts,
            cancel_event=cancel_event,
            on_delta=on_delta,
        )
        content = result.get("text", "")
        usage = self.accountant.record_usage(
            provider_id=self.config.provider_id,
            model_id=request.model,
            input_tokens=len(str(messages_dicts)) // 4,
            output_tokens=len(content) // 4,
            duration_ms=result.get("duration_ms", 0),
            task_id=request.task_id,
            session_id=request.session_id,
            provenance=ExecutionProvenance.LIVE_LOCAL,
        )
        return GenerationResponse(
            content=content,
            tool_calls=(),
            usage=usage,
            model=request.model,
            provider_id=self.config.provider_id,
        )

    def decide(
        self,
        messages: list[Any],
        catalog: dict[str, Any],
        session: dict[str, Any] | None = None,
        cancel_event: Any = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        # Preserve existing local grammar decide method on local_provider
        if hasattr(self.local_provider, "decide"):
            return self.local_provider.decide(
                messages,
                catalog,
                session=session,
                cancel_event=cancel_event,
                on_delta=on_delta,
            )
        return super().decide(messages, catalog, session=session, cancel_event=cancel_event, on_delta=on_delta)
