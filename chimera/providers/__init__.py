"""Chimera Universal Model Provider Architecture package.

Exports core provider contracts, capability structures, usage accounting,
adapter implementations, and the centralized provider registry.
"""
from __future__ import annotations

from .schemas import (
    ApiDialect,
    CapabilityStatus,
    ExecutionProvenance,
    GenerationRequest,
    GenerationResponse,
    NormalizedMessage,
    StreamEvent,
    ToolCall,
    ToolResult,
    UsageMetrics,
)
from .errors import (
    ProviderAuthenticationError,
    ProviderCapabilityError,
    ProviderConnectionError,
    ProviderError,
    ProviderMalformedResponseError,
    ProviderQuotaExceededError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from .capabilities import ModelCapabilities
from .usage import UniversalUsageAccountant
from .base import ModelProvider, ProviderConfig
from .registry import ProviderRegistry, get_provider_registry
from .adapters.gemini_interactions_adapter import GeminiInteractionsAdapter
from .adapters.openai_responses_adapter import OpenAIResponsesAdapter
from .compatibility_lab import (
    ConformanceReport,
    ConformanceReportStore,
    ProviderCompatibilityLab,
    TestResult,
    TestStatus,
)

# Legacy symbols for backward compatibility with existing tests and modules
from dataclasses import dataclass
from typing import Any, Callable

@dataclass(frozen=True)
class ProviderCapabilities:
    provider_id: str
    model_id: str | None
    modalities: tuple[str, ...] = ("text",)
    tool_calling: bool = False
    streaming: bool = False
    context_window: int | None = None
    reasoning: bool = False

@dataclass
class ProviderRegistration:
    capabilities: ProviderCapabilities
    factory: Callable[[], Any]
    enabled: bool = True

class LegacyProviderRegistry:
    def __init__(self):
        self._providers: dict[str, ProviderRegistration] = {}
    def register(self, registration: ProviderRegistration) -> None:
        key = registration.capabilities.provider_id
        if not key or key in self._providers:
            raise ValueError("provider ID must be unique")
        self._providers[key] = registration
    def describe(self) -> list[ProviderCapabilities]:
        return [item.capabilities for item in self._providers.values() if item.enabled]
    def select(self, provider_id: str, *, require_tools: bool = False, modality: str = "text") -> Any:
        item = self._providers.get(provider_id)
        if item is None or not item.enabled:
            raise ValueError("provider is not enabled")
        caps = item.capabilities
        if require_tools and not caps.tool_calling:
            raise ValueError("selected provider does not support tool calling")
        if modality not in caps.modalities:
            raise ValueError("selected provider does not support requested modality")
        return item.factory()
