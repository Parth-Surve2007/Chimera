from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class Lifecycle(str, Enum):
    DRAFT = "draft"
    VALIDATING = "validating"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"

_TRANSITIONS = {
    Lifecycle.DRAFT: {Lifecycle.VALIDATING, Lifecycle.REJECTED},
    Lifecycle.VALIDATING: {Lifecycle.ACCEPTED, Lifecycle.REJECTED},
    Lifecycle.ACCEPTED: {Lifecycle.DEPRECATED},
    Lifecycle.REJECTED: {Lifecycle.DRAFT},
    Lifecycle.DEPRECATED: set(),
}

def transition_allowed(current: Lifecycle, target: Lifecycle) -> bool:
    return target in _TRANSITIONS[current]

@dataclass
class Capability:
    capability_id: str
    name: str
    domain: str
    description: str
    input_contract: dict[str, Any] = field(default_factory=dict)
    output_contract: dict[str, Any] = field(default_factory=dict)
    entrypoint: str = ""
    artifact_files: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    resource_profile: dict[str, Any] = field(default_factory=dict)
    version: str = "0.1.0"
    status: Lifecycle = Lifecycle.DRAFT
    verification_evidence: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)

