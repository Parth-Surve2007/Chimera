from chimera.acquisition import AcquisitionEngine, FakeProvider, FileVerifier
from chimera.contracts import TaskSpec, VerificationResult, VerificationStatus
from chimera.discovery import discover

def test_generic_task_and_fake_acquisition(tmp_path):
    task = TaskSpec("produce a deliverable", deliverables=["artifact"], acceptance_criteria=["nonempty"])
    result = AcquisitionEngine(tmp_path / "candidates").acquire(task, FakeProvider(), FileVerifier())
    assert result.status == "accepted" and result.verification.status == VerificationStatus.PASS

def test_inconclusive_verification_is_not_accepted(tmp_path):
    class Unknown:
        def verify(self, task, candidate): return VerificationResult(VerificationStatus.INCONCLUSIVE, diagnostics="no independent check")
    result = AcquisitionEngine(tmp_path / "candidates").acquire(TaskSpec("unknown"), FakeProvider(), Unknown())
    assert result.status == "unverified" and result.candidate is None

def test_repair_attempts_are_bounded(tmp_path):
    class Failing:
        def construct(self, task, workspace): workspace.mkdir(); return workspace / "missing"
    result = AcquisitionEngine(tmp_path / "candidates", max_attempts=2).acquire(TaskSpec("fail"), Failing(), FileVerifier())
    assert result.attempts == 2 and result.status == "rejected"

def test_tool_discovery_reports_missing_tools():
    report = discover(["tool-that-does-not-exist"]); assert report.tools["tool-that-does-not-exist"] is None
