"""Result Verification Framework for Chimera Phase 5.3.

Provides verification mechanisms for different types of operations and results,
ensuring that tool execution success translates to actual task completion.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class VerificationResult:
    """Result of a verification check."""
    status: str  # "verified", "failed", "unknown"
    evidence: dict[str, Any]
    reason: str | None = None


class ResultVerifier:
    """Verifies that operation results meet expectations."""

    def verify(
        self,
        capability: str,
        tool: str,
        result: dict[str, Any],
        expected_output: str | None = None,
    ) -> VerificationResult:
        """Verify an operation result based on capability and tool type."""
        if not result or result.get("status") != "completed":
            return VerificationResult(
                status="failed",
                evidence={"result_status": result.get("status") if result else "none"},
                reason="Operation did not complete successfully",
            )

        # Route to specific verifier
        if capability == "workspace":
            return self._verify_workspace_operation(tool, result, expected_output)
        elif capability == "rea":
            return self._verify_rea_operation(tool, result, expected_output)
        elif capability == "ghidra":
            return self._verify_ghidra_operation(tool, result, expected_output)
        elif capability == "computer":
            return self._verify_computer_operation(tool, result, expected_output)
        else:
            return VerificationResult(
                status="unknown",
                evidence={"capability": capability, "tool": tool},
                reason=f"No specific verifier for capability: {capability}",
            )

    def _verify_workspace_operation(
        self,
        tool: str,
        result: dict[str, Any],
        expected_output: str | None,
    ) -> VerificationResult:
        """Verify workspace operation results."""
        data = result.get("data", {})

        if tool == "create_artifact":
            # Verify file was created and has content
            artifact_path = data.get("artifact")
            if not artifact_path:
                return VerificationResult(
                    status="failed",
                    evidence=data,
                    reason="Artifact path not in result",
                )

            path = Path(artifact_path)
            if not path.exists():
                return VerificationResult(
                    status="failed",
                    evidence={"path": str(path)},
                    reason="Artifact file does not exist",
                )

            if not path.is_file():
                return VerificationResult(
                    status="failed",
                    evidence={"path": str(path)},
                    reason="Artifact path is not a file",
                )

            file_size = data.get("bytes", 0)
            if file_size == 0:
                return VerificationResult(
                    status="failed",
                    evidence={"size": file_size},
                    reason="Artifact file is empty",
                )

            # Read back and verify content
            try:
                actual_content = path.read_text(encoding="utf-8")
                return VerificationResult(
                    status="verified",
                    evidence={
                        "path": str(path),
                        "size": file_size,
                        "content_length": len(actual_content),
                    },
                )
            except Exception as exc:
                return VerificationResult(
                    status="failed",
                    evidence={"path": str(path)},
                    reason=f"Failed to read artifact: {exc}",
                )

        elif tool == "read_text":
            # Verify content was returned
            content = data.get("content")
            if not content or not isinstance(content, str):
                return VerificationResult(
                    status="failed",
                    evidence=data,
                    reason="Content not in result or not a string",
                )

            if len(content) == 0:
                return VerificationResult(
                    status="failed",
                    evidence={"length": 0},
                    reason="Returned content is empty",
                )

            return VerificationResult(
                status="verified",
                evidence={"path": data.get("path"), "content_length": len(content)},
            )

        elif tool in ("list_files", "search_files"):
            # Verify file list was returned
            files = data.get("files")
            if not isinstance(files, list):
                return VerificationResult(
                    status="failed",
                    evidence=data,
                    reason="Files not in result or not a list",
                )

            return VerificationResult(
                status="verified",
                evidence={"file_count": len(files)},
            )

        elif tool == "search_text":
            # Verify search results were returned
            matches = data.get("matches")
            if not isinstance(matches, list):
                return VerificationResult(
                    status="failed",
                    evidence=data,
                    reason="Matches not in result or not a list",
                )

            return VerificationResult(
                status="verified",
                evidence={"match_count": len(matches)},
            )

        return VerificationResult(
            status="unknown",
            evidence={"tool": tool},
            reason=f"No specific verifier for workspace tool: {tool}",
        )

    def _verify_rea_operation(
        self,
        tool: str,
        result: dict[str, Any],
        expected_output: str | None,
    ) -> VerificationResult:
        """Verify REA operation results."""
        data = result.get("data", {})

        if tool == "analyze_artifact":
            # Verify analysis findings are present
            findings = data.get("findings")
            if not findings or not isinstance(findings, dict):
                return VerificationResult(
                    status="failed",
                    evidence=data,
                    reason="Findings not in result or not a dict",
                )

            # Check for expected analysis fields
            required_fields = ["sha256", "format_type", "architecture"]
            missing_fields = [f for f in required_fields if f not in findings]
            if missing_fields:
                return VerificationResult(
                    status="failed",
                    evidence={"findings": findings, "missing": missing_fields},
                    reason=f"Missing required analysis fields: {missing_fields}",
                )

            return VerificationResult(
                status="verified",
                evidence={
                    "artifact_id": data.get("artifact_id"),
                    "format": findings.get("format_type"),
                    "architecture": findings.get("architecture"),
                    "sections_count": findings.get("sections_count", 0),
                },
            )

        elif tool == "extract_strings":
            # Verify strings were extracted
            findings = data.get("findings")
            if not findings or not isinstance(findings, dict):
                return VerificationResult(
                    status="failed",
                    evidence=data,
                    reason="Findings not in result",
                )

            strings = findings.get("strings")
            if not isinstance(strings, list):
                return VerificationResult(
                    status="failed",
                    evidence={"findings": findings},
                    reason="Strings not in findings or not a list",
                )

            return VerificationResult(
                status="verified",
                evidence={"string_count": len(strings)},
            )

        elif tool == "detect_format":
            # Verify format detection results
            findings = data.get("findings")
            if not findings or not isinstance(findings, dict):
                return VerificationResult(
                    status="failed",
                    evidence=data,
                    reason="Findings not in result",
                )

            if "format_type" not in findings:
                return VerificationResult(
                    status="failed",
                    evidence={"findings": findings},
                    reason="Format type not in findings",
                )

            return VerificationResult(
                status="verified",
                evidence={
                    "format": findings.get("format_type"),
                    "architecture": findings.get("architecture"),
                    "confidence": data.get("confidence"),
                },
            )

        return VerificationResult(
            status="unknown",
            evidence={"tool": tool},
            reason=f"No specific verifier for REA tool: {tool}",
        )

    def _verify_ghidra_operation(
        self,
        tool: str,
        result: dict[str, Any],
        expected_output: str | None,
    ) -> VerificationResult:
        """Verify Ghidra operation results."""
        data = result.get("data", {})

        if tool == "extract_strings":
            strings = data.get("strings")
            if not isinstance(strings, list):
                return VerificationResult(
                    status="failed",
                    evidence=data,
                    reason="Strings not in result or not a list",
                )

            return VerificationResult(
                status="verified",
                evidence={"string_count": len(strings)},
            )

        elif tool == "enumerate_functions":
            functions = data.get("functions")
            if not isinstance(functions, list):
                return VerificationResult(
                    status="failed",
                    evidence=data,
                    reason="Functions not in result or not a list",
                )

            return VerificationResult(
                status="verified",
                evidence={"function_count": len(functions)},
            )

        return VerificationResult(
            status="unknown",
            evidence={"tool": tool},
            reason=f"No specific verifier for Ghidra tool: {tool}",
        )

    def _verify_computer_operation(
        self,
        tool: str,
        result: dict[str, Any],
        expected_output: str | None,
    ) -> VerificationResult:
        """Verify computer operation results."""
        data = result.get("data", {})

        if tool in ("list_applications", "list_windows"):
            items = data.get("items")
            if not isinstance(items, list):
                return VerificationResult(
                    status="failed",
                    evidence=data,
                    reason="Items not in result or not a list",
                )

            return VerificationResult(
                status="verified",
                evidence={"item_count": len(items)},
            )

        elif tool == "screenshot":
            screenshot_path = data.get("path")
            if not screenshot_path:
                return VerificationResult(
                    status="failed",
                    evidence=data,
                    reason="Screenshot path not in result",
                )

            path = Path(screenshot_path)
            if not path.exists():
                return VerificationResult(
                    status="failed",
                    evidence={"path": str(path)},
                    reason="Screenshot file does not exist",
                )

            return VerificationResult(
                status="verified",
                evidence={"path": str(path), "size": path.stat().st_size},
            )

        return VerificationResult(
            status="unknown",
            evidence={"tool": tool},
            reason=f"No specific verifier for computer tool: {tool}",
        )
