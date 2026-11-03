"""Analysis registry for REA backends and capability registration.

Manages backend registration, capability discovery, and backend selection
for the Reverse Engineering Engine.
"""
from __future__ import annotations

from typing import Any

from .backends.builtin_static import BuiltinStaticBackend
from .backends.ghidra import GhidraBackend
from .backends.radare2 import Radare2Backend
from .backends.rizin import RizinBackend
from .capabilities import AnalysisCapability, BackendCapabilityManifest


class AnalysisRegistry:
    """Central registry for analysis backends and their capabilities."""

    def __init__(self):
        self._backends: dict[str, Any] = {}
        self._register_default_backends()

    def _register_default_backends(self) -> None:
        """Register the default built-in backends."""
        self.register(BuiltinStaticBackend())
        self.register(GhidraBackend())
        self.register(Radare2Backend())
        self.register(RizinBackend())

    def register(self, backend: Any) -> None:
        """Register an analysis backend.

        The backend must implement get_manifest() returning a BackendCapabilityManifest.
        """
        manifest = backend.get_manifest()
        self._backends[manifest.backend_id] = backend

    def get_backend(self, backend_id: str) -> Any | None:
        """Get a registered backend by ID."""
        return self._backends.get(backend_id)

    def get_all_manifests(self) -> list[BackendCapabilityManifest]:
        """Get capability manifests for all registered backends."""
        return [backend.get_manifest() for backend in self._backends.values()]

    def get_available_backends(self) -> list[BackendCapabilityManifest]:
        """Get manifests for backends that are installed and available."""
        return [
            manifest
            for manifest in self.get_all_manifests()
            if manifest.is_installed
        ]

    def find_backends_for_capability(
        self, capability: AnalysisCapability | str
    ) -> list[BackendCapabilityManifest]:
        """Find all backends that support a given capability."""
        cap = AnalysisCapability(capability) if isinstance(capability, str) else capability
        return [
            manifest
            for manifest in self.get_all_manifests()
            if manifest.supports(cap)
        ]

    def find_available_backend_for_capability(
        self, capability: AnalysisCapability | str
    ) -> BackendCapabilityManifest | None:
        """Find the first available backend that supports a given capability."""
        available = self.find_backends_for_capability(capability)
        for manifest in available:
            if manifest.is_installed:
                return manifest
        return None


_GLOBAL_ANALYSIS_REGISTRY: AnalysisRegistry | None = None


def get_analysis_registry() -> AnalysisRegistry:
    """Get the global analysis registry instance."""
    global _GLOBAL_ANALYSIS_REGISTRY
    if _GLOBAL_ANALYSIS_REGISTRY is None:
        _GLOBAL_ANALYSIS_REGISTRY = AnalysisRegistry()
    return _GLOBAL_ANALYSIS_REGISTRY
