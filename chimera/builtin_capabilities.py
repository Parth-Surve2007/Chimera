"""Honest inventory of first-party Chimera capabilities.

This catalog describes code that ships in this repository. External desktop
applications such as Blender and Ghidra are optional companion installations;
their connectors remain present even when the companion executable is absent.
"""

from __future__ import annotations

BUILTIN_CAPABILITIES = (
    {
        "capability_id": "workspace.files",
        "name": "Local file operations",
        "domain": "files",
        "description": "Bounded file listing, metadata inspection, name/content search, text reading, and approval-gated text, DOCX, and verified PDF creation on the configured local drive.",
        "actions": (
            "workspace.list_files",
            "workspace.inspect_file",
            "workspace.search_files",
            "workspace.search_text",
            "workspace.read_text",
            "workspace.create_file",\n            "workspace.create_document",\n            "workspace.create_pdf",
        ),
        "companion": None,
    },
    {
        "capability_id": "computer.windows",
        "name": "Windows desktop control",
        "domain": "computer",
        "description": "Discover visible apps, authorize an exact running process, inspect fresh UI Automation evidence, and use supported semantic control patterns.",
        "actions": (
            "computer.inspect_window",
            "computer.inspect_controls",
            "computer.inspect_elements",
            "computer.read_text",
            "computer.invoke_element",
            "computer.set_value",
            "computer.toggle_element",
            "computer.select_element",
            "computer.expand_element",
            "computer.collapse_element",
        ),
        "companion": "pywinauto",
    },
    {
        "capability_id": "reverse_engineering.rea",
        "name": "REA static analysis",
        "domain": "reverse-engineering",
        "description": "Built-in artifact identification and bounded static inspection for supported PE, ELF, and related formats, including strings and structural metadata.",
        "actions": (
            "application.rea.analyze_artifact",
            "application.rea.extract_strings",
            "application.rea.detect_format",
            "application.rea.analyze_unknown",
            "application.rea.list_backends",
        ),
        "companion": None,
    },
    {
        "capability_id": "reverse_engineering.ghidra",
        "name": "Ghidra integration",
        "domain": "reverse-engineering",
        "description": "Connector for Ghidra discovery and bounded binary inspection. Full headless automation is reported only when its required runtime and action are available.",
        "actions": (
            "application.ghidra.inspect_installation",
            "application.ghidra.extract_strings",
            "application.ghidra.enumerate_functions",
        ),
        "companion": "NationalSecurityAgency.Ghidra",
    },
    {
        "capability_id": "3d.blender",
        "name": "Blender scene generation",
        "domain": "3d",
        "description": "Approval-gated creation of bounded primitive-based .blend scenes and PNG render previews from validated JSON recipes.",
        "actions": (
            "application.blender.inspect_installation",
            "application.blender.create_scene",
        ),
        "companion": "BlenderFoundation.Blender",
    },
    {
        "capability_id": "workflow.sequential",
        "name": "Multi-step workflows",
        "domain": "automation",
        "description": "Dependency-aware sequential workflows with per-step policy checks, approval pauses, persisted results, and honest failure states.",
        "actions": (),
        "companion": None,
    },
    {
        "capability_id": "goals.ledger",
        "name": "Goals and evidence ledger",
        "domain": "planning",
        "description": "Persistent operator-owned goals, priorities, success criteria, evidence references, and an auditable lifecycle.",
        "actions": (),
        "companion": None,
    },
)


def builtin_capability_ids() -> tuple[str, ...]:
    """Return the stable IDs used by the built-in inventory."""
    return tuple(item["capability_id"] for item in BUILTIN_CAPABILITIES)


def validate_builtin_catalog(operations: dict | None = None) -> bool:
    """Validate workspace actions against the live tool catalogue."""
    if operations is None:
        from .tools import tool_catalog

        operations = tool_catalog()["operations"]
    for item in BUILTIN_CAPABILITIES:
        for action in item["actions"]:
            if action.startswith("workspace."):
                operation_id = action.removeprefix("workspace.")
                if operation_id not in operations:
                    raise ValueError(
                        f"built-in capability {item['capability_id']} references "
                        f"missing workspace operation {operation_id}"
                    )
    return True
