"""Google Gemini Interactions API adapter for session-based agentic workflows."""
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

DEFAULT_GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


class GeminiInteractionsAdapter(ModelProvider):
    """Adapter for Google Gemini Interactions API (server-side interaction chaining)."""

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
        self.base_url = (config.base_url or DEFAULT_GEMINI_INTERACTIONS_URL).rstrip("/")
        # Track last interaction ID per session for stateful continuation
        self._session_previous_ids: dict[str, str] = {}

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
        return {"status": "ready", "url": self.base_url, "dialect": "gemini_interactions"}

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(
            provider_id=self.config.provider_id,
            model_id=model,
            text_generation=CapabilityStatus.SUPPORTED,
            streaming=CapabilityStatus.SUPPORTED,
            tool_calling=CapabilityStatus.SUPPORTED,
            parallel_tool_calls=CapabilityStatus.SUPPORTED,
            structured_output=CapabilityStatus.SUPPORTED,
            reasoning_metadata=CapabilityStatus.SUPPORTED,
            input_token_accounting=CapabilityStatus.SUPPORTED,
            output_token_accounting=CapabilityStatus.SUPPORTED,
            cached_token_accounting=CapabilityStatus.SUPPORTED,
            declared_by="gemini_interactions_spec",
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
            raise ProviderAuthenticationError("Gemini API key missing", self.config.provider_id, request.model)

        # Build payload according to Interactions API specification
        # The latest message is the input. If previous_interaction_id exists for this session, reference it.
        session_key = request.session_id or "default"
        prev_id = self._session_previous_ids.get(session_key)

        # Extract latest message text or tool responses
        last_msg = request.messages[-1] if request.messages else None
        input_data: Any = ""
        if last_msg:
            if last_msg.role == "tool" and last_msg.tool_result:
                tr = last_msg.tool_result
                input_data = {
                    "tool_results": [{
                        "call_id": tr.tool_call_id,
                        "name": tr.name,
                        "output": tr.result,
                    }]
                }
            else:
                input_data = last_msg.content

        payload: dict[str, Any] = {
            "model": request.model,
            "input": input_data,
        }
        if prev_id:
            payload["previous_interaction_id"] = prev_id

        if request.tools:
            tools_list = []
            for item in request.tools:
                tid = item.get("id", "")
                if not tid.startswith(("workspace.", "computer.")):
                    continue
                tools_list.append({
                    "name": tid,
                    "description": item.get("description", ""),
                    "parameters": {"type": "object", "properties": item.get("arguments", {})},
                })
            if tools_list:
                payload["tools"] = tools_list

        api_key = self._get_api_key()
        sep = "&" if "?" in self.base_url else "?"
        endpoint = f"{self.base_url}{sep}key={api_key}"
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

        # Store interaction ID for next turn
        interaction_id = data.get("id", "")
        if interaction_id:
            self._session_previous_ids[session_key] = interaction_id

        # Parse outputs and tool calls
        content = ""
        parsed_calls = []

        outputs = data.get("outputs", [])
        for out in outputs:
            if isinstance(out, dict):
                if "text" in out:
                    content += out["text"]
                    if on_delta and out["text"]:
                        on_delta(out["text"])
                if "tool_call" in out:
                    tc = out["tool_call"]
                    parsed_calls.append(ToolCall(
                        id=tc.get("id", f"call_{int(time.time()*1000)}"),
                        name=tc.get("name", ""),
                        arguments=tc.get("arguments", {}),
                    ))
            elif isinstance(out, str):
                content += out
                if on_delta:
                    on_delta(out)

        usage_meta = data.get("usage", {})
        in_tok = usage_meta.get("input_tokens", len(str(input_data)) // 4)
        out_tok = usage_meta.get("output_tokens", len(content) // 4)
        cached_tok = usage_meta.get("cached_tokens", 0)

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
            tool_calls=tuple(parsed_calls),
            usage=usage,
            model=request.model,
            provider_id=self.config.provider_id,
        )
