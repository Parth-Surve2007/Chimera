"""Rizin analysis backend adapter for REA."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..capabilities import AnalysisCapability, BackendCapabilityManifest
from ..results import AnalysisResult


class RizinBackend:
    """Optional adapter for Rizin binary analysis framework."""

    BACKEND_ID = "rizin"
    DISPLAY_NAME = "Rizin SRE Framework"
    VERSION = "0.7.0"

    def __init__(self):
        self._executable = shutil.which("rizin") or shutil.which("rz-bin")
        self._is_installed = bool(self._executable)

    def get_manifest(self) -> BackendCapabilityManifest:
        missing_reason = None
        if not self._is_installed:
            missing_reason = "Rizin executable ('rizin' or 'rz-bin') not found in system PATH."

        return BackendCapabilityManifest(
            backend_id=self.BACKEND_ID,
            display_name=self.DISPLAY_NAME,
            version=self.VERSION,
            is_installed=self._is_installed,
            supported_capabilities=[
                AnalysisCapability.IDENTIFY,
                AnalysisCapability.HEADERS,
                AnalysisCapability.DISASSEMBLE,
                AnalysisCapability.SYMBOLS,
            ],
            supported_formats=["PE32", "PE32+", "ELF32", "ELF64", "Mach-O", "Raw Binary"],
            execution_mode="subprocess_cli",
            missing_reason=missing_reason,
            metadata={"executable": self._executable},
        )

    def is_available(self) -> bool:
        return self._is_installed

    def analyze(self, path: str | Path, command: str) -> AnalysisResult:
        if not self._is_installed:
            return AnalysisResult(
                status="unavailable",
                artifact_id=Path(path).name,
                operation=command,
                backend_used=self.BACKEND_ID,
                backend_version=self.VERSION,
                confidence=0.0,
                errors=["Rizin is not installed on this system."],
            )
        return AnalysisResult(
            status="unavailable",
            artifact_id=Path(path).name,
            operation=command,
            backend_used=self.BACKEND_ID,
            backend_version=self.VERSION,
            confidence=0.0,
            errors=["Rizin invocation is currently restricted to governed mode."],
        )
