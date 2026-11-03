"""Chimera Reverse Engineer Anything (REA) Capability Domain.

Provides general-purpose binary and software artifact analysis through specialized
backends, built-in static parsers, and governed AgentRuntime integration.
"""
from __future__ import annotations

from .analysis_registry import AnalysisRegistry, get_analysis_registry
from .artifact import ArtifactMetadata
from .capabilities import AnalysisCapability, BackendCapabilityManifest
from .detection import ArtifactDetector, detect_artifact
from .engine import ReverseEngineeringEngine, get_rea_engine
from .evidence import EvidenceProvenance
from .results import AnalysisResult
from .security import AnalysisLimits, SecurityPolicyViolation, validate_artifact_path

__all__ = [
    "AnalysisRegistry",
    "get_analysis_registry",
    "ArtifactMetadata",
    "AnalysisCapability",
    "BackendCapabilityManifest",
    "ArtifactDetector",
    "detect_artifact",
    "ReverseEngineeringEngine",
    "get_rea_engine",
    "EvidenceProvenance",
    "AnalysisResult",
    "AnalysisLimits",
    "SecurityPolicyViolation",
    "validate_artifact_path",
]
