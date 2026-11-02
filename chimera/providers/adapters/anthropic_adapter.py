"""Anthropic Messages protocol adapter supporting native Messages API."""
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

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"


class AnthropicMessagesAdapter(ModelProvider):
    """Adapter for Anthropic native Messages protocol."""

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
        self.base_url = (config.base_url or DEFAULT_ANTHROPIC_BASE_URL).rstrip("/")

    def _get_api_key(self) -> str:
        key = ""
        if self.credentials:
            try:
                key = self.credentials.get(self.config.provider_id)
            except Exception:
                pass
        if not key:
            import os
            key = os.environ.get("ANTHROPIC_API_KEY", "")
        return key

    def validate_credentials(self) -> bool:
        return bool(self._get_api_key())

    def check_readiness(self) -> dict[str, Any]:
        if not self.validate_credentials():
            return {"status": "unauthenticated", "error": "ANTHROPIC_API_KEY missing"}
        return {"status": "ready", "url": self.base_url}

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
            input_token_accounting=CapabilityStatus.SUPPORTED,
            output_token_accounting=CapabilityStatus.SUPPORTED,
            cached_token_accounting=CapabilityStatus.SUPPORTED,
            declared_by="anthropic_spec",
        )

    def _format_messages_and_system(self, messages: tuple) -> tuple[str, list[dict[str, Any]]]:
        system_text = ""
        formatted = []
        for msg in messages:
            if msg.role == "system":
                system_text = (system_text + "\n" + msg.content).strip()
            elif msg.role == "tool" and msg.tool_result:
                tr = msg.tool_result
                res_content = (
                    json.dumps(tr.result, separators=(",", ":"))
                    if isinstance(tr.result, (dict, list))
                    else str(tr.result)
                )
                formatted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tr.tool_call_id,
                        "content": res_content,
                    }],
                })
            elif msg.role == "assistant" and msg.tool_calls:
                blocks = []
                if msg.content:
                    blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                formatted.append({"role": "assistant", "content": blocks})
            else:
                formatted.append({"role": msg.role, "content": msg.content})
        return system_text, formatted

    def _format_tools(self, tools: tuple) -> list[dict[str, Any]]:
        result = []
        for item in tools:
            tool_id = item.get("id", "")
            if not tool_id.startswith(("workspace.", "computer.")):
                continue
            properties = {}
            for name, kind in item.get("arguments", {}).items():
                t = "string"
                if kind is bool or kind == "boolean":
                    t = "boolean"
                elif kind in (int, "integer", "int"):
                    t = "integer"
                elif kind in (dict, "object"):
                    t = "object"
                properties[name] = {"type": t}
            result.append({
                "name": tool_id,
                "description": item.get("description", ""),
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                },
            })
        return result

    def generate(
        self,
        request: GenerationRequest,
        cancel_event: Any = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> GenerationResponse:
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("inference cancelled")

        if not self.validate_credentials():
            raise ProviderAuthenticationError("Anthropic API key missing", self.config.provider_id, request.model)

        system_text, anthropic_messages = self._format_messages_and_system(request.messages)
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": anthropic_messages,
            "max_tokens": request.max_tokens or 1024,
        }
        if system_text:
            payload["system"] = system_text

        if request.tools:
            formatted_tools = self._format_tools(request.tools)
            if formatted_tools:
                payload["tools"] = formatted_tools

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._get_api_key(),
            "anthropic-version": "2023-06-01",
            **self.config.custom_headers,
        }

        endpoint = f"{self.base_url}/messages"
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
        content_parts = []
        parsed_calls = []

        for block in data.get("content", []):
            if block.get("type") == "text":
                content_parts.append(block.get("text", ""))
                if on_delta and block.get("text"):
                    on_delta(block.get("text"))
            elif block.get("type") == "tool_use":
                parsed_calls.append(ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=block.get("input", {}),
                ))

        usage_info = data.get("usage", {})
        in_tok = usage_info.get("input_tokens", len(str(payload["messages"])) // 4)
        out_tok = usage_info.get("output_tokens", len("".join(content_parts)) // 4)
        cached_tok = usage_info.get("cache_read_input_tokens", 0)

        usage = self.accountant.record_usage(
            provider_id=self.config.provider_id,
            model_id=request.model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cached_tokens=cached_tok,
            duration_ms=duration_ms,
            task_id=request.task_id,
            session_id=request.session_id,
            provenance=self.provenance,
        )

        return GenerationResponse(
            content="".join(content_parts),
            tool_calls=tuple(parsed_calls),
            usage=usage,
            model=request.model,
            provider_id=self.config.provider_id,
        )
