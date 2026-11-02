"""Application Integration Manifest and Discovery schemas for Chimera.

Defines the universal application capability contract, risk taxonomy,
and integration surface hierarchy.
"""
from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from typing import Any


class IntegrationSurface(str, enum.Enum):
    NATIVE_API = "native_api"
    SCRIPTING_EXTENSION = "scripting_extension"
    CLI = "cli"
    LOCAL_SERVICE = "local_service"
    MCP_PROTOCOL = "mcp_protocol"
    UI_AUTOMATION = "ui_automation"
    SCREENSHOT_PERCEPTION = "screenshot_perception"
    MANUAL_HANDOFF = "manual_handoff"


class ActionRiskLevel(int, enum.Enum):
    LEVEL_0_OBSERVE = 0           # Read information, inspect state, zero side effects
    LEVEL_1_RECOMMEND = 1         # Prepare plans, generate scripts, no execution
    LEVEL_2_REVERSIBLE = 2        # Reversible execution, automated backup
    LEVEL_3_PROTECTED = 3         # Protected modifications, requires explicit operator approval
    LEVEL_4_EXTERNAL_SENSITIVE = 4 # External or sensitive operations, strict scope bounds


@dataclass(frozen=True)
class ApplicationAction:
    action_id: str
    display_name: str
    description: str
    risk_level: ActionRiskLevel
    permission: str
    arguments_schema: dict[str, Any]
    expected_side_effects: list[str] = field(default_factory=list)
    reversibility: str = "reversible"  # "reversible", "destructive_with_backup", "irreversible"
    requires_approval: bool = False
    unavailable: bool = False


@dataclass
class ApplicationManifest:
    application_id: str
    display_name: str
    vendor: str
    connector_version: str
    supported_surfaces: list[IntegrationSurface]
    actions: list[ApplicationAction] = field(default_factory=list)
    detected_version: str | None = None
    installation_path: str | None = None
    is_installed: bool = False
    is_running: bool = False
    supported_file_extensions: list[str] = field(default_factory=list)
    known_limitations: list[str] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["supported_surfaces"] = [s.value if isinstance(s, IntegrationSurface) else s for s in self.supported_surfaces]
        for a in data["actions"]:
            if isinstance(a.get("risk_level"), ActionRiskLevel):
                a["risk_level"] = a["risk_level"].value
        return data
