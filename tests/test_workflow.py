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
