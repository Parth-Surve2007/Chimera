"""Reverse Engineering Engine orchestration layer for REA.

Coordinates multiple analysis backends, executes workflows, and manages
backend selection and capability routing.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .backends.builtin_static import BuiltinStaticBackend
from .backends.ghidra import GhidraBackend
from .backends.radare2 import Radare2Backend
from .backends.rizin import RizinBackend
from .capabilities import AnalysisCapability, BackendCapabilityManifest
from .detection import ArtifactDetector, detect_artifact
from .results import AnalysisResult

logger = logging.getLogger(__name__)


class ReverseEngineeringEngine:
    """Primary orchestration engine for REA capabilities."""

    def __init__(self):
        self.detector = ArtifactDetector()
        self.builtin_backend = BuiltinStaticBackend()
        self.ghidra_backend = GhidraBackend()
        self.radare2_backend = Radare2Backend()
        self.rizin_backend = RizinBackend()

        self._backends = {
            self.builtin_backend.BACKEND_ID: self.builtin_backend,
            self.ghidra_backend.BACKEND_ID: self.ghidra_backend,
            self.radare2_backend.BACKEND_ID: self.radare2_backend,
            self.rizin_backend.BACKEND_ID: self.rizin_backend,
        }

    def get_backend_manifests(self) -> list[BackendCapabilityManifest]:
        """Return capability manifests for all registered backends."""
        return [backend.get_manifest() for backend in self._backends.values()]

    def get_available_backends(self) -> list[str]:
        """Return list of backend IDs that are installed and available."""
        return [
            backend_id
            for backend_id, backend in self._backends.items()
            if backend.get_manifest().is_installed
        ]

    def select_backend_for_capability(
        self, capability: AnalysisCapability | str, preferred_backend: str | None = None
    ) -> str:
        """Select the best available backend for a given capability.

        Args:
            capability: The analysis capability required.
            preferred_backend: Optional specific backend ID to prefer.

        Returns:
            Backend ID that supports the capability and is available.
            Returns "builtin_static" as fallback if no specialized backend is available.
        """
        cap = AnalysisCapability(capability) if isinstance(capability, str) else capability

        # If preferred backend is specified and supports the capability, use it
        if preferred_backend and preferred_backend in self._backends:
            manifest = self._backends[preferred_backend].get_manifest()
            if manifest.is_installed and manifest.supports(cap):
                return preferred_backend

        # Otherwise, find the first available backend that supports the capability
        for backend_id, backend in self._backends.items():
            manifest = backend.get_manifest()
            if manifest.is_installed and manifest.supports(cap):
                return backend_id

        # Fallback to built-in static backend for basic capabilities
        if self.builtin_backend.get_manifest().supports(cap):
            return self.builtin_backend.BACKEND_ID

        logger.warning(f"No backend available for capability: {cap}")
        return "none"

    def analyze_artifact(self, path: str | Path) -> AnalysisResult:
        """Workflow A: Understand an executable.

        Performs comprehensive static analysis using built-in backend.
        """
        return self.builtin_backend.analyze_artifact(path)

    def extract_strings(
        self, path: str | Path, min_length: int = 4, max_strings: int = 2000
    ) -> AnalysisResult:
        """Extract printable strings from an artifact."""
        return self.builtin_backend.extract_strings(path, min_length, max_strings)

    def detect_format(self, path: str | Path) -> AnalysisResult:
        """Identify the format and architecture of an artifact."""
        artifact = self.detector.detect(path)

        from .results import StructuredFindings

        return AnalysisResult(
            status="completed",
            artifact_id=artifact.sha256,
            operation=AnalysisCapability.IDENTIFY.value,
            backend_used=self.builtin_backend.BACKEND_ID,
            backend_version=self.builtin_backend.VERSION,
            findings=StructuredFindings(observed_facts=artifact.to_dict()),
            confidence=artifact.detection_confidence,
        )

    def analyze_unknown_binary(self, path: str | Path) -> AnalysisResult:
        """Workflow B: Analyze an unknown binary.

        Progressively identifies format, selects available backends,
        and combines findings from multiple sources.
        """
        # First, detect the format
        detection_result = self.detect_format(path)

        # Then run comprehensive analysis with built-in backend
        analysis_result = self.analyze_artifact(path)

        # Merge findings
        combined_facts = {
            **detection_result.findings.observed_facts,
            **analysis_result.findings.observed_facts,
        }

        combined_conclusions = detection_result.findings.derived_conclusions + analysis_result.findings.derived_conclusions

        return AnalysisResult(
            status=analysis_result.status,
            artifact_id=analysis_result.artifact_id,
            operation="workflow_unknown",
            backend_used="builtin_static",
            backend_version=self.builtin_backend.VERSION,
            findings=analysis_result.findings,
            evidence=analysis_result.evidence,
            confidence=min(detection_result.confidence, analysis_result.confidence),
            warnings=detection_result.warnings + analysis_result.warnings,
            errors=detection_result.errors + analysis_result.errors,
            duration_ms=detection_result.duration_ms + analysis_result.duration_ms,
            limitations=detection_result.limitations + analysis_result.limitations,
        )

    def run_ghidra_headless(
        self,
        binary_path: str | Path,
        project_location: str | Path,
        project_name: str,
        script_path: str | Path | None = None,
    ) -> AnalysisResult:
        """Execute Ghidra headless analysis if available."""
        return self.ghidra_backend.run_headless_analysis(
            binary_path, project_location, project_name, script_path
        )


_GLOBAL_REA_ENGINE: ReverseEngineeringEngine | None = None


def get_rea_engine() -> ReverseEngineeringEngine:
    """Get the global REA engine instance."""
    global _GLOBAL_REA_ENGINE
    if _GLOBAL_REA_ENGINE is None:
        _GLOBAL_REA_ENGINE = ReverseEngineeringEngine()
    return _GLOBAL_REA_ENGINE
