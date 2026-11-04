"""Reverse Engineer Anything (REA) Application Connector.

Exposes REA's general-purpose reverse-engineering capabilities through the
Application SDK, governed by AgentRuntime's permission and approval pipeline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..base import BaseApplicationConnector
from ..manifest import ActionRiskLevel, ApplicationAction, ApplicationManifest, IntegrationSurface


class ReaConnector(BaseApplicationConnector):
    """Connector for Chimera's Reverse Engineer Anything (REA) capability domain."""

    def __init__(self):
        actions = [
            ApplicationAction(
                action_id="analyze_artifact",
                display_name="Analyze Binary Artifact",
                description="Perform comprehensive static analysis of a binary: identify format, architecture, headers, sections, exports, imports, symbols, and entropy.",
                risk_level=ActionRiskLevel.LEVEL_0_OBSERVE,
                permission="rea.read",
                arguments_schema={"artifact_path": "string"},
            ),
            ApplicationAction(
                action_id="extract_strings",
                display_name="Extract Printable Strings",
                description="Extract ASCII and UTF-16LE printable strings from a binary file with their offsets.",
                risk_level=ActionRiskLevel.LEVEL_0_OBSERVE,
                permission="rea.read",
                arguments_schema={"artifact_path": "string", "min_length": "integer"},
            ),
            ApplicationAction(
                action_id="detect_format",
                display_name="Detect File Format",
                description="Identify the format, architecture, endianness, and target OS of a binary artifact.",
                risk_level=ActionRiskLevel.LEVEL_0_OBSERVE,
                permission="rea.read",
                arguments_schema={"artifact_path": "string"},
            ),
            ApplicationAction(
                action_id="analyze_unknown",
                display_name="Analyze Unknown Binary",
                description="Progressively identify format, select available backends, and combine findings from multiple analysis sources.",
                risk_level=ActionRiskLevel.LEVEL_0_OBSERVE,
                permission="rea.read",
                arguments_schema={"artifact_path": "string"},
            ),
            ApplicationAction(
                action_id="run_ghidra_headless",
                display_name="Run Ghidra Headless Analysis",
                description="Execute Ghidra's analyzeHeadless script for deep decompilation and analysis (requires Ghidra installation).",
                risk_level=ActionRiskLevel.LEVEL_2_REVERSIBLE,
                permission="rea.ghidra",
                arguments_schema={
                    "binary_path": "string",
                    "project_location": "string",
                    "project_name": "string",
                },
            ),
            ApplicationAction(
                action_id="list_backends",
                display_name="List Analysis Backends",
                description="List all registered analysis backends and their capability manifests.",
                risk_level=ActionRiskLevel.LEVEL_0_OBSERVE,
                permission="rea.read",
                arguments_schema={},
            ),
        ]

        manifest = ApplicationManifest(
            application_id="rea",
            display_name="Reverse Engineer Anything (REA)",
            vendor="Chimera",
            connector_version="1.0.0",
            supported_surfaces=[IntegrationSurface.NATIVE_API],
            actions=actions,
            supported_file_extensions=[
                ".exe",
                ".dll",
                ".so",
                ".dylib",
                ".elf",
                ".bin",
                ".o",
                ".a",
                ".lib",
                ".apk",
                ".dex",
                ".macho",
                ".zip",
                ".7z",
                ".tar",
                ".gz",
            ],
            known_limitations=[
                "Static analysis only; no code execution performed.",
                "Large files (>150MB) may be rejected or partially analyzed.",
                "Current Ghidra 12.2 releases require native installation and a 64-bit JDK 25+.",
                "Dynamic analysis capabilities are not yet implemented.",
            ],
            preconditions=["Artifact file must exist and be readable"],
        )
        super().__init__(manifest)

    def discover(self) -> ApplicationManifest:
        """Probe REA capabilities and backend availability."""
        from chimera.reverse_engineering import get_rea_engine

        engine = get_rea_engine()
        available_backends = engine.get_available_backends()

        self.manifest.is_installed = True  # REA is always available (built-in)
        self.manifest.is_running = False  # Not applicable for REA
        self.manifest.metadata["available_backends"] = available_backends
        self.manifest.metadata["backend_count"] = len(available_backends)

        return self.manifest

    def execute_action(self, action_id: str, arguments: dict[str, Any], approved: bool = False) -> dict[str, Any]:
        """Execute REA analysis actions."""
        from chimera.reverse_engineering import get_rea_engine

        engine = get_rea_engine()

        if action_id == "analyze_artifact":
            artifact_path = arguments.get("artifact_path", "")
            result = engine.analyze_artifact(artifact_path)
            return {
                "status": result.status,
                "artifact_id": result.artifact_id,
                "operation": result.operation,
                "backend_used": result.backend_used,
                "findings": result.findings.to_dict(),
                "confidence": result.confidence,
                "warnings": result.warnings,
                "errors": result.errors,
                "duration_ms": result.duration_ms,
            }

        if action_id == "extract_strings":
            artifact_path = arguments.get("artifact_path", "")
            min_length = arguments.get("min_length", 4)
            result = engine.extract_strings(artifact_path, min_length)
            return {
                "status": result.status,
                "artifact_id": result.artifact_id,
                "operation": result.operation,
                "backend_used": result.backend_used,
                "findings": result.findings.to_dict(),
                "confidence": result.confidence,
                "warnings": result.warnings,
                "errors": result.errors,
                "duration_ms": result.duration_ms,
            }

        if action_id == "detect_format":
            artifact_path = arguments.get("artifact_path", "")
            result = engine.detect_format(artifact_path)
            return {
                "status": result.status,
                "artifact_id": result.artifact_id,
                "operation": result.operation,
                "backend_used": result.backend_used,
                "findings": result.findings.to_dict(),
                "confidence": result.confidence,
                "warnings": result.warnings,
                "errors": result.errors,
                "duration_ms": result.duration_ms,
            }

        if action_id == "analyze_unknown":
            artifact_path = arguments.get("artifact_path", "")
            result = engine.analyze_unknown_binary(artifact_path)
            return {
                "status": result.status,
                "artifact_id": result.artifact_id,
                "operation": result.operation,
                "backend_used": result.backend_used,
                "findings": result.findings.to_dict(),
                "confidence": result.confidence,
                "warnings": result.warnings,
                "errors": result.errors,
                "duration_ms": result.duration_ms,
            }

        if action_id == "run_ghidra_headless":
            binary_path = arguments.get("binary_path", "")
            project_location = arguments.get("project_location", "")
            project_name = arguments.get("project_name", "")
            result = engine.run_ghidra_headless(binary_path, project_location, project_name)
            return {
                "status": result.status,
                "artifact_id": result.artifact_id,
                "operation": result.operation,
                "backend_used": result.backend_used,
                "findings": result.findings.to_dict(),
                "confidence": result.confidence,
                "warnings": result.warnings,
                "errors": result.errors,
                "duration_ms": result.duration_ms,
            }

        if action_id == "list_backends":
            manifests = engine.get_backend_manifests()
            return {
                "status": "completed",
                "backends": [m.to_dict() for m in manifests],
                "available_count": len([m for m in manifests if m.is_installed]),
            }

        return {"status": "failed", "error": f"Unsupported REA action: {action_id}"}
