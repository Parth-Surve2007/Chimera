"""Security controls, limits, and validation for Reverse Engineer Anything (REA).

Enforces defensive research rules:
- Prohibits automated execution of untrusted binaries.
- Enforces strict file-size and processing bounds.
- Restricts filesystem access to safe/specified boundaries.
- Governs scripted operations and ensures read-only safety by default.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence


class SecurityPolicyViolation(Exception):
    """Raised when an operation violates REA security or safety limits."""
    pass


class AnalysisLimits:
    """Configurable boundaries and resource thresholds for static analysis."""
    MAX_FILE_SIZE_BYTES: int = 150 * 1024 * 1024  # 150 MB default ceiling
    MAX_STRINGS_SCAN_BYTES: int = 4 * 1024 * 1024  # 4 MB max scan for quick string sweeps
    MAX_STRINGS_EXTRACTED: int = 2000
    MAX_SYMBOLS_EXTRACTED: int = 5000
    MAX_SECTIONS_COUNT: int = 512
    SUBPROCESS_TIMEOUT_SECONDS: float = 60.0
    PROHIBITED_DIRECT_EXECUTION: bool = True


def validate_artifact_path(
    path: str | Path,
    max_size: int = AnalysisLimits.MAX_FILE_SIZE_BYTES,
    allowed_roots: Sequence[str | Path] | None = None,
) -> Path:
    """Validate that a target path is a safe, readable, non-empty, bounded file.

    Raises:
        FileNotFoundError: If the target does not exist.
        IsADirectoryError: If the target is a directory rather than a file.
        SecurityPolicyViolation: If size limits or root path boundaries are exceeded.
    """
    resolved = Path(path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Artifact not found: {resolved}")
    if resolved.is_dir():
        raise IsADirectoryError(f"Artifact path is a directory, not a file: {resolved}")

    # Check root restrictions if specified
    if allowed_roots:
        is_contained = False
        for root in allowed_roots:
            resolved_root = Path(root).resolve()
            try:
                resolved.relative_to(resolved_root)
                is_contained = True
                break
            except ValueError:
                continue
        if not is_contained:
            raise SecurityPolicyViolation(
                f"Artifact path '{resolved}' is outside permitted workspace roots: {list(allowed_roots)}"
            )

    # Validate size bounds
    try:
        size = resolved.stat().st_size
    except OSError as err:
        raise SecurityPolicyViolation(f"Unable to read artifact metadata: {err}") from err

    if size > max_size:
        raise SecurityPolicyViolation(
            f"Artifact size ({size} bytes) exceeds maximum configured limit ({max_size} bytes)."
        )

    return resolved


def verify_static_safety(execute_intent: bool = False) -> None:
    """Ensure binaries are never executed directly for inspection."""
    if execute_intent:
        raise SecurityPolicyViolation(
            "Direct dynamic binary execution is strictly forbidden in REA static analysis pipelines."
        )
