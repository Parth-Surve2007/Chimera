"""Canonical provider schemas, requests, responses, and events."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


class ApiDialect(str, enum.Enum):
    OPENAI_COMPATIBLE = "openai_compatible"
    OPENAI_RESPONSES = "openai_responses"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    GEMINI_CONTENT = "gemini_content"
    GEMINI_INTERACTIONS = "gemini_interactions"
    META_MODEL_API = "meta_model_api"
    LLAMA_CPP = "llama_cpp"
    CUSTOM = "custom"


class CapabilityStatus(str, enum.Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"
    CONFIGURATION_REQUIRED = "CONFIGURATION_REQUIRED"


class ExecutionProvenance(str, enum.Enum):
    LIVE_REMOTE = "LIVE_REMOTE"
    LIVE_LOCAL = "LIVE_LOCAL"
    MOCK = "MOCK"
    FIXTURE = "FIXTURE"
    MANUAL = "MANUAL"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    name: str
    result: Any
    status: str = "completed"
    error: str | None = None


@dataclass(frozen=True)
class NormalizedMessage:
    role: str  # "system", "user", "assistant", "tool"
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_result: ToolResult | None = None


@dataclass(frozen=True)
class UsageMetrics:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    estimated_cost: float = 0.0
    actual_cost: float | None = None
    duration_ms: int = 0
    request_count: int = 1
    provenance: ExecutionProvenance = ExecutionProvenance.LIVE_REMOTE


@dataclass(frozen=True)
class GenerationRequest:
    model: str
    messages: tuple[NormalizedMessage, ...]
    tools: tuple[dict[str, Any], ...] = ()
    temperature: float = 0.2
    max_tokens: int | None = None
    stream: bool = False
    context_limit: int | None = None
    task_id: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class GenerationResponse:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: UsageMetrics = field(default_factory=UsageMetrics)
    finish_reason: str = "stop"
    raw_response: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    provider_id: str = ""


@dataclass(frozen=True)
class StreamEvent:
    delta_text: str = ""
    tool_call: ToolCall | None = None
    is_done: bool = False
    usage: UsageMetrics | None = None
