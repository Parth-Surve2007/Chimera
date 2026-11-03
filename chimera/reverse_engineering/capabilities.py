"""Capabilities and operation definitions for REA engines and backends."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AnalysisCapability(str, Enum):
    """Discrete reverse engineering analysis operations supported by backends."""
    IDENTIFY = "identify"
    HASHES = "hashes"
    STRINGS = "strings"
    HEADERS = "headers"
    SECTIONS = "sections"
    EXPORTS = "exports"
    IMPORTS = "imports"
    SYMBOLS = "symbols"
    ENTROPY = "entropy"
    ARCHIVES = "archives"
    DISASSEMBLE = "disassemble"
    DECOMPILE = "decompile"
    CALLGRAPH = "callgraph"
    DATA_TYPES = "data_types"
    SCRIPT_EXECUTION = "script_execution"
    HEADLESS_ANALYSIS = "headless_analysis"
    WORKFLOW_UNDERSTAND = "workflow_understand"
    WORKFLOW_UNKNOWN = "workflow_unknown"
    WORKFLOW_INTERNALS = "workflow_internals"
    WORKFLOW_FIRMWARE = "workflow_firmware"


@dataclass
class BackendCapabilityManifest:
    """Declared capabilities, dependencies, and operational status of an analysis backend."""
    backend_id: str
    display_name: str
    version: str
    is_installed: bool = False
    supported_capabilities: list[AnalysisCapability] = field(default_factory=list)
    supported_formats: list[str] = field(default_factory=list)
    execution_mode: str = "native_static"  # "native_static", "subprocess_cli", "rpc"
    missing_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def supports(self, capability: AnalysisCapability | str) -> bool:
        cap = AnalysisCapability(capability) if isinstance(capability, str) else capability
        return cap in self.supported_capabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend_id": self.backend_id,
            "display_name": self.display_name,
            "version": self.version,
            "is_installed": self.is_installed,
            "supported_capabilities": [c.value for c in self.supported_capabilities],
            "supported_formats": self.supported_formats,
            "execution_mode": self.execution_mode,
            "missing_reason": self.missing_reason,
            "metadata": self.metadata,
        }
