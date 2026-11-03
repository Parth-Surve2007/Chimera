"""Normalized analysis results schema for Reverse Engineer Anything (REA).

Distinguishes:
- Observed facts (directly parsed from binary bytes and structures).
- Derived conclusions (logical deductions based on structural facts).
- Model-generated hypotheses (inferences requiring further verification).
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

from .evidence import EvidenceProvenance


@dataclass
class StructuredFindings:
    """Findings partitioned strictly by epistemic certainty."""
    observed_facts: dict[str, Any] = field(default_factory=dict)
    derived_conclusions: list[str] = field(default_factory=list)
    model_hypotheses: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed_facts": self.observed_facts,
            "derived_conclusions": self.derived_conclusions,
            "model_hypotheses": self.model_hypotheses,
        }


@dataclass
class AnalysisResult:
    """Normalized, governed result emitted by all REA operations and backends."""
    status: str  # "completed", "failed", "unavailable", "partial"
    artifact_id: str
    operation: str
    backend_used: str
    backend_version: str
    findings: StructuredFindings = field(default_factory=StructuredFindings)
    evidence: list[EvidenceProvenance] = field(default_factory=list)
    confidence: float = 1.0  # 0.0 to 1.0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    limitations: list[str] = field(default_factory=list)
    created_at_iso: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "artifact_id": self.artifact_id,
            "operation": self.operation,
            "backend_used": self.backend_used,
            "backend_version": self.backend_version,
            "findings": self.findings.to_dict(),
            "evidence": [e.to_dict() for e in self.evidence],
            "confidence": self.confidence,
            "warnings": self.warnings,
            "errors": self.errors,
            "duration_ms": self.duration_ms,
            "limitations": self.limitations,
            "created_at_iso": self.created_at_iso,
        }
