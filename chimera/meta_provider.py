"""Meta Model API Provider integration for Chimera.

Provides direct access to Meta's hosted model family (e.g. muse-spark-1.3)
at https://api.meta.ai/v1 while preserving Chimera's local-first architecture,
Windows Credential Manager secret storage, strict AgentRuntime execution authority,
privacy controls (Standard mode default; Contributor mode never auto-activated),
streaming responses, model-generated tool calls, and detailed usage accounting.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .model_hub import CredentialStore, ModelProfile

META_API_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_META_MODEL = "muse-spark-1.3"

# Pricing rates per million tokens for Muse Spark 1.3
# Input: $1.25 / 1M tokens ($0.00000125 / token)
# Output: $4.25 / 1M tokens ($0.00000425 / token)
# Cached input: $0.15 / 1M tokens ($0.00000015 / token)
RATE_INPUT_PER_TOKEN = 1.25 / 1_000_000
RATE_OUTPUT_PER_TOKEN = 4.25 / 1_000_000
RATE_CACHED_PER_TOKEN = 0.15 / 1_000_000


@dataclass(frozen=True)
class UsageRecord:
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    estimated_cost: float
    request_count: int
    duration_ms: int
    task_id: str | None = None
    created_at: str | None = None


class UsageAccountant:
    """Persistent, privacy-preserving usage and spending accountant for external APIs.
    
    Kept strictly separate from conversation content and user workspaces.
    """

    def __init__(self, db_path: Path | None = None, spend_limit_usd: float = 10.0):
        self.db_path = Path(db_path) if db_path else Path(os.environ.get("CHIMERA_DATA_DIR", ".chimera")) / "meta_usage.json"
        self.spend_limit_usd = spend_limit_usd

    def _load_records(self) -> list[dict]:
        if not self.db_path.is_file():
            return []
        try:
            return json.loads(self.db_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

    def _save_records(self, records: list[dict]) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    def record_usage(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        duration_ms: int = 0,
        task_id: str | None = None,
    ) -> UsageRecord:
        uncached_input = max(0, input_tokens - cached_tokens)
        cost = (
            (uncached_input * RATE_INPUT_PER_TOKEN)
            + (cached_tokens * RATE_CACHED_PER_TOKEN)
            + (output_tokens * RATE_OUTPUT_PER_TOKEN)
        )
        rec = UsageRecord(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            estimated_cost=round(cost, 6),
            request_count=1,
            duration_ms=duration_ms,
            task_id=task_id,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        records = self._load_records()
        records.append({
            "provider": rec.provider,
            "model": rec.model,
            "input_tokens": rec.input_tokens,
            "output_tokens": rec.output_tokens,
            "cached_tokens": rec.cached_tokens,
            "estimated_cost": rec.estimated_cost,
            "request_count": rec.request_count,
            "duration_ms": rec.duration_ms,
            "task_id": rec.task_id,
            "created_at": rec.created_at,
        })
        self._save_records(records)
        return rec

    def summary(self) -> dict[str, Any]:
        records = self._load_records()
        total_requests = len(records)
        total_input = sum(r.get("input_tokens", 0) for r in records)
        total_output = sum(r.get("output_tokens", 0) for r in records)
        total_cached = sum(r.get("cached_tokens", 0) for r in records)
        total_cost = sum(r.get("estimated_cost", 0.0) for r in records)
        limit_reached = total_cost >= self.spend_limit_usd
        warning_reached = total_cost >= (self.spend_limit_usd * 0.8)
        return {
            "total_requests": total_requests,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cached_tokens": total_cached,
            "total_estimated_cost_usd": round(total_cost, 6),
            "spend_limit_usd": self.spend_limit_usd,
            "warning_reached": warning_reached,
            "limit_reached": limit_reached,
        }


class MetaModelProvider:
    """Provider for Meta Model API (e.g. Muse Spark 1.3).
    
    Adheres strictly to Chimera's local-first rules:
    - Never contacts Meta unless explicitly selected.
    - Operates in Standard data-use mode (no training on user prompts/code).
    - Uses Windows Credential Manager or secure runtime env without logging keys.
    - Formats tools for OpenAI-compatible chat completions interface.
    - AgentRuntime remains the sole execution authority; tools are never called directly.
    - Yields text streaming deltas when on_delta callback is provided.
    - Records detailed token usage and cost accounting.
    """

    def __init__(
        self,
        profile: ModelProfile,
        credentials: CredentialStore | None = None,
        accountant: UsageAccountant | None = None,
        data_mode: str = "standard",
    ):
        self.profile = profile
        self.credentials = credentials or CredentialStore()
        self.accountant = accountant or UsageAccountant()
        self.base_url = (profile.base_url or META_API_BASE_URL).rstrip("/")
        # Data mode defaults to Standard; contributor mode is not auto-enabled.
        self.data_mode = data_mode

    def _get_api_key(self) -> str:
        # Check Windows Credential Manager first, then MODEL_API_KEY / META_API_KEY env vars
        key = None
        try:
            key = self.credentials.get(self.profile.provider_id)
        except Exception:
            key = None

        if not key:
            key = os.environ.get("MODEL_API_KEY") or os.environ.get("META_API_KEY")

        if not key:
            raise RuntimeError(
                f"Authentication required for Meta Model API provider '{self.profile.provider_id}'. "
                "Store credentials securely in Windows Credential Manager or set MODEL_API_KEY."
            )
        return key

    @staticmethod
    def _format_messages(messages: list[Any]) -> list[dict[str, Any]]:
        result = []
        for item in messages:
            role = getattr(item, "role", None) or (item.get("role") if isinstance(item, dict) else "user")
            content = getattr(item, "content", None) if not isinstance(item, dict) else item.get("content", "")
            if role == "tool":
                try:
                    data = json.loads(content)
                    result.append({
                        "role": "tool",
                        "tool_call_id": data.get("invocation_id", ""),
                        "content": json.dumps(data.get("result", data), separators=(",", ":")),
                    })
                except (TypeError, ValueError):
                    result.append({"role": "tool", "content": str(content)})
            elif role == "assistant":
                # Check if it was a tool call message
                if isinstance(item, dict) and "tool_calls" in item:
                    result.append(item)
                else:
                    result.append({"role": "assistant", "content": content or ""})
            else:
                result.append({"role": role, "content": str(content or "")})
        return result

    @staticmethod
    def _format_tools(catalog: dict[str, Any]) -> list[dict[str, Any]]:
        tools = []
        for item in catalog.get("tools", []):
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
            tools.append({
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
        return tools

    def generate(
        self,
        messages: list[dict],
        timeout: float = 60,
        cancel_event: Any = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("inference cancelled")

        summary = self.accountant.summary()
        if summary.get("limit_reached"):
            raise RuntimeError(f"Usage limit of ${summary.get('spend_limit_usd')} reached for Meta API.")

        key = self._get_api_key()
        payload = {
            "model": self.profile.model_id,
            "messages": self._format_messages(messages),
            "stream": bool(on_delta),
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "X-Meta-Data-Policy": self.data_mode,
        }
        endpoint = f"{self.base_url}/chat/completions"
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST")
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if on_delta:
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
                                on_delta(text)
                    content = "".join(parts)
                else:
                    data = json.loads(response.read().decode())
                    content = data["choices"][0]["message"].get("content", "")
                    usage_info = data.get("usage", {})

            duration_ms = int((time.perf_counter() - started) * 1000)
            in_tok = usage_info.get("prompt_tokens", len(str(messages)) // 4)
            out_tok = usage_info.get("completion_tokens", len(content) // 4)
            rec = self.accountant.record_usage(
                provider=self.profile.provider_id,
                model=self.profile.model_id,
                input_tokens=in_tok,
                output_tokens=out_tok,
                duration_ms=duration_ms,
            )
            return {
                "text": content,
                "provider": self.profile.provider_id,
                "model": self.profile.model_id,
                "duration_ms": duration_ms,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "estimated_cost": rec.estimated_cost,
                "reasoning_source": "meta_model_api",
            }
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Meta Model API request failed with HTTP {exc.code}") from None

    def decide(
        self,
        messages: list[Any],
        catalog: dict[str, Any],
        session: dict[str, Any] | None = None,
        cancel_event: Any = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Ask Muse Spark to make an agentic decision: either message or tool_call."""
        if not self.profile.tool_calling:
            raise RuntimeError("selected Meta model profile does not support tool calling")

        summary = self.accountant.summary()
        if summary.get("limit_reached"):
            raise RuntimeError(f"Usage limit of ${summary.get('spend_limit_usd')} reached for Meta API.")

        key = self._get_api_key()
        formatted_tools = self._format_tools(catalog)
        payload = {
            "model": self.profile.model_id,
            "messages": self._format_messages(messages),
            "tools": formatted_tools,
            "tool_choice": "auto",
            "stream": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "X-Meta-Data-Policy": self.data_mode,
        }
        endpoint = f"{self.base_url}/chat/completions"
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST")

        if cancel_event and cancel_event.is_set():
            raise InterruptedError("inference cancelled")

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Meta Model API request failed with HTTP {exc.code}") from None

        duration_ms = int((time.perf_counter() - started) * 1000)
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError, ValueError):
            raise RuntimeError("Meta Model API returned an invalid chat response") from None

        usage_info = data.get("usage", {})
        in_tok = usage_info.get("prompt_tokens", len(str(messages)) // 4)
        out_tok = usage_info.get("completion_tokens", 10)
        cached_tok = usage_info.get("prompt_tokens_details", {}).get("cached_tokens", 0)

        task_id = session.get("session_id") if session else None
        rec = self.accountant.record_usage(
            provider=self.profile.provider_id,
            model=self.profile.model_id,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cached_tokens=cached_tok,
            duration_ms=duration_ms,
            task_id=task_id,
        )

        metrics = {
            "provider": self.profile.provider_id,
            "model": self.profile.model_id,
            "duration_ms": duration_ms,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cached_tokens": cached_tok,
            "estimated_cost": rec.estimated_cost,
            "reasoning_source": "meta_muse_spark",
        }

        calls = message.get("tool_calls") or []
        if len(calls) > 1:
            raise RuntimeError("Meta provider returned multiple tool calls; Chimera requires sequential validation")

        if calls:
            call = calls[0]
            func = call.get("function", {})
            name = func.get("name")
            try:
                arguments = json.loads(func.get("arguments", "{}"))
            except (TypeError, ValueError):
                raise RuntimeError("Meta provider returned malformed tool arguments") from None

            if not isinstance(arguments, dict) or not isinstance(name, str):
                raise RuntimeError("Meta provider returned an invalid tool call structure")

            return {
                "type": "tool_call",
                "tool": name,
                "arguments": arguments,
                "invocation_id": call.get("id"),
                "_metrics": metrics,
            }

        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Meta provider returned neither a message nor a tool call")

        return {
            "type": "message",
            "content": content,
            "_metrics": metrics,
        }
