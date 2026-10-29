import json
import pytest
from chimera.review import ReviewService
from chimera.audit import AuditStore
from chimera.registry import Registry

def make_candidate(tmp_path):
    data = tmp_path / "data"; root = data / "candidates" / "c1"; root.mkdir(parents=True); (root / "artifact.txt").write_text("safe")
    (root / ".chimera-candidate.json").write_text(json.dumps({"candidate_id":"c1","name":"Test","version":"1.0.0","task":"test","permissions":[],"status":"pending_review"})); return data

def anchor_candidate(data):
    root = data / "candidates" / "c1"; manifest, digest = Registry.canonical_manifest(root)
    meta = json.loads((root / ".chimera-candidate.json").read_text()); meta.update({"manifest": manifest, "manifest_digest": digest, "workflow_run_id": "run-c1"}); (root / ".chimera-candidate.json").write_text(json.dumps(meta))
    audit = AuditStore(data / "chimera.db"); audit.workflow_start("run-c1", "fixture"); audit.workflow_event("run-c1", "manifest_anchor", "completed", json.dumps({"candidate_id":"c1", "version":"1.0.0", "manifest":manifest}), "c1", digest)

def test_review_persists_restart_and_requires_approval(tmp_path):
    data = make_candidate(tmp_path); anchor_candidate(data); service = ReviewService(data); assert service.get("c1").status == "pending_review"
    service.verify("c1"); service.approve_execution("c1")
    with pytest.raises(PermissionError): service.accept("c1")
    service.approve_acceptance("c1"); cap = service.accept("c1"); assert cap.status.value == "validating" or cap.status.value == "accepted"
    assert any(row[0] == "acceptance" for row in service.audit.events("c1"))

def test_stale_manifest_blocks_acceptance(tmp_path):
    data = make_candidate(tmp_path); anchor_candidate(data); service = ReviewService(data); service.verify("c1"); service.approve_acceptance("c1"); (data / "candidates" / "c1" / "artifact.txt").write_text("changed")
    with pytest.raises(Exception): service.accept("c1")

def test_legacy_candidate_without_anchor_is_blocked(tmp_path):
    data = make_candidate(tmp_path); service = ReviewService(data)
    with pytest.raises(Exception): service.verify("c1")

def test_duplicate_anchor_is_rejected(tmp_path):
    data = make_candidate(tmp_path); anchor_candidate(data); audit = AuditStore(data / "chimera.db"); anchor = audit.manifest_anchor("run-c1"); audit.workflow_event("run-c1", "manifest_anchor", "completed", json.dumps({"candidate_id":"c1", "version":"1.0.0", "manifest":anchor["manifest"]}), "c1", anchor["manifest_digest"])
    with pytest.raises(Exception): ReviewService(data).verify("c1")

def test_acceptance_approval_requires_anchor(tmp_path):
    data = make_candidate(tmp_path)
    with pytest.raises(Exception): ReviewService(data).approve_acceptance("c1")

@pytest.mark.parametrize("size", [True, False, -1, 1.5, "1", None])
def test_invalid_manifest_sizes_are_rejected(tmp_path, size):
    data = make_candidate(tmp_path); anchor_candidate(data); root = data / "candidates" / "c1"; meta_path = root / ".chimera-candidate.json"; meta = json.loads(meta_path.read_text()); meta["manifest"][0]["size"] = size; meta_path.write_text(json.dumps(meta))
    with pytest.raises(Exception): ReviewService(data).verify("c1")

def test_anchor_digest_must_match_stored_manifest(tmp_path):
    data = make_candidate(tmp_path); anchor_candidate(data); root = data / "candidates" / "c1"; meta_path = root / ".chimera-candidate.json"; meta = json.loads(meta_path.read_text()); meta["manifest_digest"] = "0" * 64; meta_path.write_text(json.dumps(meta))
    with pytest.raises(Exception): ReviewService(data).verify("c1")

def test_rejection_is_persisted(tmp_path):
    data = make_candidate(tmp_path); service = ReviewService(data); service.reject("c1", "not needed"); assert ReviewService(data).get("c1").status == "rejected"
