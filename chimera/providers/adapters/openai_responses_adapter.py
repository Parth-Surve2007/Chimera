"""OpenAI-compatible Responses API adapter (POST /v1/responses)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from ..base import ModelProvider, ProviderConfig
from ..capabilities import ModelCapabilities
from ..errors import (
    ProviderAuthenticationError,
    ProviderCapabilityError,
    ProviderConnectionError,
    ProviderMalformedResponseError,
    ProviderRateLimitError,
)
from ..schemas import (
    CapabilityStatus,
    ExecutionProvenance,
    GenerationRequest,
    GenerationResponse,
    ToolCall,
)
from ..usage import UniversalUsageAccountant


class OpenAIResponsesAdapter(ModelProvider):
    """Adapter for providers offering OpenAI Responses interface (e.g. Meta Model API, newer OpenAI models)."""

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
        self.base_url = (config.base_url or "").rstrip("/")

    def _get_api_key(self) -> str:
        key = ""
        if self.credentials:
            try:
                key = self.credentials.get(self.config.provider_id)
            except Exception:
                pass
        if not key:
            import os
            key = os.environ.get(f"{self.config.provider_id.upper()}_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        return key

    def validate_credentials(self) -> bool:
        return bool(self._get_api_key())

    def check_readiness(self) -> dict[str, Any]:
        if not self.base_url:
            return {"status": "unconfigured", "error": "base_url is required"}
        if not self.validate_credentials():
            return {"status": "unauthenticated", "error": "credentials missing"}
        return {"status": "ready", "url": self.base_url, "dialect": "openai_responses"}

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
            declared_by="responses_spec",
        )

    def generate(
        self,
        request: GenerationRequest,
        cancel_event: Any = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> GenerationResponse:
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("inference cancelled")

        if not self.validate_credentials():
            raise ProviderAuthenticationError(
                f"Missing API key for provider '{self.config.provider_id}'",
                self.config.provider_id,
                request.model,
            )

        # Build responses API input array
        input_items = []
        for msg in request.messages:
            if msg.role == "tool" and msg.tool_result:
                tr = msg.tool_result
                input_items.append({
                    "type": "function_call_output",
                    "call_id": tr.tool_call_id,
                    "output": json.dumps(tr.result, separators=(",", ":")) if isinstance(tr.result, (dict, list)) else str(tr.result),
                })
            else:
                input_items.append({
                    "role": msg.role,
                    "content": msg.content,
                })

        payload: dict[str, Any] = {
            "model": request.model,
            "input": input_items,
        }
        if request.tools:
            tools_spec = []
            for item in request.tools:
                tid = item.get("id", "")
                if not tid.startswith(("workspace.", "computer.")):
                    continue
                tools_spec.append({
                    "type": "function",
                    "name": tid,
                    "description": item.get("description", ""),
                    "parameters": {"type": "object", "properties": item.get("arguments", {})},
                })
            if tools_spec:
                payload["tools"] = tools_spec

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._get_api_key()}",
            **self.config.custom_headers,
        }

        endpoint = f"{self.base_url}/responses"
        req = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST")

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                data = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ProviderAuthenticationError(f"HTTP {exc.code} Unauthorized", self.config.provider_id, request.model, exc.code)
            if exc.code == 429:
                raise ProviderRateLimitError(f"HTTP {exc.code} Rate limited", self.config.provider_id, request.model, exc.code)
            raise ProviderConnectionError(f"HTTP {exc.code} Error", self.config.provider_id, request.model, exc.code)
        except OSError as exc:
            raise ProviderConnectionError(f"Connection failed: {exc}", self.config.provider_id, request.model)

        duration_ms = int((time.perf_counter() - started) * 1000)

        content = ""
        parsed_calls = []

        # Responses schema returns output items
        output_items = data.get("output", [])
        for item in output_items:
            if item.get("type") == "message":
                for part in item.get("content", []):
                    if part.get("type") == "text":
                        text_val = part.get("text", "")
                        content += text_val
                        if on_delta and text_val:
                            on_delta(text_val)
            elif item.get("type") == "function_call":
                raw_args = item.get("arguments", {})
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args)
                    except Exception:
                        raw_args = {}
                parsed_calls.append(ToolCall(
                    id=item.get("call_id", item.get("id", "")),
                    name=item.get("name", ""),
                    arguments=raw_args,
                ))

        usage_meta = data.get("usage", {})
        in_tok = usage_meta.get("input_tokens", len(str(input_items)) // 4)
        out_tok = usage_meta.get("output_tokens", len(content) // 4)

        usage = self.accountant.record_usage(
            provider_id=self.config.provider_id,
            model_id=request.model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            duration_ms=duration_ms,
            task_id=request.task_id,
            session_id=request.session_id,
            provenance=self.provenance,
        )

        return GenerationResponse(
            content=content,
            tool_calls=tuple(parsed_calls),
            usage=usage,
            model=request.model,
            provider_id=self.config.provider_id,
        )
