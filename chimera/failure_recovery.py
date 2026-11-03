"""Intelligent Failure Recovery for Chimera Phase 5.3.

Handles realistic failures with retry logic, dependency propagation,
and appropriate recovery strategies based on failure type.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any


class FailureType(Enum):
    """Classification of failure types for recovery strategy selection."""
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"
    MISSING_DEPENDENCY = "missing_dependency"
    PERMISSION_BLOCK = "permission_block"
    PARTIAL_COMPLETION = "partial_completion"
    UNRECOVERABLE = "unrecoverable"


@dataclass
class FailureAnalysis:
    """Analysis of a failure with recommended recovery strategy."""
    failure_type: FailureType
    error_message: str
    can_retry: bool
    max_retries: int
    retry_delay_seconds: float
    suggested_alternative: str | None = None
    explanation: str | None = None


class FailureRecovery:
    """Analyzes failures and recommends recovery strategies."""

    def __init__(self, max_retries: int = 3, base_retry_delay: float = 1.0):
        self.max_retries = max_retries
        self.base_retry_delay = base_retry_delay

    def analyze_failure(
        self,
        capability: str,
        tool: str,
        error: str,
        retry_count: int,
    ) -> FailureAnalysis:
        """Analyze a failure and determine recovery strategy."""
        error_lower = error.lower()

        # Permission-related failures
        if any(kw in error_lower for kw in ["permission", "denied", "unauthorized", "forbidden"]):
            return FailureAnalysis(
                failure_type=FailureType.PERMISSION_BLOCK,
                error_message=error,
                can_retry=False,
                max_retries=0,
                retry_delay_seconds=0.0,
                explanation="Permission denied - requires operator approval or configuration change",
            )

        # Missing dependency failures
        if any(kw in error_lower for kw in ["not found", "missing", "does not exist", "no such file"]):
            return FailureAnalysis(
                failure_type=FailureType.MISSING_DEPENDENCY,
                error_message=error,
                can_retry=False,
                max_retries=0,
                retry_delay_seconds=0.0,
                explanation="Required dependency or file is missing",
            )

        # Backend unavailable failures
        if any(kw in error_lower for kw in ["backend", "ghidra", "installation", "not installed"]):
            return FailureAnalysis(
                failure_type=FailureType.MISSING_DEPENDENCY,
                error_message=error,
                can_retry=False,
                max_retries=0,
                retry_delay_seconds=0.0,
                suggested_alternative="Use built-in static analysis backend",
                explanation="Specialized backend is unavailable; fallback to alternative capability",
            )

        # Timeout failures
        if any(kw in error_lower for kw in ["timeout", "timed out"]):
            if retry_count < self.max_retries:
                return FailureAnalysis(
                    failure_type=FailureType.RETRYABLE,
                    error_message=error,
                    can_retry=True,
                    max_retries=self.max_retries,
                    retry_delay_seconds=self.base_retry_delay * (2 ** retry_count),  # Exponential backoff
                    explanation="Operation timed out - may succeed on retry",
                )
            return FailureAnalysis(
                failure_type=FailureType.NON_RETRYABLE,
                error_message=error,
                can_retry=False,
                max_retries=0,
                retry_delay_seconds=0.0,
                explanation="Operation timed out after maximum retries",
            )

        # Network/connection failures
        if any(kw in error_lower for kw in ["connection", "network", "unreachable"]):
            if retry_count < self.max_retries:
                return FailureAnalysis(
                    failure_type=FailureType.RETRYABLE,
                    error_message=error,
                    can_retry=True,
                    max_retries=self.max_retries,
                    retry_delay_seconds=self.base_retry_delay * (2 ** retry_count),
                    explanation="Connection issue - may be transient",
                )
            return FailureAnalysis(
                failure_type=FailureType.NON_RETRYABLE,
                error_message=error,
                can_retry=False,
                max_retries=0,
                retry_delay_seconds=0.0,
                explanation="Connection issue persisted after retries",
            )

        # Malformed input failures
        if any(kw in error_lower for kw in ["invalid", "malformed", "bad request", "type error"]):
            return FailureAnalysis(
                failure_type=FailureType.NON_RETRYABLE,
                error_message=error,
                can_retry=False,
                max_retries=0,
                retry_delay_seconds=0.0,
                explanation="Invalid input - retry will not help without correction",
            )

        # Resource exhaustion
        if any(kw in error_lower for kw in ["memory", "disk", "space", "resource"]):
            return FailureAnalysis(
                failure_type=FailureType.NON_RETRYABLE,
                error_message=error,
                can_retry=False,
                max_retries=0,
                retry_delay_seconds=0.0,
                explanation="Resource exhaustion - retry will not help",
            )

        # Default: treat as retryable up to limit
        if retry_count < self.max_retries:
            return FailureAnalysis(
                failure_type=FailureType.RETRYABLE,
                error_message=error,
                can_retry=True,
                max_retries=self.max_retries,
                retry_delay_seconds=self.base_retry_delay,
                explanation="Generic error - attempting retry",
            )

        return FailureAnalysis(
            failure_type=FailureType.NON_RETRYABLE,
            error_message=error,
            can_retry=False,
            max_retries=0,
            retry_delay_seconds=0.0,
            explanation="Generic error - maximum retries exceeded",
        )

    def should_propagate_failure(
        self,
        failure_type: FailureType,
        step_position: int,
        total_steps: int,
    ) -> bool:
        """Determine if a failure should propagate to dependent steps."""
        # Permission blocks and missing dependencies always propagate
        if failure_type in (FailureType.PERMISSION_BLOCK, FailureType.MISSING_DEPENDENCY):
            return True

        # Non-retryable failures propagate
        if failure_type == FailureType.NON_RETRYABLE:
            return True

        # Retryable failures at critical positions may propagate
        if failure_type == FailureType.RETRYABLE and step_position == 0:
            # First step failure likely blocks everything
            return True

        return False

    def get_alternative_capability(
        self,
        failed_capability: str,
        failed_tool: str,
    ) -> tuple[str, str] | None:
        """Suggest an alternative capability/tool for failed operation."""
        alternatives = {
            "ghidra": {
                "extract_strings": ("rea", "extract_strings"),
                "enumerate_functions": ("rea", "analyze_artifact"),
                "analyze_headless": ("rea", "analyze_artifact"),
            },
        }

        if failed_capability in alternatives:
            if failed_tool in alternatives[failed_capability]:
                return alternatives[failed_capability][failed_tool]

        return None

    def calculate_retry_delay(self, retry_count: int, base_delay: float) -> float:
        """Calculate retry delay with exponential backoff."""
        return base_delay * (2 ** min(retry_count, 5))  # Cap at 32x

    def attempt_recovery(
        self,
        failure_analysis: FailureAnalysis,
        retry_count: int,
    ) -> bool:
        """Determine if recovery should be attempted."""
        if not failure_analysis.can_retry:
            return False

        if retry_count >= failure_analysis.max_retries:
            return False

        return True
