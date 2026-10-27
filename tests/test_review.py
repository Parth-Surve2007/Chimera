import json
import pytest
from chimera.review import ReviewService
from chimera.audit import AuditStore

def make_candidate(tmp_path):
    data = tmp_path / "data"; root = data / "candidates" / "c1"; root.mkdir(parents=True); (root / "artifact.txt").write_text("safe")
    (root / ".chimera-candidate.json").write_text(json.dumps({"candidate_id":"c1","name":"Test","version":"1.0.0","task":"test","permissions":[],"status":"pending_review"})); return data

def test_review_persists_restart_and_requires_approval(tmp_path):
    data = make_candidate(tmp_path); service = ReviewService(data); assert service.get("c1").status == "pending_review"
    service.verify("c1"); service.approve_execution("c1")
    with pytest.raises(PermissionError): service.accept("c1")
    service.approve_acceptance("c1"); cap = service.accept("c1"); assert cap.status.value == "validating" or cap.status.value == "accepted"
    assert any(row[0] == "acceptance" for row in service.audit.events("c1"))

def test_stale_manifest_blocks_acceptance(tmp_path):
    data = make_candidate(tmp_path); service = ReviewService(data); service.verify("c1"); service.approve_acceptance("c1"); (data / "candidates" / "c1" / "artifact.txt").write_text("changed")
    with pytest.raises(Exception): service.accept("c1")

def test_rejection_is_persisted(tmp_path):
    data = make_candidate(tmp_path); service = ReviewService(data); service.reject("c1", "not needed"); assert ReviewService(data).get("c1").status == "rejected"
