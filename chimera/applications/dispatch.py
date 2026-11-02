"""Application SDK dispatch helpers for AgentRuntime integration.

Provides:
- application_tool_catalog(): convert ApplicationRegistry manifests to tool schema entries
- validate_application_arguments(): schema validation against manifest action definitions
- APPLICATION_TOOL_PREFIX: stable namespace prefix used in all tool IDs
- encode_tool_id / decode_tool_id: safe bijective mapping with no parsing ambiguity
"""
from __future__ import annotations

from typing import Any

APPLICATION_TOOL_PREFIX = "application."

# Map Python type-name strings in argument_schema to Python types for validation
_TYPE_MAP: dict[str, type] = {
    "string": str,
    "str": str,
    "integer": int,
    "int": int,
    "boolean": bool,
    "bool": bool,
    "number": float,
    "float": float,
}

# Read-only application permissions — actions with these permissions do NOT require
# operator approval in Build Mode (same semantics as workspace.read / computer.observe).
APPLICATION_READ_PERMISSIONS = {
    "ghidra.read",
    "reaper.read",
}


def encode_tool_id(app_id: str, action_id: str) -> str:
    """Return the stable external tool identifier for a registered application action.

    Format: ``application.{app_id}.{action_id}``
    Both segments are stored verbatim; decoding is unambiguous because we split
    on the *second* dot only (splitting on prefix length, not on arbitrary dots).
    """
    return f"{APPLICATION_TOOL_PREFIX}{app_id}.{action_id}"


def decode_tool_id(tool_id: str) -> tuple[str, str] | None:
    """Return (app_id, action_id) from an application tool identifier, or None.

    Splitting strategy: strip the ``application.`` prefix, then split on the
    first remaining dot to separate app_id from action_id.  action_id may itself
    contain dots (e.g. ``reaper.project.read``).
    """
    if not tool_id.startswith(APPLICATION_TOOL_PREFIX):
        return None
    remainder = tool_id[len(APPLICATION_TOOL_PREFIX):]
    if "." not in remainder:
        return None
    app_id, action_id = remainder.split(".", 1)
    return app_id, action_id


def application_tool_catalog(registry) -> list[dict[str, Any]]:
    """Build a list of tool-schema entries from all registered connectors.

    Only actions on connectors that have been discovered (is_installed=True) or
    whose risk_level is LEVEL_0 (observe/read) are exposed.  Stubs and actions
    marked as unavailable are excluded.

    Each entry mirrors the shape of workspace tool entries::

        {
            "id": "application.ghidra.extract_strings",
            "description": "...",
            "arguments": {"binary_path": "string", "min_length": "integer"},
            "permission": "ghidra.read",
            "requires_approval": False,
            "risk_level": 0,
            "app_id": "ghidra",
            "action_id": "extract_strings",
        }
    """
    from .manifest import ActionRiskLevel

    tools = []
    for connector in registry._connectors.values():
        manifest = connector.manifest
        for action in manifest.actions:
            # Skip actions explicitly marked as not yet implemented
            if getattr(action, "unavailable", False):
                continue
            # Skip high-risk actions on uninstalled applications
            # (read-only/observe actions are always safe to expose)
            if action.risk_level > ActionRiskLevel.LEVEL_0_OBSERVE and not manifest.is_installed:
                continue
            tools.append({
                "id": encode_tool_id(manifest.application_id, action.action_id),
                "description": action.description,
                "arguments": action.arguments_schema,
                "permission": action.permission,
                "requires_approval": action.requires_approval,
                "risk_level": action.risk_level.value,
                "app_id": manifest.application_id,
                "action_id": action.action_id,
            })
    return tools


def validate_application_arguments(action, arguments: dict[str, Any]) -> None:
    """Validate ``arguments`` against the declared schema of ``action``.

    Raises ``ValueError`` on unknown keys or type mismatches.
    Missing optional keys are allowed (schema defines required keys only).
    """
    schema: dict[str, str] = action.arguments_schema
    unknown = set(arguments) - set(schema)
    if unknown:
        raise ValueError(f"Unexpected arguments for action '{action.action_id}': {unknown}")
    for arg_name, type_name in schema.items():
        if arg_name not in arguments:
            continue  # optional — connectors handle missing args internally
        expected_type = _TYPE_MAP.get(type_name if isinstance(type_name, str) else str(type_name))
        if expected_type is not None and not isinstance(arguments[arg_name], expected_type):
            raise ValueError(
                f"Argument '{arg_name}' for action '{action.action_id}' "
                f"must be {type_name}, got {type(arguments[arg_name]).__name__}"
            )
