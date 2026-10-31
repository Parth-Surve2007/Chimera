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

def test_workflow_stops_before_proposal_when_clarification_is_needed(tmp_path):
    model = tmp_path / "m.gguf"; model.write_bytes(b"x"); cli = tmp_path / "llama"; cli.write_bytes(b"x")
    calls = []
    provider = LocalModelProvider(LocalModelConfig(model, str(cli)), lambda prompt, config: calls.append(prompt) or '{"outcome":"make artifact"}')
    result = CapabilityWorkflow(provider, tmp_path / "work").run("make artifact")
    assert result.state == "needs_clarification" and result.readiness.questions and len(calls) == 1
    assert not list((tmp_path / "work").glob("candidate-*"))

def test_workflow_resumes_with_clarifications_and_preserves_original_request(tmp_path):
    model = tmp_path / "m.gguf"; model.write_bytes(b"x"); cli = tmp_path / "llama"; cli.write_bytes(b"x")
    calls = iter(['{"outcome":"make artifact"}', '{"outcome":"make artifact", "deliverables":["artifact"], "acceptance_criteria":["artifact exists"]}', proposal_json()])
    provider = LocalModelProvider(LocalModelConfig(model, str(cli)), lambda *_: next(calls))
    first = CapabilityWorkflow(provider, tmp_path / "work").run("make artifact")
    second = CapabilityWorkflow(provider, tmp_path / "work").run("make artifact", {"acceptance":"artifact exists", "deliverable":"artifact"})
    assert first.state == "needs_clarification" and second.state == "pending_review"
    from chimera.audit import AuditStore
    events = AuditStore(tmp_path / "chimera.db").workflow_events_for(second.run_id)
    assert any(row[0] == "clarification_supplied" for row in events)

def test_durable_clarification_session_resumes_once(tmp_path):
    model = tmp_path / "m.gguf"; model.write_bytes(b"x"); cli = tmp_path / "llama"; cli.write_bytes(b"x")
    calls = iter(['{"outcome":"make artifact"}', '{"outcome":"make artifact", "deliverables":["artifact"], "acceptance_criteria":["artifact exists"]}', proposal_json()])
    provider = LocalModelProvider(LocalModelConfig(model, str(cli)), lambda *_: next(calls))
    workflow = CapabilityWorkflow(provider, tmp_path / "work")
    pending = workflow.run("original request")
    assert pending.clarification_session_id
    resumed = workflow.resume_clarification(pending.clarification_session_id, {"acceptance": "artifact exists", "deliverable": "artifact"})
    assert resumed.state == "pending_review"
    import pytest
    with pytest.raises(ValueError): workflow.resume_clarification(pending.clarification_session_id, {"acceptance": "again", "deliverable": "artifact"})

def test_clarification_session_rejects_unknown_question_ids(tmp_path):
    model = tmp_path / "m.gguf"; model.write_bytes(b"x"); cli = tmp_path / "llama"; cli.write_bytes(b"x")
    provider = LocalModelProvider(LocalModelConfig(model, str(cli)), lambda *_: '{"outcome":"make artifact"}')
    workflow = CapabilityWorkflow(provider, tmp_path / "work"); pending = workflow.run("request")
    import pytest
    with pytest.raises(ValueError): workflow.resume_clarification(pending.clarification_session_id, {"unknown": "answer"})

def test_resuming_session_is_recoverable_with_same_continuation_id(tmp_path):
    model = tmp_path / "m.gguf"; model.write_bytes(b"x"); cli = tmp_path / "llama"; cli.write_bytes(b"x")
    calls = iter(['{"outcome":"make artifact"}', '{"outcome":"make artifact", "deliverables":["artifact"], "acceptance_criteria":["artifact exists"]}', proposal_json()])
    provider = LocalModelProvider(LocalModelConfig(model, str(cli)), lambda *_: next(calls))
    workflow = CapabilityWorkflow(provider, tmp_path / "work"); pending = workflow.run("request")
    from chimera.audit import AuditStore
    audit = AuditStore(tmp_path / "chimera.db"); continuation = "recoverable-run"; audit.clarification_claim(pending.clarification_session_id, continuation, {"acceptance": "artifact exists", "deliverable": "artifact"})
    resumed = workflow.resume_clarification(pending.clarification_session_id, {"acceptance": "artifact exists", "deliverable": "artifact"})
    assert resumed.state == "pending_review" and resumed.run_id == continuation

def test_running_continuation_remains_authoritative_until_explicit_recovery(tmp_path):
    from chimera.audit import AuditStore
    import pytest
    workflow = CapabilityWorkflow(LocalModelProvider(LocalModelConfig(None, None)), tmp_path / "work")
    audit = AuditStore(tmp_path / "chimera.db")
    audit.clarification_create("s1", "origin", "request", [{"question_id": "q", "question": "?", "reason": "required"}])
    audit.clarification_claim("s1", "continuation", {"q": "answer"})
    audit.workflow_start("continuation", "request")
    with pytest.raises(ValueError, match="recovery reconciliation"):
        workflow.resume_clarification("s1", {"q": "answer"})
    record = audit.clarification_get("s1")
    assert record["state"] == "resuming"
    report = workflow.reconcile_clarification("s1")
    assert report["run_status"] == "running" and report["read_only"] is True
    assert audit.clarification_get("s1")["state"] == "resuming"

def test_fenced_renewal_rejects_stale_revision(tmp_path):
    from chimera.audit import AuditStore
    import pytest
    audit = AuditStore(tmp_path / "chimera.db")
    audit.clarification_create("s1", "origin", "request", [])
    ownership = audit.clarification_claim("s1", "continuation", {})
    with pytest.raises(ValueError):
        audit.clarification_renew_fenced("s1", "continuation", ownership["owner_token"], ownership["fencing_token"], ownership["revision"] - 1, 120)

def test_staging_intent_is_persisted_before_promotion(tmp_path):
    from chimera.audit import AuditStore
    audit = AuditStore(tmp_path / "chimera.db")
    audit.workflow_start("run", "request")
    audit.workflow_event("run", "staging_intent", "completed", '{"attempt_id":"a1"}', "candidate", "digest")
    assert audit.workflow_events_for("run")[0][0] == "staging_intent"

def test_promotion_record_transitions_idempotently(tmp_path):
    from chimera.audit import AuditStore
    import pytest
    audit = AuditStore(tmp_path / "chimera.db")
    audit.clarification_create("s1", "origin", "request", [])
    audit.clarification_claim("s1", "run", {}, owner_token="owner")
    audit.promotion_intent("p1", "a1", "c1", "0.1.0", "s1", "run", "owner", 1, 1, tmp_path / "stage", tmp_path / "candidate", [{"path": "a.txt", "sha256": "x", "size": 1}], "d")
    with pytest.raises(ValueError): audit.promotion_transition("p1", "intent_recorded", "promotion_observed")
    args = dict(attempt_id="a1", session_id="s1", run_id="run", owner_token="owner", fencing_token=1, revision=1, candidate_id="c1", version="0.1.0", digest="d")
    audit.promotion_transition("p1", "intent_recorded", "promotion_observed", **args)
    audit.promotion_transition("p1", "promotion_observed", "promotion_observed", **args)
    assert audit.promotion_get("p1")["state"] == "promotion_observed"

def test_promotion_idempotency_rechecks_current_authority(tmp_path):
    from chimera.audit import AuditStore
    import pytest
    audit = AuditStore(tmp_path / "chimera.db")
    audit.clarification_create("s1", "origin", "request", [])
    owner = audit.clarification_claim("s1", "run", {}, owner_token="owner")
    args = dict(attempt_id="a1", session_id="s1", run_id="run", owner_token="owner", fencing_token=owner["fencing_token"], revision=owner["revision"], candidate_id="c1", version="0.1.0", digest="d")
    audit.promotion_intent("p1", "a1", "c1", "0.1.0", "s1", "run", "owner", owner["fencing_token"], owner["revision"], tmp_path / "stage", tmp_path / "candidate", [], "d")
    audit.promotion_transition("p1", "intent_recorded", "promotion_observed", **args)
    for field, value in (("attempt_id", "wrong"), ("candidate_id", "wrong"), ("version", "9"), ("digest", "wrong"), ("owner_token", "wrong"), ("fencing_token", 99), ("revision", 99), ("run_id", "wrong")):
        rejected = dict(args); rejected[field] = value
        with pytest.raises(ValueError): audit.promotion_transition("p1", "promotion_observed", "promotion_observed", **rejected)
    assert audit.promotion_get("p1")["state"] == "promotion_observed"

def test_renewal_wrapper_requires_complete_session_generation(tmp_path):
    from chimera.audit import AuditStore
    import pytest
    audit = AuditStore(tmp_path / "chimera.db")
    audit.clarification_create("s1", "origin", "request", [])
    owner = audit.clarification_claim("s1", "run", {}, owner_token="owner", lease_seconds=10)
    with pytest.raises(ValueError): audit.clarification_renew("s1", "owner", 7)
    audit.clarification_renew_fenced("s1", "run", "owner", owner["fencing_token"], owner["revision"], lease_seconds=7)
    import time
    assert audit.clarification_get("s1")["lease_expires_at"] > time.time() + 6

def test_completion_is_generation_fenced(tmp_path):
    from chimera.audit import AuditStore
    import pytest
    audit = AuditStore(tmp_path / "chimera.db")
    audit.clarification_create("s1", "origin", "request", [])
    owner = audit.clarification_claim("s1", "run", {}, owner_token="owner")
    with pytest.raises(ValueError): audit.clarification_finish("s1", "failed")
    audit.clarification_finish("s1", "failed", "run", "owner", owner["fencing_token"], owner["revision"])
    assert audit.clarification_get("s1")["state"] == "failed"

def test_recovery_required_reason_is_durable_and_activation_clears_it(tmp_path):
    from chimera.audit import AuditStore
    audit = AuditStore(tmp_path / "chimera.db")
    audit.clarification_create("s1", "origin", "request", [])
    owner = audit.clarification_claim("s1", "run", {}, owner_token="owner")
    marked = audit.clarification_recovery_required("s1", "promotion evidence requires reconciliation", "run", "owner", owner["fencing_token"], owner["revision"])
    assert marked["state"] == "recovery_required"
    assert marked["recovery_reason"] == "promotion evidence requires reconciliation"
    audit.clarification_recovery_activate("s1", "run", "owner", owner["fencing_token"], owner["revision"])
    assert audit.clarification_get("s1")["recovery_reason"] is None
    events = audit.workflow_events_for("run")
    assert [event[0] for event in events] == ["recovery_required_recorded", "recovery_activated"]
    assert "promotion evidence requires reconciliation" in events[1][2]

def test_recovery_reason_validation_is_atomic(tmp_path):
    from chimera.audit import AuditStore
    import pytest
    audit = AuditStore(tmp_path / "chimera.db")
    audit.clarification_create("s1", "origin", "request", [])
    owner = audit.clarification_claim("s1", "run", {}, owner_token="owner")
    for reason in ("", "   ", 42, "x" * 1001):
        with pytest.raises(ValueError): audit.clarification_recovery_required("s1", reason, "run", "owner", owner["fencing_token"], owner["revision"])
    assert audit.clarification_get("s1")["state"] == "resuming"
    assert audit.workflow_events_for("run") == []

def test_recovery_history_survives_reopen(tmp_path):
    from chimera.audit import AuditStore
    audit = AuditStore(tmp_path / "chimera.db")
    audit.clarification_create("s1", "origin", "request", [])
    owner = audit.clarification_claim("s1", "run", {}, owner_token="owner")
    audit.clarification_recovery_required("s1", "needs reconciliation", "run", "owner", owner["fencing_token"], owner["revision"])
    reopened = AuditStore(tmp_path / "chimera.db")
    assert len(reopened.recovery_history("s1")) == 1
    assert "needs reconciliation" in reopened.recovery_history("s1")[0][3]

def test_heartbeat_stop_serializes_against_renewal(tmp_path):
    from chimera.workflow import _HeartbeatController
    import threading
    entered = threading.Event(); release = threading.Event(); committed = []
    class FakeAudit:
        def clarification_renew_fenced(self, *args):
            entered.set(); release.wait(1); committed.append(True)
    controller = _HeartbeatController(FakeAudit(), "s", "r", "o", 1, 1, interval=0.01)
    controller.start(); assert entered.wait(1)
    done = threading.Event()
    def shutdown(): controller.stop(); done.set()
    thread = threading.Thread(target=shutdown); thread.start()
    assert not done.wait(0.05)
    release.set(); thread.join(1)
    assert done.is_set() and controller.stopped and committed == [True]
    assert controller.state == "stopped"

def test_heartbeat_renewal_error_is_observable(tmp_path):
    from chimera.workflow import _HeartbeatController
    import threading
    failed = threading.Event()
    class FakeAudit:
        def clarification_renew_fenced(self, *args): raise ValueError("stale generation")
    controller = _HeartbeatController(FakeAudit(), "s", "r", "o", 1, 1, interval=0.01)
    controller.start()
    while not controller.ownership_lost.wait(1): pass
    controller.stop()
    assert isinstance(controller.renewal_error, ValueError)
    assert controller.state == "stopped"

def test_heartbeat_stop_has_bounded_gate_wait(tmp_path):
    from chimera.workflow import _HeartbeatController
    import threading, time
    entered = threading.Event(); release = threading.Event()
    class FakeAudit:
        def clarification_renew_fenced(self, *args): entered.set(); release.wait(1)
    controller = _HeartbeatController(FakeAudit(), "s", "r", "o", 1, 1, interval=0.01, shutdown_timeout=0.05)
    controller.start(); assert entered.wait(1)
    started = time.monotonic(); assert controller.stop() is False; elapsed = time.monotonic() - started
    assert elapsed < 0.5 and controller.state == "alive_after_timeout" and not controller.stopped
    release.set(); assert controller.stop() is True

def test_heartbeat_no_renewal_starts_after_shutdown_request(tmp_path):
    from chimera.workflow import _HeartbeatController
    import threading
    entered = threading.Event(); release = threading.Event(); calls = []
    class FakeAudit:
        def clarification_renew_fenced(self, *args):
            calls.append(True); entered.set(); release.wait(1)
    controller = _HeartbeatController(FakeAudit(), "s", "r", "o", 1, 1, interval=0.01)
    controller.start(); assert entered.wait(1)
    stopper = threading.Thread(target=controller.stop); stopper.start(); release.set(); stopper.join(1)
    assert len(calls) == 1 and controller.stopped

def test_paint_adapter_creates_and_verifies_real_artifact(tmp_path):
    from chimera.adapters import PaintAdapter
    from chimera.image_spec import interpret_request
    result = PaintAdapter(executable="missing-paint").execute("make an image", tmp_path, open_in_app=False)
    assert result.status == "blocked"
    adapter = PaintAdapter(executable="python")
    spec, _ = interpret_request("create a red circle on black background")
    result = adapter.execute("make an image", tmp_path, open_in_app=False, image_spec=spec)
    assert result.status == "completed" and result.observation["valid"]
    assert result.artifact and result.artifact.is_file()

def test_execution_slice_persists_plan_observation_and_artifact(tmp_path):
    from chimera.execution import run
    from chimera.local_model import LocalModelConfig, LocalModelProvider
    result = run("create a red circle on black background", tmp_path, open_in_app=False, provider=LocalModelProvider(LocalModelConfig(None, None)))
    assert result["status"] == "completed"
    assert result["plan"]["application"] == "Microsoft Paint"
    assert result["observation"]["valid"]
    assert __import__("pathlib").Path(result["artifact"]).is_file()
    assert (tmp_path / "executions" / result["task_id"] / "execution.json").is_file()

def test_image_requests_produce_different_specs_and_deterministic_artifacts(tmp_path):
    from chimera.image_spec import interpret_request
    from chimera.adapters import PaintAdapter
    first, _ = interpret_request("create a red circle on black background 320x200")
    second, _ = interpret_request("create a blue square on white background 320x200")
    assert first != second
    adapter = PaintAdapter(executable="python")
    a = adapter.execute("x", tmp_path / "a", False, first); b = adapter.execute("x", tmp_path / "b", False, first)
    assert a.status == b.status == "completed" and a.artifact.read_bytes() == b.artifact.read_bytes()
    from chimera.adapters import verify_bmp
    assert verify_bmp(a.artifact, first)["valid"]

def test_image_bmp_pixels_match_spec_colors(tmp_path):
    from chimera.image_spec import interpret_request
    from chimera.adapters import PaintAdapter, verify_bmp
    adapter = PaintAdapter(executable="python")
    for request in ("red circle on black", "blue square on white 320x200", "green circle on black"):
        spec, _ = interpret_request(request); result = adapter.execute(request, tmp_path / request.split()[0], False, spec)
        check = verify_bmp(result.artifact, spec)
        assert result.status == "completed" and check["valid"]
        assert all(item["actual"] == item["expected"] for item in check["pixel_checks"])

def test_image_spec_invalid_and_model_unavailable_fail_safely(tmp_path):
    from chimera.image_spec import validate_image_spec, interpret_request
    from chimera.local_model import LocalModelConfig, LocalModelProvider
    import pytest
    with pytest.raises(ValueError): validate_image_spec({"width": 1, "height": 1, "shapes": []})
    with pytest.raises(ValueError): interpret_request("make a photorealistic dragon")
    result = __import__("chimera.execution", fromlist=["run"]).run("make a photorealistic dragon", tmp_path, False, LocalModelProvider(LocalModelConfig(None, None)))
    assert result["status"] == "blocked" and result["reasoning"] == "unavailable"

def test_extensible_scene_validates_and_renders_layers(tmp_path):
    from chimera.scene import validate_scene, render_scene
    scene = validate_scene({"width": 64, "height": 48, "background": "black", "layers": [{"name": "forms", "objects": [{"kind": "polygon", "x": 4, "y": 4, "width": 40, "height": 30, "points": [[4, 4], [40, 8], [20, 30]], "fill": "green"}, {"kind": "line", "x": 0, "y": 0, "width": 0, "height": 0, "points": [[0, 0], [63, 47]], "stroke": "white", "fill": "white"}]}]})
    output = tmp_path / "scene.bmp"; render_scene(output, scene)
    assert output.is_file() and output.read_bytes()[:2] == b"BM"

def test_reconcile_promotion_blocks_missing_destination(tmp_path):
    from chimera.audit import AuditStore
    audit = AuditStore(tmp_path / "chimera.db")
    audit.promotion_intent("p1", "a1", "c1", "0.1.0", None, "run", "owner", 1, 2, tmp_path / "stage", tmp_path / "candidate", [], "d")
    result = audit.reconcile_promotion("p1")
    assert result["state"] == "recovery_required" and not result["verified"]

def test_recovery_required_resume_uses_persisted_revision(tmp_path):
    from chimera.audit import AuditStore
    workflow = CapabilityWorkflow(LocalModelProvider(LocalModelConfig(None, None)), tmp_path / "work")
    audit = AuditStore(tmp_path / "chimera.db")
    audit.clarification_create("s1", "origin", "request", [{"question_id": "q", "question": "?", "reason": "required"}])
    audit.clarification_claim("s1", "continuation", {"q": "answer"}, lease_seconds=-1)
    session = audit.clarification_get("s1")
    audit.recover_expired("s1", "continuation", session["revision"], {"q": "answer"})
    result = workflow.resume_clarification("s1", {"q": "answer"})
    assert result.state == "blocked"

def test_workflow_stages_and_requires_approval(tmp_path):
    model = tmp_path / "m.gguf"; model.write_bytes(b"x"); cli = tmp_path / "llama"; cli.write_bytes(b"x")
    calls = iter(['{"outcome":"make artifact", "deliverables":["artifact"], "acceptance_criteria":["artifact exists"]}', proposal_json()])
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
    calls = iter(['{"outcome":"make artifact", "deliverables":["artifact"], "acceptance_criteria":["artifact exists"]}', proposal_json(), '{"outcome":"make artifact", "deliverables":["artifact"], "acceptance_criteria":["artifact exists"]}', proposal_json()])
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
    calls = iter(['{"outcome":"make artifact", "deliverables":["artifact"], "acceptance_criteria":["artifact exists"]}', proposal_json()])
    provider = LocalModelProvider(LocalModelConfig(model, str(cli)), lambda *_: next(calls))
    result = CapabilityWorkflow(provider, tmp_path / "work").run("make artifact")
    runs = AuditStore(tmp_path / "chimera.db").workflow_runs()
    assert len(runs) == 1 and runs[0][4] == "pending_review" and runs[0][5] == "c1"
    stages = [row[0] for row in AuditStore(tmp_path / "chimera.db").workflow_events_for(runs[0][0])]
    assert "task_analysis" in stages and "static_verification" in stages and "pending_review" in stages
    evaluation = next(row for row in AuditStore(tmp_path / "chimera.db").workflow_events_for(runs[0][0]) if row[0] == "evidence_evaluation")
    assert evaluation[3] == "c1" and evaluation[4] == runs[0][6] and '"status":"unknown"' in evaluation[2]
    metadata = json.loads((result.candidate / ".chimera-candidate.json").read_text())
    assert metadata["candidate_id"] == "c1" and metadata["manifest_digest"] == evaluation[4]
    assert metadata["manifest"] and metadata["manifest"][0]["path"] == "artifact.txt"

def test_workflow_event_preserves_large_structured_detail(tmp_path):
    from chimera.audit import AuditStore
    store = AuditStore(tmp_path / "audit.db")
    detail = json.dumps({"criteria": [{"reason": "x" * 1500}]})
    store.workflow_start("run", "test")
    store.workflow_event("run", "evidence_evaluation", "unknown", detail, "c1", "d1")
    row = store.workflow_events_for("run")[0]
    assert json.loads(row[2]) == json.loads(detail)

def test_canonical_manifest_detects_content_and_path_changes(tmp_path):
    from chimera.registry import Registry
    root = tmp_path / "candidate"; root.mkdir(); (root / "a.txt").write_text("one")
    expected, digest = Registry.canonical_manifest(root)
    (root / "a.txt").write_text("two")
    actual, changed = Registry.canonical_manifest(root)
    assert actual != expected and changed != digest
    (root / "b.txt").write_text("two")
    actual, changed = Registry.canonical_manifest(root)
    assert {item["path"] for item in actual} == {"a.txt", "b.txt"} and changed != digest

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
