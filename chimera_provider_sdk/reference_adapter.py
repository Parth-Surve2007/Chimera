"""Reference Provider Adapter implementation for SDK developers."""
from __future__ import annotations

import json
import time
from typing import Any, Callable

from chimera.providers.base import ModelProvider, ProviderConfig
from chimera.providers.capabilities import ModelCapabilities
from chimera.providers.schemas import (
    CapabilityStatus,
    ExecutionProvenance,
    GenerationRequest,
    GenerationResponse,
    ToolCall,
)


class ReferenceEchoAdapter(ModelProvider):
    """A minimal, compliant reference adapter demonstrating the Chimera contract.
    
    Can be used by developers as a template to connect novel inference engines.
    """

    def __init__(
        self,
        config: ProviderConfig,
        credentials: Any = None,
        accountant: Any = None,
        provenance: ExecutionProvenance = ExecutionProvenance.FIXTURE,
    ):
        super().__init__(config, accountant)
        self.credentials = credentials
        self.provenance = provenance

    def validate_credentials(self) -> bool:
        if self.config.auth_type == "none":
            return True
        if self.credentials:
            try:
                return bool(self.credentials.get(self.config.provider_id))
            except Exception:
                pass
        return True

    def check_readiness(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "provider_id": self.config.provider_id,
            "dialect": "reference_echo",
            "readiness": "ok",
        }

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(
            provider_id=self.config.provider_id,
            model_id=model,
            text_generation=CapabilityStatus.SUPPORTED,
            streaming=CapabilityStatus.SUPPORTED,
            tool_calling=CapabilityStatus.SUPPORTED,
            reasoning_metadata=CapabilityStatus.SUPPORTED,
            input_token_accounting=CapabilityStatus.SUPPORTED,
            output_token_accounting=CapabilityStatus.SUPPORTED,
            declared_by="reference_sdk_template",
        )

    def generate(
        self,
        request: GenerationRequest,
        cancel_event: Any = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> GenerationResponse:
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("inference cancelled")

        # Check if the prompt requests a tool call
        last_msg = request.messages[-1] if request.messages else None
        user_prompt = last_msg.content if (last_msg and last_msg.role == "user") else ""

        tool_calls: list[ToolCall] = []
        content = ""

        # If tools are provided and user prompt mentions workspace files
        if request.tools and ("list" in user_prompt.lower() or "file" in user_prompt.lower()):
            tool_calls.append(ToolCall(
                id="ref_call_101",
                name="workspace.list_files",
                arguments={"relative": ".", "recursive": True},
            ))
        elif last_msg and last_msg.role == "tool":
            # Continuation turn with tool result
            content = "Reference adapter confirmed tool execution successfully."
            if on_delta:
                on_delta(content)
        else:
            content = f"Echo from {self.config.provider_id}: {user_prompt}"
            if on_delta:
                on_delta(content)

        usage = self.accountant.record_usage(
            provider_id=self.config.provider_id,
            model_id=request.model,
            input_tokens=10,
            output_tokens=len(content) // 4 if content else 5,
            task_id=request.task_id,
            session_id=request.session_id,
            provenance=self.provenance,
        )

        return GenerationResponse(
            content=content,
            tool_calls=tuple(tool_calls),
            usage=usage,
            model=request.model,
            provider_id=self.config.provider_id,
        )
