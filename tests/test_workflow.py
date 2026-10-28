import json
from chimera.acquisition import ApprovalGate, ProposalVerifier
from chimera.contracts import TaskSpec, VerificationStatus
from chimera.local_model import LocalModelConfig, LocalModelProvider, parse_proposal
from chimera.workflow import CapabilityWorkflow

def proposal_json(path="artifact.txt"):
    return json.dumps({"candidate_id":"c1","name":"Local artifact","purpose":"test","problem":"test","inputs":{},"outputs":{},"assumptions":[],"dependencies":[],"permissions":[],"files":[{"path":path,"content":"safe\n"}],"validation_plan":["static"]})

def test_workflow_blocks_without_real_model(tmp_path):
    result = CapabilityWorkflow(LocalModelProvider(LocalModelConfig(None, None)), tmp_path).run("do work")
    assert result.state == "blocked"

def test_workflow_stages_and_requires_approval(tmp_path):
    model = tmp_path / "m.gguf"; model.write_bytes(b"x"); cli = tmp_path / "llama"; cli.write_bytes(b"x")
    calls = iter(['{"outcome":"make artifact"}', proposal_json()])
    provider = LocalModelProvider(LocalModelConfig(model, str(cli)), lambda *_: next(calls))
    result = CapabilityWorkflow(provider, tmp_path / "work").run("make artifact")
    assert result.state == "pending_review" and not ApprovalGate().can_accept()

def test_proposal_rejects_traversal_and_unsafe_source():
    import pytest
    with pytest.raises(ValueError): parse_proposal(json.loads(proposal_json("../escape")))

def test_proposal_rejects_reserved_metadata_path():
    import pytest
    with pytest.raises(ValueError): parse_proposal(json.loads(proposal_json(".chimera-candidate.json")))

def test_repeated_acquisition_uses_distinct_storage_and_preserves_first(tmp_path):
    model = tmp_path / "m.gguf"; model.write_bytes(b"x"); cli = tmp_path / "llama"; cli.write_bytes(b"x")
    calls = iter(['{"outcome":"make artifact"}', proposal_json(), '{"outcome":"make artifact"}', proposal_json()])
    provider = LocalModelProvider(LocalModelConfig(model, str(cli)), lambda *_: next(calls))
    workspace = tmp_path / "work"
    first = CapabilityWorkflow(provider, workspace).run("make artifact")
    second = CapabilityWorkflow(provider, workspace).run("make artifact")
    assert first.state == second.state == "pending_review"
    assert first.candidate != second.candidate
    assert (first.candidate / "artifact.txt").is_file()
    assert not list(workspace.glob(".staging-*"))

def test_workflow_timeline_persists_and_links_candidate(tmp_path):
    from chimera.audit import AuditStore
    model = tmp_path / "m.gguf"; model.write_bytes(b"x"); cli = tmp_path / "llama"; cli.write_bytes(b"x")
    calls = iter(['{"outcome":"make artifact"}', proposal_json()])
    provider = LocalModelProvider(LocalModelConfig(model, str(cli)), lambda *_: next(calls))
    result = CapabilityWorkflow(provider, tmp_path / "work").run("make artifact")
    runs = AuditStore(tmp_path / "chimera.db").workflow_runs()
    assert len(runs) == 1 and runs[0][4] == "pending_review" and runs[0][5] == "c1"
    stages = [row[0] for row in AuditStore(tmp_path / "chimera.db").workflow_events_for(runs[0][0])]
    assert "task_analysis" in stages and "static_verification" in stages and "pending_review" in stages

def test_capability_matching_distinguishes_missing_and_accepted():
    from chimera.discovery import match_capabilities
    from chimera.models import Capability, Lifecycle
    cap = Capability("words", "Word counter", "generic", "Counts words and lines", status=Lifecycle.ACCEPTED)
    assert match_capabilities("counts words and lines", [cap]).status == "direct"
    assert match_capabilities("render a 3d scene", [cap]).status == "missing"

def test_matching_requires_contract_metadata_for_direct_match():
    from chimera.discovery import match_capabilities
    from chimera.models import Capability, Lifecycle
    from chimera.contracts import TaskSpec
    cap = Capability("json", "Text utility", "generic", "Counts words", status=Lifecycle.ACCEPTED, input_contract={"text": "string"}, output_contract={"words": "integer"})
    result = match_capabilities(TaskSpec("counts words", inputs={"text": "string", "encoding": "string"}), [cap])
    assert result.status == "partial"
    assert "inputs:encoding" in result.evidence["contract_gaps"]

def test_matching_is_deterministic_and_does_not_claim_behavioral_proof():
    from chimera.discovery import match_capabilities
    from chimera.models import Capability, Lifecycle
    cap = Capability("words", "Word counter", "generic", "Counts words", status=Lifecycle.ACCEPTED)
    first = match_capabilities("counts words", [cap]); second = match_capabilities("counts words", [cap])
    assert first == second and first.evidence["behaviorally_verified"] is False
