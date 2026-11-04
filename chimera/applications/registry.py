"""Application Registry and Discovery Engine for Chimera."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Type

from .base import BaseApplicationConnector
from .manifest import ActionRiskLevel, ApplicationManifest

logger = logging.getLogger(__name__)


class ApplicationRegistry:
    """Central registry for discovering, inspecting, and managing installed software connectors."""

    def __init__(self):
        self._connectors: dict[str, BaseApplicationConnector] = {}

    def register(self, connector: BaseApplicationConnector) -> None:
        """Register an application connector."""
        self._connectors[connector.manifest.application_id] = connector

    def get(self, app_id: str) -> BaseApplicationConnector | None:
        return self._connectors.get(app_id)

    def discover_all(self) -> list[ApplicationManifest]:
        """Trigger dynamic discovery across all registered application connectors."""
        manifests = []
        for connector in self._connectors.values():
            try:
                manifest = connector.discover()
                manifests.append(manifest)
            except Exception as exc:
                logger.warning(f"Discovery error for {connector.manifest.application_id}: {exc}")
                manifests.append(connector.manifest)
        return manifests

    def list_installed(self) -> list[ApplicationManifest]:
        """Return only verified installed applications."""
        return [m for m in self.discover_all() if m.is_installed]

    def resolve_action_handler(self, action_qualified_id: str) -> tuple[BaseApplicationConnector | None, str]:
        """Resolve app.action (e.g. 'ghidra.extract_strings') to connector and action ID."""
        if "." not in action_qualified_id:
            return None, action_qualified_id
        app_id, action_id = action_qualified_id.split(".", 1)
        return self.get(app_id), action_id


_GLOBAL_APP_REGISTRY: ApplicationRegistry | None = None


def get_application_registry() -> ApplicationRegistry:
    global _GLOBAL_APP_REGISTRY
    if _GLOBAL_APP_REGISTRY is None:
        _GLOBAL_APP_REGISTRY = ApplicationRegistry()
        # Auto-register built-in connectors (Ghidra, REA)
        from .connectors.ghidra_connector import GhidraConnector
        from .connectors.rea_connector import ReaConnector
        from .connectors.blender_connector import BlenderConnector
        from .connectors.browser_connector import BrowserConnector
        _GLOBAL_APP_REGISTRY.register(GhidraConnector())
        _GLOBAL_APP_REGISTRY.register(ReaConnector())
        _GLOBAL_APP_REGISTRY.register(BlenderConnector())
        _GLOBAL_APP_REGISTRY.register(BrowserConnector())
    return _GLOBAL_APP_REGISTRY
