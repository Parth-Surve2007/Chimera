"""Comprehensive test suite for Phase 5.0:
- Application Registry & Connector Manifests
- Ghidra Connector discovery, strings extraction, function analysis
- REAPER Connector discovery, .RPP parsing, non-destructive track addition
- ExperienceLearningEngine trace capture, secret redaction, and skill approval lifecycle
- ActionRiskLevel policy validation
"""
from pathlib import Path
import pytest

from chimera.applications import (
    ActionRiskLevel,
    ApplicationAction,
    ApplicationManifest,
    ApplicationRegistry,
    GhidraConnector,
    IntegrationSurface,
    ReaperConnector,
    get_application_registry,
)
from chimera.learning import ExperienceLearningEngine, ProposedSkill, SkillStore


def test_application_manifest_and_registry_discovery():
    """Verify application registry registers connectors and reports capability manifests."""
    registry = ApplicationRegistry()
    ghidra = GhidraConnector()
    reaper = ReaperConnector()
    registry.register(ghidra)
    registry.register(reaper)

    manifests = registry.discover_all()
    assert len(manifests) == 2
    gh_manifest = registry.get("ghidra").manifest
    assert gh_manifest.application_id == "ghidra"
    assert gh_manifest.vendor == "National Security Agency"
    assert IntegrationSurface.CLI in gh_manifest.supported_surfaces

    reaper_manifest = registry.get("reaper").manifest
    assert reaper_manifest.application_id == "reaper"
    assert reaper_manifest.vendor == "Cockos Incorporated"


def test_ghidra_static_analysis_on_binary(tmp_path):
    """Test GhidraConnector static string extraction and function enumeration without native install."""
    # Create synthetic test binary with identifiable strings
    bin_file = tmp_path / "sample.exe"
    bin_file.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00ThisProgramCannotBeRunInDOSMode\x00HelloChimera\x00SecretToken1234\x00")

    connector = GhidraConnector()
    # Test extract_strings
    res_str = connector.execute_action("extract_strings", {"binary_path": str(bin_file), "min_length": 5})
    assert res_str["status"] == "completed"
    strings_found = res_str["strings"]
    assert any("HelloChimera" in s for s in strings_found)

    # Test enumerate_functions
    res_fn = connector.execute_action("enumerate_functions", {"binary_path": str(bin_file)})
    assert res_fn["status"] == "completed"
    assert res_fn["format"] == "PE"
    assert res_fn["metadata"]["simulated"] is False
    assert res_fn["metadata"]["real_inspection"] is True


def test_reaper_rpp_project_parsing_and_track_addition(tmp_path):
    """Test ReaperConnector offline .RPP parsing and non-destructive track addition with backup."""
    rpp_file = tmp_path / "song.rpp"
    rpp_file.write_text(
        """<REAPER_PROJECT 0.1 "6.80/x64" 1680000000
  TEMPO 140 4 4
  <TRACK {GUID1}
    NAME "Vocal Lead"
  >
  <TRACK {GUID2}
    NAME "Acoustic Guitar"
  >
>
""",
        encoding="utf-8",
    )

    connector = ReaperConnector()
    # 1. Inspect project
    inspect_res = connector.execute_action("inspect_project", {"project_path": str(rpp_file)})
    assert inspect_res["status"] == "completed"
    assert inspect_res["tempo_bpm"] == 140.0
    assert inspect_res["track_count"] == 2
    assert inspect_res["tracks"][0]["name"] == "Vocal Lead"

    # 2. Add track (Reversible execution)
    add_res = connector.execute_action("add_track", {"project_path": str(rpp_file), "track_name": "Bassline"})
    assert add_res["status"] == "completed"
    assert add_res["track_added"] == "Bassline"
    assert (tmp_path / "song.rpp.bak").is_file()

    # Re-inspect to confirm modification
    reinspect = connector.execute_action("inspect_project", {"project_path": str(rpp_file)})
    assert reinspect["track_count"] == 3
    assert reinspect["tracks"][2]["name"] == "Bassline"


def test_action_risk_level_governance():
    """Verify that Plan Mode blocks Level 2+ actions and Build Mode enforces approval for Level 3."""
    connector = ReaperConnector()
    
    # LEVEL_0 action allowed in Plan Mode
    valid, reason, act = connector.validate_action_call("inspect_project", {"project_path": "fake.rpp"}, mode="plan")
    assert valid is True

    # LEVEL_2 action (add_track) blocked in Plan Mode
    valid2, reason2, act2 = connector.validate_action_call("add_track", {"project_path": "fake.rpp", "track_name": "T"}, mode="plan")
    assert valid2 is False
    assert "prohibited in Plan Mode" in reason2

    # LEVEL_3 action (render_project) requires explicit approval
    render_res = connector.execute_action("render_project", {"project_path": "f.rpp", "output_path": "o.wav"}, approved=False)
    assert render_res["status"] == "awaiting_approval"


def test_experience_learning_and_skill_approval_lifecycle(tmp_path):
    """Test ExperienceLearningEngine capturing execution trace, redacting secrets, and saving proposed skill."""
    store = SkillStore(tmp_path / "skills.json")
    engine = ExperienceLearningEngine(store)

    messages = [
        {"role": "user", "content": "Analyze binary in Ghidra"},
        {
            "role": "tool",
            "content": '{"invocation_id": "inv1", "tool": "ghidra.extract_strings", "result": {"key_secret": "my-secret-key-12345", "strings": ["func_a", "func_b"]}}',
        },
    ]

    skill = engine.extract_skill_from_trace(
        session_id="sess_1234",
        task_objective="Analyze binary in Ghidra",
        messages=messages,
        events=[],
    )

    assert skill.status == "proposed"
    assert "ghidra.read" in skill.required_permissions
    # Verify secret was redacted
    sanitized_arg = skill.reusable_steps[0]["sanitized_arguments"]
    assert sanitized_arg.get("key_secret") == "***REDACTED***"

    # Verify skill persistence and operator approval
    all_skills = store.list()
    assert len(all_skills) == 1
    assert all_skills[0]["status"] == "proposed"

    approved = store.approve(skill.skill_id)
    assert approved is True
    assert store.list()[0]["status"] == "approved"
