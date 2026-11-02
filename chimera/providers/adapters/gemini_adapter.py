"""Google Gemini native API adapter."""
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

DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiAdapter(ModelProvider):
    """Adapter for Google Gemini native Generative Language API."""

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
        self.base_url = (config.base_url or DEFAULT_GEMINI_BASE_URL).rstrip("/")

    def _get_api_key(self) -> str:
        key = ""
        if self.credentials:
            try:
                key = self.credentials.get(self.config.provider_id)
            except Exception:
                pass
        if not key:
            import os
            key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
        return key

    def validate_credentials(self) -> bool:
        return bool(self._get_api_key())

    def check_readiness(self) -> dict[str, Any]:
        if not self.validate_credentials():
            return {"status": "unauthenticated", "error": "GEMINI_API_KEY missing"}
        return {"status": "ready", "url": self.base_url}

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(
            provider_id=self.config.provider_id,
            model_id=model,
            text_generation=CapabilityStatus.SUPPORTED,
            streaming=CapabilityStatus.SUPPORTED,
            tool_calling=CapabilityStatus.SUPPORTED,
            structured_output=CapabilityStatus.SUPPORTED,
            vision_input=CapabilityStatus.SUPPORTED,
            audio_input=CapabilityStatus.SUPPORTED,
            video_input=CapabilityStatus.SUPPORTED,
            input_token_accounting=CapabilityStatus.SUPPORTED,
            output_token_accounting=CapabilityStatus.SUPPORTED,
            cached_token_accounting=CapabilityStatus.SUPPORTED,
            declared_by="gemini_spec",
        )

    def _format_contents_and_system(self, messages: tuple) -> tuple[str, list[dict[str, Any]]]:
        system_instruction = ""
        contents = []
        for msg in messages:
            if msg.role == "system":
                system_instruction = (system_instruction + "\n" + msg.content).strip()
            elif msg.role == "tool" and msg.tool_result:
                tr = msg.tool_result
                contents.append({
                    "role": "function",
                    "parts": [{
                        "functionResponse": {
                            "name": tr.name,
                            "response": {"name": tr.name, "content": tr.result},
                        }
                    }],
                })
            elif msg.role == "assistant" and msg.tool_calls:
                parts = []
                if msg.content:
                    parts.append({"text": msg.content})
                for tc in msg.tool_calls:
                    parts.append({
                        "functionCall": {
                            "name": tc.name,
                            "args": tc.arguments,
                        }
                    })
                contents.append({"role": "model", "parts": parts})
            else:
                gemini_role = "user" if msg.role == "user" else "model"
                contents.append({"role": gemini_role, "parts": [{"text": msg.content}]})
        return system_instruction, contents

    def _format_tools(self, tools: tuple) -> list[dict[str, Any]]:
        declarations = []
        for item in tools:
            tool_id = item.get("id", "")
            if not tool_id.startswith(("workspace.", "computer.")):
                continue
            properties = {}
            for name, kind in item.get("arguments", {}).items():
                t = "STRING"
                if kind is bool or kind == "boolean":
                    t = "BOOLEAN"
                elif kind in (int, "integer", "int"):
                    t = "INTEGER"
                elif kind in (dict, "object"):
                    t = "OBJECT"
                properties[name] = {"type": t}
            declarations.append({
                "name": tool_id,
                "description": item.get("description", ""),
                "parameters": {
                    "type": "OBJECT",
                    "properties": properties,
                },
            })
        if not declarations:
            return []
        return [{"functionDeclarations": declarations}]

    def generate(
        self,
        request: GenerationRequest,
        cancel_event: Any = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> GenerationResponse:
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("inference cancelled")

        if not self.validate_credentials():
            raise ProviderAuthenticationError("Gemini API key missing", self.config.provider_id, request.model)

        sys_inst, contents = self._format_contents_and_system(request.messages)
        payload: dict[str, Any] = {"contents": contents}
        if sys_inst:
            payload["systemInstruction"] = {"parts": [{"text": sys_inst}]}

        if request.tools:
            tools_block = self._format_tools(request.tools)
            if tools_block:
                payload["tools"] = tools_block

        api_key = self._get_api_key()
        endpoint = f"{self.base_url}/models/{request.model}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json", **self.config.custom_headers}
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

        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            for p in parts:
                if "text" in p:
                    content_parts.append(p["text"])
                    if on_delta and p["text"]:
                        on_delta(p["text"])
                elif "functionCall" in p:
                    fn = p["functionCall"]
                    parsed_calls.append(ToolCall(
                        id=f"gemini_call_{int(time.time()*1000)}",
                        name=fn.get("name", ""),
                        arguments=fn.get("args", {}),
                    ))

        usage_meta = data.get("usageMetadata", {})
        in_tok = usage_meta.get("promptTokenCount", len(str(contents)) // 4)
        out_tok = usage_meta.get("candidatesTokenCount", len("".join(content_parts)) // 4)
        cached_tok = usage_meta.get("cachedContentTokenCount", 0)

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
