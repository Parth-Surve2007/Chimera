"""OpenAI-compatible protocol adapter supporting standard chat completions."""
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
    ProviderTimeoutError,
)
from ..schemas import (
    CapabilityStatus,
    ExecutionProvenance,
    GenerationRequest,
    GenerationResponse,
    ToolCall,
)
from ..usage import UniversalUsageAccountant


class OpenAICompatibleAdapter(ModelProvider):
    """Adapter for any provider offering an OpenAI-compatible Chat Completions endpoint."""

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
        if not self.config.hosted:
            return True
        return bool(self._get_api_key())

    def check_readiness(self) -> dict[str, Any]:
        if not self.base_url:
            return {"status": "unconfigured", "error": "base_url is required"}
        if not self.validate_credentials():
            return {"status": "unauthenticated", "error": "credentials missing"}
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
            input_token_accounting=CapabilityStatus.SUPPORTED,
            output_token_accounting=CapabilityStatus.SUPPORTED,
            cached_token_accounting=CapabilityStatus.SUPPORTED,
            declared_by="adapter_profile",
        )

    def _format_messages(self, messages: tuple) -> list[dict[str, Any]]:
        result = []
        for msg in messages:
            if msg.role == "tool" and msg.tool_result:
                tr = msg.tool_result
                content_str = (
                    json.dumps(tr.result, separators=(",", ":"))
                    if isinstance(tr.result, (dict, list))
                    else str(tr.result)
                )
                result.append({
                    "role": "tool",
                    "tool_call_id": tr.tool_call_id,
                    "content": content_str,
                })
            elif msg.role == "assistant" and msg.tool_calls:
                tc_dicts = []
                for tc in msg.tool_calls:
                    tc_dicts.append({
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, separators=(",", ":")),
                        },
                    })
                result.append({"role": "assistant", "content": msg.content or "", "tool_calls": tc_dicts})
            else:
                result.append({"role": msg.role, "content": msg.content})
        return result

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
                "type": "function",
                "function": {
                    "name": tool_id,
                    "description": item.get("description", ""),
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "additionalProperties": False,
                    },
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
            raise ProviderAuthenticationError(
                f"Missing API key for provider '{self.config.provider_id}'",
                self.config.provider_id,
                request.model,
            )

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": self._format_messages(request.messages),
            "stream": bool(on_delta),
        }
        if request.tools:
            formatted_tools = self._format_tools(request.tools)
            if formatted_tools:
                payload["tools"] = formatted_tools
                payload["tool_choice"] = "auto"
                # If tool calling is involved, prefer non-streaming for deterministic tool call parsing
                if on_delta:
                    payload["stream"] = False

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._get_api_key()}",
            **self.config.custom_headers,
        }

        endpoint = f"{self.base_url}/chat/completions"
        req = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST")

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                if payload.get("stream"):
                    parts = []
                    usage_info = {}
                    for raw in response:
                        if cancel_event and cancel_event.is_set():
                            raise InterruptedError("inference cancelled")
                        line = raw.decode(errors="replace").strip()
                        if not line:
                            continue
                        if line.startswith("data:"):
                            line = line[5:].strip()
                            if line == "[DONE]":
                                break
                            try:
                                chunk = json.loads(line)
                                text = chunk["choices"][0].get("delta", {}).get("content", "")
                                if "usage" in chunk and chunk["usage"]:
                                    usage_info = chunk["usage"]
                            except (ValueError, KeyError, IndexError):
                                text = ""
                            if text:
                                parts.append(text)
                                if on_delta:
                                    on_delta(text)
                    content = "".join(parts)
                    tool_calls = ()
                else:
                    data = json.loads(response.read().decode())
                    choice = data["choices"][0]
                    msg = choice["message"]
                    content = msg.get("content") or ""
                    raw_calls = msg.get("tool_calls") or []
                    parsed_calls = []
                    for c in raw_calls:
                        fn = c.get("function", {})
                        args = fn.get("arguments", "{}")
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except Exception:
                                args = {}
                        parsed_calls.append(ToolCall(id=c.get("id", ""), name=fn.get("name", ""), arguments=args))
                    tool_calls = tuple(parsed_calls)
                    usage_info = data.get("usage", {})

        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ProviderAuthenticationError(f"HTTP {exc.code} Unauthorized", self.config.provider_id, request.model, exc.code)
            if exc.code == 429:
                raise ProviderRateLimitError(f"HTTP {exc.code} Rate limited", self.config.provider_id, request.model, exc.code)
            raise ProviderConnectionError(f"HTTP {exc.code} Error", self.config.provider_id, request.model, exc.code)
        except OSError as exc:
            raise ProviderConnectionError(f"Connection failed: {exc}", self.config.provider_id, request.model)

        duration_ms = int((time.perf_counter() - started) * 1000)
        in_tok = usage_info.get("prompt_tokens", len(str(payload["messages"])) // 4)
        out_tok = usage_info.get("completion_tokens", len(content) // 4)
        cached_tok = usage_info.get("prompt_tokens_details", {}).get("cached_tokens", 0)

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
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            model=request.model,
            provider_id=self.config.provider_id,
        )
