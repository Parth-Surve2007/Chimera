"""Utilities for message and tool call normalization."""
from __future__ import annotations

import json
from typing import Any
from .schemas import NormalizedMessage, ToolCall, ToolResult


def normalize_messages(raw_messages: list[Any]) -> list[NormalizedMessage]:
    """Convert heterogeneous message representations into canonical NormalizedMessage objects."""
    result: list[NormalizedMessage] = []
    for item in raw_messages:
        if isinstance(item, NormalizedMessage):
            result.append(item)
            continue

        role = getattr(item, "role", None) or (item.get("role") if isinstance(item, dict) else "user")
        content = getattr(item, "content", None) if not isinstance(item, dict) else item.get("content", "")

        if role == "tool":
            # Might be json string with invocation_id and result
            tool_call_id = ""
            name = ""
            status = "completed"
            err = None
            res_data: Any = content
            try:
                data = json.loads(content) if isinstance(content, str) else content
                if isinstance(data, dict):
                    tool_call_id = data.get("invocation_id", "")
                    name = data.get("tool", "")
                    status = data.get("status", "completed")
                    err = data.get("error")
                    res_data = data.get("result", data)
            except Exception:
                pass
            result.append(NormalizedMessage(
                role="tool",
                content=str(content),
                tool_result=ToolResult(tool_call_id=tool_call_id, name=name, result=res_data, status=status, error=err),
            ))
        elif role == "assistant":
            # Extract any tool calls if present
            calls: list[ToolCall] = []
            if isinstance(item, dict) and "tool_calls" in item:
                for c in item["tool_calls"]:
                    func = c.get("function", {})
                    args = func.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    calls.append(ToolCall(id=c.get("id", ""), name=func.get("name", ""), arguments=args))
            result.append(NormalizedMessage(role="assistant", content=str(content or ""), tool_calls=tuple(calls)))
        else:
            result.append(NormalizedMessage(role=role, content=str(content or "")))

    return result
