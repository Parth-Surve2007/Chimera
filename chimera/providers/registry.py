"""Universal Provider Registry and Adapter Resolver."""
from __future__ import annotations

import os
from typing import Any, Callable, Type
from .base import ModelProvider, ProviderConfig
from .capabilities import ModelCapabilities
from .schemas import ApiDialect, CapabilityStatus, ExecutionProvenance
from .usage import UniversalUsageAccountant

from .adapters.openai_adapter import OpenAICompatibleAdapter
from .adapters.openai_responses_adapter import OpenAIResponsesAdapter
from .adapters.anthropic_adapter import AnthropicMessagesAdapter
from .adapters.gemini_adapter import GeminiAdapter
from .adapters.gemini_interactions_adapter import GeminiInteractionsAdapter
from .adapters.meta_adapter import MetaModelAdapter
from .adapters.local_adapter import LocalModelAdapter


class ProviderRegistry:
    """Central registry and factory for provider adapters."""

    def __init__(self, accountant: UniversalUsageAccountant | None = None):
        self.accountant = accountant or UniversalUsageAccountant()
        self._adapters: dict[ApiDialect, Type[ModelProvider]] = {
            ApiDialect.OPENAI_COMPATIBLE: OpenAICompatibleAdapter,
            ApiDialect.OPENAI_RESPONSES: OpenAIResponsesAdapter,
            ApiDialect.ANTHROPIC_MESSAGES: AnthropicMessagesAdapter,
            ApiDialect.GEMINI_CONTENT: GeminiAdapter,
            ApiDialect.GEMINI_INTERACTIONS: GeminiInteractionsAdapter,
            ApiDialect.META_MODEL_API: MetaModelAdapter,
            ApiDialect.LLAMA_CPP: LocalModelAdapter,
            ApiDialect.CUSTOM: OpenAICompatibleAdapter,
        }
        self._custom_factories: dict[str, Callable[[ProviderConfig, Any], ModelProvider]] = {}

    def register_adapter(self, dialect: ApiDialect, adapter_cls: Type[ModelProvider]) -> None:
        """Register a custom protocol adapter class."""
        self._adapters[dialect] = adapter_cls

    def register_custom_factory(self, provider_id: str, factory: Callable[[ProviderConfig, Any], ModelProvider]) -> None:
        """Register a dedicated factory for custom provider integrations."""
        self._custom_factories[provider_id] = factory

    # Backward compatibility with legacy ProviderRegistration interface
    def register(self, registration: Any) -> None:
        key = getattr(getattr(registration, "capabilities", None), "provider_id", None)
        if not key:
            raise ValueError("provider ID must be unique")
        if not hasattr(self, "_legacy_providers"):
            self._legacy_providers = {}
        if key in self._legacy_providers:
            raise ValueError("provider ID must be unique")
        self._legacy_providers[key] = registration

    def describe(self) -> list[Any]:
        if not hasattr(self, "_legacy_providers"):
            return []
        return [item.capabilities for item in self._legacy_providers.values() if item.enabled]

    def select(self, provider_id: str, *, require_tools: bool = False, modality: str = "text") -> Any:
        if not hasattr(self, "_legacy_providers"):
            raise ValueError("provider is not enabled")
        item = self._legacy_providers.get(provider_id)
        if item is None or not getattr(item, "enabled", True):
            raise ValueError("provider is not enabled")
        caps = item.capabilities
        if require_tools and not caps.tool_calling:
            raise ValueError("selected provider does not support tool calling")
        if modality not in caps.modalities:
            raise ValueError("selected provider does not support requested modality")
        return item.factory()

    def create_provider(
        self,
        config: ProviderConfig,
        credentials: Any = None,
        provenance: ExecutionProvenance = ExecutionProvenance.LIVE_REMOTE,
        **extra: Any,
    ) -> ModelProvider:
        """Instantiate a provider adapter according to dialect and configuration."""
        if config.provider_id in self._custom_factories:
            return self._custom_factories[config.provider_id](config, credentials)

        adapter_cls = self._adapters.get(config.dialect)
        if adapter_cls is None:
            # Default to OpenAI compatible adapter
            adapter_cls = OpenAICompatibleAdapter

        acc = extra.get("accountant") or self.accountant
        if issubclass(adapter_cls, LocalModelAdapter):
            local_p = extra.get("local_provider")
            return LocalModelAdapter(config, local_p, accountant=acc)

        return adapter_cls(config, credentials=credentials, accountant=acc, provenance=provenance)

    def resolve_from_profile(
        self,
        profile: Any,
        credentials: Any = None,
        local_provider: Any = None,
        accountant: Any = None,
        provenance: ExecutionProvenance = ExecutionProvenance.LIVE_REMOTE,
    ) -> Any:
        """Convert a legacy or new ModelProfile into a standardized ModelProvider.

        Dialect resolution order:
        1. Explicit ``profile.dialect`` string (maps to ApiDialect enum value).
        2. URL/provider_id heuristic for well-known providers.
        3. Default to OpenAI-compatible.
        """
        provider_id = getattr(profile, "provider_id", "custom")
        hosted = getattr(profile, "hosted", True)
        base_url = getattr(profile, "base_url", None)

        # 1. Explicit dialect override from profile (e.g. "gemini_interactions", "openai_responses")
        explicit_dialect_str = getattr(profile, "dialect", None)
        if explicit_dialect_str and not hosted:
            dialect: ApiDialect = ApiDialect.LLAMA_CPP
        elif explicit_dialect_str:
            try:
                dialect = ApiDialect(explicit_dialect_str)
            except ValueError:
                # Unrecognized string — fall through to heuristic below
                explicit_dialect_str = None
                dialect = ApiDialect.OPENAI_COMPATIBLE  # will be overwritten
        else:
            dialect = ApiDialect.OPENAI_COMPATIBLE  # placeholder; overwritten below

        # 2. Heuristic when no explicit dialect was provided (or it was unrecognized)
        if not explicit_dialect_str:
            if not hosted:
                dialect = ApiDialect.LLAMA_CPP
            elif provider_id == "meta" or (base_url and "meta.ai" in base_url):
                dialect = ApiDialect.META_MODEL_API
            elif provider_id == "anthropic" or (base_url and "anthropic.com" in base_url):
                dialect = ApiDialect.ANTHROPIC_MESSAGES
            elif provider_id in ("gemini", "google") or (base_url and "googleapis.com" in base_url):
                dialect = ApiDialect.GEMINI_CONTENT
            else:
                dialect = ApiDialect.OPENAI_COMPATIBLE

        # Legacy Meta path: preserve MetaModelProvider for backward compatibility
        if dialect == ApiDialect.META_MODEL_API:
            from ..meta_provider import MetaModelProvider
            return MetaModelProvider(profile, credentials, accountant=accountant)

        config = ProviderConfig(
            provider_id=provider_id,
            display_name=getattr(profile, "display_name", None) or provider_id,
            dialect=dialect,
            base_url=base_url,
            hosted=hosted,
        )
        setattr(config, "default_model", getattr(profile, "model_id", "default"))

        return self.create_provider(
            config,
            credentials=credentials,
            provenance=provenance,
            local_provider=local_provider,
            accountant=accountant,
        )


# Global singleton registry instance
_GLOBAL_REGISTRY: ProviderRegistry | None = None


def get_provider_registry() -> ProviderRegistry:
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = ProviderRegistry()
    return _GLOBAL_REGISTRY
