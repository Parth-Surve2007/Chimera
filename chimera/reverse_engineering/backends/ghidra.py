"""NSA Ghidra Software Reverse Engineering (SRE) backend adapter for REA.

Governs headless project analysis, Ghidra script execution, and decompilation.
Requires native Ghidra and JDK 17+ for headless tasks; truthfully reports
unavailability when Ghidra is not installed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ..capabilities import AnalysisCapability, BackendCapabilityManifest
from ..evidence import EvidenceProvenance
from ..results import AnalysisResult, StructuredFindings
from ..security import AnalysisLimits, validate_artifact_path


class GhidraBackend:
    """Specialized Ghidra analysis backend adapter."""

    BACKEND_ID = "ghidra"
    DISPLAY_NAME = "NSA Ghidra SRE Suite"
    VERSION = "11.1.0"

    def __init__(self, custom_install_path: Path | str | None = None):
        self.custom_install_path = Path(custom_install_path) if custom_install_path else None
        self._installation_path: str | None = None
        self._headless_executable: str | None = None
        self._is_installed: bool = False
        self._discover()

    def _discover(self) -> None:
        """Scan system environment, PATH, and standard directories for Ghidra."""
        ghidra_home = os.environ.get("GHIDRA_HOME") or os.environ.get("GHIDRA_INSTALL_DIR")
        executable = None

        if self.custom_install_path and self.custom_install_path.exists():
            root = self.custom_install_path
            candidate = root / "support" / ("analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless")
            if candidate.is_file():
                executable = str(candidate)
                ghidra_home = str(root)

        if not executable and ghidra_home:
            candidate = Path(ghidra_home) / "support" / ("analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless")
            if candidate.is_file():
                executable = str(candidate)

        if not executable:
            which_cli = shutil.which("analyzeHeadless") or shutil.which("ghidraRun")
            if which_cli:
                executable = which_cli
                ghidra_home = str(Path(which_cli).parent.parent)

        if not executable:
            standard_paths = [
                Path(r"C:\Program Files\Ghidra"),
                Path(r"C:\tools\ghidra"),
                Path(r"C:\ghidra"),
                Path.home() / "ghidra",
                Path("/usr/local/ghidra"),
                Path("/opt/ghidra"),
            ]
            for sp in standard_paths:
                if sp.is_dir():
                    candidate = sp / "support" / ("analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless")
                    if candidate.is_file():
                        executable = str(candidate)
                        ghidra_home = str(sp)
                        break

        self._is_installed = bool(executable)
        self._installation_path = ghidra_home
        self._headless_executable = executable

    def get_manifest(self) -> BackendCapabilityManifest:
        missing_reason = None
        if not self._is_installed:
            missing_reason = (
                "Ghidra installation or analyzeHeadless executable not detected. "
                "Set GHIDRA_HOME environment variable and install JDK 17+ to enable deep decompilation."
            )

        return BackendCapabilityManifest(
            backend_id=self.BACKEND_ID,
            display_name=self.DISPLAY_NAME,
            version=self.VERSION,
            is_installed=self._is_installed,
            supported_capabilities=[
                AnalysisCapability.HEADLESS_ANALYSIS,
                AnalysisCapability.DECOMPILE,
                AnalysisCapability.DISASSEMBLE,
                AnalysisCapability.CALLGRAPH,
                AnalysisCapability.DATA_TYPES,
                AnalysisCapability.SCRIPT_EXECUTION,
            ],
            supported_formats=["PE32", "PE32+", "ELF32", "ELF64", "Mach-O", "Raw Binary"],
            execution_mode="subprocess_cli",
            missing_reason=missing_reason,
            metadata={
                "headless_executable": self._headless_executable,
                "installation_path": self._installation_path,
            },
        )

    def is_available(self) -> bool:
        return self._is_installed

    def run_headless_analysis(
        self,
        binary_path: str | Path,
        project_location: str | Path,
        project_name: str,
        script_path: str | Path | None = None,
    ) -> AnalysisResult:
        """Run headless batch analysis with Ghidra's analyzeHeadless script."""
        start_time = time.perf_counter()
        valid_binary = validate_artifact_path(binary_path)

        if not self._is_installed or not self._headless_executable:
            return AnalysisResult(
                status="unavailable",
                artifact_id=valid_binary.name,
                operation=AnalysisCapability.HEADLESS_ANALYSIS.value,
                backend_used=self.BACKEND_ID,
                backend_version=self.VERSION,
                confidence=0.0,
                errors=[
                    "Ghidra backend is not installed or analyzeHeadless could not be located. "
                    "Built-in static analysis remains available as a fallback."
                ],
                duration_ms=round((time.perf_counter() - start_time) * 1000, 2),
                limitations=["Deep headless decompilation requires native Ghidra and JDK 17+."],
            )

        cmd = [
            self._headless_executable,
            str(project_location),
            project_name,
            "-import",
            str(valid_binary),
            "-overwrite",
        ]
        if script_path:
            cmd.extend(["-postScript", str(script_path)])

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=AnalysisLimits.SUBPROCESS_TIMEOUT_SECONDS,
                check=False,
            )
            success = res.returncode == 0
            facts = {
                "exit_code": res.returncode,
                "stdout_sample": res.stdout[:2000],
                "project_location": str(project_location),
                "project_name": project_name,
            }
            conclusions = ["Ghidra headless analysis executed successfully."] if success else ["Ghidra headless analysis exited with error."]
            errors = [res.stderr[:1000]] if not success and res.stderr else []

            return AnalysisResult(
                status="completed" if success else "failed",
                artifact_id=valid_binary.name,
                operation=AnalysisCapability.HEADLESS_ANALYSIS.value,
                backend_used=self.BACKEND_ID,
                backend_version=self.VERSION,
                findings=StructuredFindings(observed_facts=facts, derived_conclusions=conclusions),
                confidence=1.0 if success else 0.5,
                errors=errors,
                duration_ms=round((time.perf_counter() - start_time) * 1000, 2),
            )
        except subprocess.TimeoutExpired:
            return AnalysisResult(
                status="failed",
                artifact_id=valid_binary.name,
                operation=AnalysisCapability.HEADLESS_ANALYSIS.value,
                backend_used=self.BACKEND_ID,
                backend_version=self.VERSION,
                confidence=0.0,
                errors=[f"Ghidra process timed out after {AnalysisLimits.SUBPROCESS_TIMEOUT_SECONDS} seconds."],
                duration_ms=round((time.perf_counter() - start_time) * 1000, 2),
            )
        except Exception as exc:
            return AnalysisResult(
                status="failed",
                artifact_id=valid_binary.name,
                operation=AnalysisCapability.HEADLESS_ANALYSIS.value,
                backend_used=self.BACKEND_ID,
                backend_version=self.VERSION,
                confidence=0.0,
                errors=[f"Ghidra execution failed: {exc}"],
                duration_ms=round((time.perf_counter() - start_time) * 1000, 2),
            )
