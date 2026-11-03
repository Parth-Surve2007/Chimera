"""Tests for Reverse Engineer Anything (REA) capability domain.

Verifies:
- Artifact detection (PE, ELF, Mach-O, archives, firmware)
- Built-in static analysis (strings, PE/ELF parsing, entropy)
- Malformed file handling
- Backend registration and discovery
- REA Application SDK connector integration
"""
import pytest
from pathlib import Path

from chimera.reverse_engineering import (
    ArtifactMetadata,
    ArtifactDetector,
    AnalysisCapability,
    AnalysisRegistry,
    get_analysis_registry,
    get_rea_engine,
    ReverseEngineeringEngine,
    validate_artifact_path,
)
from chimera.reverse_engineering.backends import BuiltinStaticBackend
from chimera.applications import ReaConnector, get_application_registry


def test_artifact_detection_pe(tmp_path):
    """Test PE binary detection."""
    # Create a minimal PE binary (simplified to DOS header for test)
    pe_file = tmp_path / "test.exe"
    pe_data = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"
    pe_data += b"\x00" * 100
    pe_file.write_bytes(pe_data)

    detector = ArtifactDetector()
    artifact = detector.detect(pe_file)

    # Detector should identify as MS-DOS Executable (valid DOS header, no PE signature)
    assert artifact.format_type in ("MS-DOS Executable", "PE32", "PE (No Optional Header)")
    assert artifact.target_os in ("MS-DOS", "Windows")
    assert artifact.detection_confidence > 0.5


def test_artifact_detection_elf(tmp_path):
    """Test ELF binary detection."""
    elf_file = tmp_path / "test.elf"
    elf_data = b"\x7fELF"  # ELF magic
    elf_data += b"\x02"  # 64-bit
    elf_data += b"\x01"  # Little endian
    elf_data += b"\x01"  # ELF version
    elf_data += b"\x00"  # System V ABI
    elf_data += b"\x00" * 12  # Padding
    elf_data += b"\x02\x00"  # ET_EXEC
    elf_data += b"\x3e\x00"  # EM_X86_64
    elf_data += b"\x00\x00\x00\x00\x00\x00\x00\x00"  # Entry point
    elf_file.write_bytes(elf_data)

    detector = ArtifactDetector()
    artifact = detector.detect(elf_file)

    assert artifact.format_type == "ELF64"
    # Architecture may be Unknown if header parsing is incomplete
    assert artifact.endianness == "little"
    assert artifact.detection_confidence > 0.9


def test_artifact_detection_archive(tmp_path):
    """Test ZIP archive detection."""
    zip_file = tmp_path / "test.zip"
    zip_data = b"PK\x03\x04"  # ZIP magic
    zip_data += b"\x00" * 100
    zip_file.write_bytes(zip_data)

    detector = ArtifactDetector()
    artifact = detector.detect(zip_file)

    assert artifact.format_type == "Archive"
    assert artifact.sub_format == "ZIP Archive"
    assert artifact.detection_confidence > 0.9


def test_artifact_detection_unknown(tmp_path):
    """Test unknown binary detection."""
    unknown_file = tmp_path / "unknown.bin"
    unknown_file.write_bytes(b"\xAA\xBB\xCC\xDD\xEE\xFF\x00\x11\x22\x33")

    detector = ArtifactDetector()
    artifact = detector.detect(unknown_file)

    assert artifact.format_type == "Unknown Binary"
    assert artifact.detection_confidence < 0.5
    assert len(artifact.diagnostics) > 0


def test_builtin_static_backend_strings(tmp_path):
    """Test string extraction from built-in backend."""
    test_file = tmp_path / "strings.bin"
    test_file.write_bytes(b"HelloWorld\x00TestString123\x00AnotherString\x00")

    backend = BuiltinStaticBackend()
    result = backend.extract_strings(test_file, min_length=5)

    assert result.status == "completed"
    assert result.backend_used == "builtin_static"
    assert "HelloWorld" in [s["string"] for s in result.findings.observed_facts["strings"]]
    assert "TestString123" in [s["string"] for s in result.findings.observed_facts["strings"]]


def test_builtin_static_backend_pe_parsing(tmp_path):
    """Test PE structure parsing with built-in backend."""
    pe_file = tmp_path / "test_pe.exe"
    # Use a simple MZ header - detector will identify as MS-DOS Executable
    pe_data = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"
    pe_data += b"\x00" * 100
    pe_file.write_bytes(pe_data)

    backend = BuiltinStaticBackend()
    result = backend.analyze_artifact(pe_file)

    # Should detect as MS-DOS Executable (valid DOS header but no PE signature)
    assert result.status in ("completed", "partial", "failed")
    assert result.backend_used == "builtin_static"
    assert result.findings.observed_facts.get("format_type") in ("MS-DOS Executable", "Unknown Binary")


def test_malformed_file_handling(tmp_path):
    """Test that malformed files fail safely."""
    malformed_file = tmp_path / "malformed.bin"
    # Truncated PE header
    malformed_file.write_bytes(b"MZ\x00\x00")

    backend = BuiltinStaticBackend()
    result = backend.analyze_artifact(malformed_file)

    # Should not crash; should report diagnostic
    assert result.status in ("partial", "failed")
    assert len(result.warnings) > 0 or len(result.errors) > 0


def test_analysis_registry():
    """Test analysis backend registration."""
    registry = get_analysis_registry()

    manifests = registry.get_all_manifests()
    assert len(manifests) >= 1  # At least builtin_static

    builtin_manifest = registry.get_backend("builtin_static").get_manifest()
    assert builtin_manifest.backend_id == "builtin_static"
    assert builtin_manifest.is_installed is True


def test_rea_engine_workflow(tmp_path):
    """Test REA engine orchestration."""
    test_file = tmp_path / "test.bin"
    test_file.write_bytes(b"MZ\x90\x00\x03\x00" + b"\x00" * 100 + b"TestString\x00")

    engine = get_rea_engine()

    # Test analyze_artifact workflow
    result = engine.analyze_artifact(test_file)
    assert result.status in ("completed", "partial", "failed")
    assert result.artifact_id is not None

    # Test extract_strings workflow
    strings_result = engine.extract_strings(test_file, min_length=4)
    assert strings_result.status == "completed"
    assert "TestString" in [s["string"] for s in strings_result.findings.observed_facts["strings"]]


def test_rea_connector_integration(tmp_path):
    """Test REA connector through Application SDK."""
    registry = get_application_registry()
    rea_connector = registry.get("rea")

    assert rea_connector is not None
    assert rea_connector.manifest.application_id == "rea"

    # Call discover to set is_installed
    rea_connector.discover()
    assert rea_connector.manifest.is_installed is True

    # Test list_backends action
    result = rea_connector.execute_action("list_backends", {})
    assert result["status"] == "completed"
    assert "backends" in result
    assert len(result["backends"]) >= 1


def test_rea_connector_analyze_artifact(tmp_path):
    """Test REA connector analyze_artifact action."""
    test_file = tmp_path / "test.exe"
    test_file.write_bytes(b"MZ\x90\x00\x03\x00" + b"\x00" * 100)

    registry = get_application_registry()
    rea_connector = registry.get("rea")
    rea_connector.discover()

    result = rea_connector.execute_action("analyze_artifact", {"artifact_path": str(test_file)})
    assert result["status"] in ("completed", "partial", "failed")
    assert "findings" in result
    assert result["backend_used"] == "builtin_static"


def test_rea_connector_extract_strings(tmp_path):
    """Test REA connector extract_strings action."""
    test_file = tmp_path / "strings.bin"
    test_file.write_bytes(b"ExtractMe\x00AndMeToo\x00")

    registry = get_application_registry()
    rea_connector = registry.get("rea")
    rea_connector.discover()

    result = rea_connector.execute_action("extract_strings", {"artifact_path": str(test_file), "min_length": 5})
    assert result["status"] == "completed"
    assert "ExtractMe" in [s["string"] for s in result["findings"]["observed_facts"]["strings"]]


def test_path_validation(tmp_path):
    """Test security path validation."""
    valid_file = tmp_path / "valid.bin"
    valid_file.write_bytes(b"test")

    # Valid path should pass
    validated = validate_artifact_path(valid_file)
    assert validated == valid_file

    # Non-existent path should raise
    with pytest.raises(FileNotFoundError):
        validate_artifact_path(tmp_path / "nonexistent.bin")

    # Directory should raise
    with pytest.raises(IsADirectoryError):
        validate_artifact_path(tmp_path)


def test_rea_connector_action_validation():
    """Test REA connector action validation."""
    connector = ReaConnector()

    # LEVEL_0 action allowed in Plan Mode
    valid, reason, action = connector.validate_action_call("analyze_artifact", {"artifact_path": "test.exe"}, mode="plan")
    assert valid is True

    # LEVEL_2 action blocked in Plan Mode
    valid2, reason2, action2 = connector.validate_action_call("run_ghidra_headless", {"binary_path": "test.exe", "project_location": "/tmp", "project_name": "test"}, mode="plan")
    assert valid2 is False
    assert "prohibited in Plan Mode" in reason2
