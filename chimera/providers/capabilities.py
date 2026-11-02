"""Comprehensive model and provider capability negotiation."""
from __future__ import annotations

from dataclasses import dataclass, field
from .schemas import CapabilityStatus


@dataclass(frozen=True)
class ModelCapabilities:
    provider_id: str
    model_id: str
    text_generation: CapabilityStatus = CapabilityStatus.SUPPORTED
    streaming: CapabilityStatus = CapabilityStatus.UNKNOWN
    tool_calling: CapabilityStatus = CapabilityStatus.UNKNOWN
    parallel_tool_calls: CapabilityStatus = CapabilityStatus.UNKNOWN
    structured_output: CapabilityStatus = CapabilityStatus.UNKNOWN
    vision_input: CapabilityStatus = CapabilityStatus.UNSUPPORTED
    audio_input: CapabilityStatus = CapabilityStatus.UNSUPPORTED
    audio_output: CapabilityStatus = CapabilityStatus.UNSUPPORTED
    video_input: CapabilityStatus = CapabilityStatus.UNSUPPORTED
    reasoning_metadata: CapabilityStatus = CapabilityStatus.UNSUPPORTED
    context_window: int | None = None
    input_token_accounting: CapabilityStatus = CapabilityStatus.SUPPORTED
    output_token_accounting: CapabilityStatus = CapabilityStatus.SUPPORTED
    cached_token_accounting: CapabilityStatus = CapabilityStatus.UNKNOWN
    computer_use: CapabilityStatus = CapabilityStatus.UNSUPPORTED
    declared_by: str = "default"  # "declared", "probed", "manual_override"

    def is_tool_calling_supported(self) -> bool:
        return self.tool_calling == CapabilityStatus.SUPPORTED

    def is_streaming_supported(self) -> bool:
        return self.streaming == CapabilityStatus.SUPPORTED

    def explain_unsupported(self, required_feature: str) -> str | None:
        status = getattr(self, required_feature, None)
        if status == CapabilityStatus.UNSUPPORTED:
            return f"Feature '{required_feature}' is not supported by model '{self.model_id}'."
        if status == CapabilityStatus.CONFIGURATION_REQUIRED:
            return f"Feature '{required_feature}' requires specific provider configuration."
        return None
