"""Base connector contract and SDK for application integrations."""
from __future__ import annotations

import abc
from typing import Any

from .manifest import ActionRiskLevel, ApplicationAction, ApplicationManifest


class BaseApplicationConnector(abc.ABC):
    """Abstract base class for all native and bridge application connectors."""

    def __init__(self, manifest: ApplicationManifest):
        self.manifest = manifest

    @abc.abstractmethod
    def discover(self) -> ApplicationManifest:
        """Probe environment, detect installation, binary location, and running state."""
        pass

    @abc.abstractmethod
    def execute_action(self, action_id: str, arguments: dict[str, Any], approved: bool = False) -> dict[str, Any]:
        """Execute a declared capability action with risk governance."""
        pass

    def validate_action_call(self, action_id: str, arguments: dict[str, Any], mode: str = "plan") -> tuple[bool, str, ApplicationAction | None]:
        """Validate action against manifest, argument contract, and mode policy."""
        action = next((a for a in self.manifest.actions if a.action_id == action_id), None)
        if not action:
            return False, f"Unknown action '{action_id}' on application '{self.manifest.application_id}'", None

        # Mode policy check
        if mode == "plan" and action.risk_level > ActionRiskLevel.LEVEL_0_OBSERVE:
            return False, f"Action '{action_id}' (risk level {action.risk_level.value}) is prohibited in Plan Mode. Plan Mode is read-only.", action

        # Basic schema validation
        for arg_name, arg_type in action.arguments_schema.items():
            if arg_name not in arguments:
                return False, f"Missing required argument '{arg_name}' for action '{action_id}'", action

        return True, "Action permitted", action
