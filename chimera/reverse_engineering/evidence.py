"""Evidence provenance and reference tracking for reverse engineering findings."""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvidenceProvenance:
    """Metadata recording the origin, byte offsets, and toolchain used for a piece of evidence."""
    file_sha256: str
    file_path: str
    file_size_bytes: int
    source_tool: str
    tool_version: str
    method: str = "direct_binary_parsing"
    offset_start: int | None = None
    offset_end: int | None = None
    confidence: float = 1.0
    timestamp_iso: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    additional_context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_sha256": self.file_sha256,
            "file_path": self.file_path,
            "file_size_bytes": self.file_size_bytes,
            "source_tool": self.source_tool,
            "tool_version": self.tool_version,
            "method": self.method,
            "offset_start": self.offset_start,
            "offset_end": self.offset_end,
            "confidence": self.confidence,
            "timestamp_iso": self.timestamp_iso,
            "additional_context": self.additional_context,
        }
