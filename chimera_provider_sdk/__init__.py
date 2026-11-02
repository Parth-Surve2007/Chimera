"""Chimera Provider Adapter SDK.

Provides contracts, schemas, and helper utilities for building and registering
third-party model provider adapters without modifying Chimera's AgentRuntime
or core execution pipeline.
"""
from __future__ import annotations

from typing import Any, Callable, Type

# Core contracts
from chimera.providers.base import ModelProvider, ProviderConfig
from chimera.providers.capabilities import ModelCapabilities
from chimera.providers.schemas import (
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
from chimera.providers.errors import (
    ProviderAuthenticationError,
    ProviderCapabilityError,
    ProviderConnectionError,
    ProviderError,
    ProviderMalformedResponseError,
    ProviderQuotaExceededError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from chimera.providers.usage import UniversalUsageAccountant
from chimera.providers.security import validate_provider_endpoint
from chimera.providers.registry import ProviderRegistry, get_provider_registry

__all__ = [
    # Base
    "ModelProvider",
    "ProviderConfig",
    "ModelCapabilities",
    # Schemas
    "ApiDialect",
    "CapabilityStatus",
    "ExecutionProvenance",
    "GenerationRequest",
    "GenerationResponse",
    "NormalizedMessage",
    "StreamEvent",
    "ToolCall",
    "ToolResult",
    "UsageMetrics",
    # Errors
    "ProviderError",
    "ProviderAuthenticationError",
    "ProviderConnectionError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "ProviderQuotaExceededError",
    "ProviderCapabilityError",
    "ProviderMalformedResponseError",
    # Usage & Security
    "UniversalUsageAccountant",
    "validate_provider_endpoint",
    # Registry & Helper
    "ProviderRegistry",
    "get_provider_registry",
    "register_provider_adapter",
    "create_reference_adapter",
]


def register_provider_adapter(
    provider_id: str,
    adapter_class: Type[ModelProvider],
    dialect: ApiDialect = ApiDialect.CUSTOM,
) -> None:
    """Convenience helper to safely register an external adapter in Chimera."""
    if not isinstance(adapter_class, type) or not issubclass(adapter_class, ModelProvider):
        raise TypeError(
            f"Adapter '{adapter_class}' must be a subclass of chimera.providers.base.ModelProvider. "
            "Please inherit from ModelProvider and implement its abstract methods."
        )
    registry = get_provider_registry()
    registry.register_adapter(dialect, adapter_class)
    # Also register factory for direct provider_id lookup
    registry.register_custom_factory(
        provider_id,
        lambda config, creds: adapter_class(config, credentials=creds),
    )



def create_reference_adapter(
    provider_id: str = "example-custom",
    display_name: str = "Example Reference Adapter",
) -> ModelProvider:
    """Instantiate a minimal reference adapter showing standard compliance."""
    from .reference_adapter import ReferenceEchoAdapter
    config = ProviderConfig(
        provider_id=provider_id,
        display_name=display_name,
        dialect=ApiDialect.CUSTOM,
        base_url="http://127.0.0.1:8000/v1",
    )
    return ReferenceEchoAdapter(config)
