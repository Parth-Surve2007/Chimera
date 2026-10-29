"""Domain-agnostic contracts for capability acquisition."""
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

class VerificationStatus(str, Enum): PASS = "pass"; FAIL = "fail"; INCONCLUSIVE = "inconclusive"

@dataclass(frozen=True)
class TaskSpec:
    outcome: str
    inputs: dict[str, Any] = field(default_factory=dict)
    deliverables: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    resources: dict[str, Any] = field(default_factory=dict)
    permissions: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

@dataclass(frozen=True)
class ClarificationQuestion:
    question_id: str
    question: str
    reason: str
    source: str
    mandatory: bool = True

@dataclass(frozen=True)
class ReadinessAssessment:
    status: str
    questions: tuple[ClarificationQuestion, ...] = ()
    reason: str = ""

@dataclass(frozen=True)
class ProposedFile:
    path: str
    content: str

@dataclass(frozen=True)
class CapabilityProposal:
    candidate_id: str
    name: str
    purpose: str
    problem: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    assumptions: tuple[str, ...]
    dependencies: tuple[str, ...]
    permissions: tuple[str, ...]
    files: tuple[ProposedFile, ...]
    validation_plan: tuple[str, ...]
    limitations: tuple[str, ...] = ()
    requirement_coverage: tuple[dict[str, Any], ...] = ()

@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    evidence: dict[str, Any] = field(default_factory=dict)
    diagnostics: str = ""

class CandidateProvider(Protocol):
    def construct(self, task: TaskSpec, workspace: Path) -> Path: ...
    def propose(self, task: TaskSpec) -> CapabilityProposal: ...

class Verifier(Protocol):
    def verify(self, task: TaskSpec, candidate: Path) -> VerificationResult: ...

@dataclass(frozen=True)
class DiscoveryReport:
    tools: dict[str, str | None]
    runtimes: dict[str, str | None] = field(default_factory=dict)

@dataclass(frozen=True)
class AcquisitionResult:
    status: str
    attempts: int
    candidate: Path | None
    verification: VerificationResult
    diagnostics: str = ""

