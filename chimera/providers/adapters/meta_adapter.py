"""Meta Model API adapter implementing the universal ModelProvider interface."""
from __future__ import annotations

import json
from typing import Any, Callable

from ..base import ModelProvider, ProviderConfig
from ..capabilities import ModelCapabilities
from ..schemas import (
    ApiDialect,
    CapabilityStatus,
    ExecutionProvenance,
    GenerationRequest,
    GenerationResponse,
)
from ..usage import UniversalUsageAccountant
from .openai_adapter import OpenAICompatibleAdapter

DEFAULT_META_API_URL = "https://api.meta.ai/v1"


class MetaModelAdapter(ModelProvider):
    """Adapter for Meta Model API (e.g. Muse Spark 1.3)."""

    def __init__(
        self,
        config: ProviderConfig,
        credentials: Any = None,
        accountant: UniversalUsageAccountant | None = None,
        provenance: ExecutionProvenance = ExecutionProvenance.LIVE_REMOTE,
    ):
        super().__init__(config, accountant)
        self.credentials = credentials
        self.provenance = provenance
        # Ensure default custom headers preserve Meta data policy
        policy = config.privacy_mode or "standard"
        config.custom_headers.setdefault("X-Meta-Data-Policy", policy)
        self._underlying = OpenAICompatibleAdapter(config, credentials, accountant, provenance=provenance)

    def validate_credentials(self) -> bool:
        return self._underlying.validate_credentials()

    def check_readiness(self) -> dict[str, Any]:
        return self._underlying.check_readiness()

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(
            provider_id=self.config.provider_id,
            model_id=model,
            text_generation=CapabilityStatus.SUPPORTED,
            streaming=CapabilityStatus.SUPPORTED,
            tool_calling=CapabilityStatus.SUPPORTED,
            parallel_tool_calls=CapabilityStatus.SUPPORTED,
            structured_output=CapabilityStatus.SUPPORTED,
            vision_input=CapabilityStatus.SUPPORTED,
            reasoning_metadata=CapabilityStatus.SUPPORTED,
            input_token_accounting=CapabilityStatus.SUPPORTED,
            output_token_accounting=CapabilityStatus.SUPPORTED,
            cached_token_accounting=CapabilityStatus.SUPPORTED,
            declared_by="meta_model_spec",
        )

    def generate(
        self,
        request: GenerationRequest,
        cancel_event: Any = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> GenerationResponse:
        return self._underlying.generate(request, cancel_event=cancel_event, on_delta=on_delta)
