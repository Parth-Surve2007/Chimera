import pytest
from pathlib import Path
from chimera.audit import AuditRecord, AuditStore
from chimera.models import Capability, Lifecycle, transition_allowed
from chimera.policy import validate_path, PermissionErrorPolicy
from chimera.registry import Registry, IntegrityError
from chimera.supervisor import ExecutionSupervisor

def test_lifecycle():
    assert transition_allowed(Lifecycle.DRAFT, Lifecycle.VALIDATING)
    assert not transition_allowed(Lifecycle.DRAFT, Lifecycle.ACCEPTED)

def test_registry_and_versioned_artifact(tmp_path):
    r = Registry(tmp_path / "data"); c = Capability("cap-1", "Test", "test", "demo")
    artifact = tmp_path / "artifact"; artifact.mkdir(); (artifact / "run.txt").write_text("v1")
    digest = r.save(c, artifact); assert digest and r.get("cap-1").version == "0.1.0"
    c.version = "0.2.0"; (artifact / "run.txt").write_text("v2"); r.save(c, artifact)
    assert (r.artifacts / "cap-1" / "0.1.0" / "run.txt").read_text() == "v1"
    assert [item.capability_id for item in r.list()] == ["cap-1"]

def test_path_traversal(tmp_path):
    with pytest.raises(PermissionErrorPolicy): validate_path(tmp_path.parent / "escape", tmp_path)

def test_audit_persistence(tmp_path):
    store = AuditStore(tmp_path / "audit.db"); item = AuditRecord("t", None, "now"); store.record(item)
    assert (tmp_path / "audit.db").exists()

def test_timeout_reporting(tmp_path):
    result = ExecutionSupervisor().run(["python", "-c", "import time; time.sleep(1)"], tmp_path, timeout=.01)
    assert result.timed_out and result.exit_code is None

def candidate(tmp_path, text="v1"):
    folder = tmp_path / ("candidate-" + text); folder.mkdir(); (folder / "main.txt").write_text(text); return folder

def test_accept_verify_tamper_and_rollback(tmp_path):
    r = Registry(tmp_path / "data")
    first = Capability("cap", "Counter", "logic", "demo", version="1.0.0", status=Lifecycle.VALIDATING)
    r.accept(first, candidate(tmp_path, "v1"))
    second = Capability("cap", "Counter", "logic", "demo", version="2.0.0", status=Lifecycle.VALIDATING)
    r.accept(second, candidate(tmp_path, "v2"))
    (r.artifacts / "cap" / "1.0.0" / "main.txt").write_text("tampered")
    with pytest.raises(IntegrityError): r.rollback("cap", "1.0.0")
    (r.artifacts / "cap" / "1.0.0" / "main.txt").write_text("v1")
    assert r.rollback("cap", "1.0.0").version == "1.0.0"

def test_candidate_rejects_symlink_and_invalid_acceptance(tmp_path):
    r = Registry(tmp_path / "data"); folder = candidate(tmp_path)
    link = folder / "link.txt"
    try: link.symlink_to(folder / "main.txt")
    except (OSError, NotImplementedError): pytest.skip("symlinks unavailable")
    with pytest.raises(IntegrityError): r.accept(Capability("x", "X", "x", "x", status=Lifecycle.VALIDATING), folder)
    with pytest.raises(IntegrityError): r.accept(Capability("x", "X", "x", "x"), candidate(tmp_path, "other"))

def test_failed_candidate_preserves_active_version(tmp_path):
    r = Registry(tmp_path / "data"); c = Capability("cap", "C", "x", "x", version="1", status=Lifecycle.VALIDATING); r.accept(c, candidate(tmp_path))
    failed = Capability("cap", "C", "x", "x", version="2", status=Lifecycle.VALIDATING)
    with pytest.raises(IntegrityError): r.accept(failed, tmp_path / "missing")
    assert r.get("cap").version == "1" and r.get("cap").status == Lifecycle.ACCEPTED

