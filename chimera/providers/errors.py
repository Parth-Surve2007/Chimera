"""Normalized error hierarchy for Chimera model providers."""
from __future__ import annotations


class ProviderError(Exception):
    """Base class for all normalized provider errors."""

    def __init__(self, message: str, provider_id: str = "", model_id: str = "", status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.provider_id = provider_id
        self.model_id = model_id
        self.status_code = status_code


class ProviderAuthenticationError(ProviderError):
    """Missing or invalid credentials."""
    pass


class ProviderConnectionError(ProviderError):
    """Network connection failure, DNS resolution failure, or unreachable host."""
    pass


class ProviderTimeoutError(ProviderError):
    """Provider request timed out."""
    pass


class ProviderRateLimitError(ProviderError):
    """Provider rate limit or quota exceeded."""
    pass


class ProviderQuotaExceededError(ProviderRateLimitError):
    """Hard credit or budget limit exceeded."""
    pass


class ProviderCapabilityError(ProviderError):
    """Requested capability (e.g. tool calling, streaming) is not supported by model/provider."""
    pass


class ProviderMalformedResponseError(ProviderError):
    """Provider returned response that violates dialect schema."""
    pass
