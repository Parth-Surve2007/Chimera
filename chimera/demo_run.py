"""Run one honest, local-only capability acquisition demonstration."""
import argparse, json, os, time, uuid
from pathlib import Path

from .audit import AuditRecord, AuditStore
from .local_model import check_status, config_from_env, tool_version, LocalModelProvider
from .review import ReviewService
from .workflow import CapabilityWorkflow


def run(task: str, data_dir: Path) -> dict:
    run_id = uuid.uuid4().hex
    started = time.perf_counter()
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    config = config_from_env()
    status = check_status(config)
    audit = AuditStore(Path(data_dir) / "chimera.db")
    base = dict(run_id=run_id, task_summary=task[:240], genuine_local_inference=False,
                model_filename=config.model_path.name if config.model_path else None,
                runtime_version=tool_version(config.cli_path), stages={}, candidate_id=None,
                manifest_digest=None, verification=None, unresolved_checks=[],
                failure_stage=None, next_action="configure a local GGUF model and llama.cpp runtime")
    if status.state != "ready":
        base.update(status="blocked", failure_stage="runtime_validation", error=status.detail)
        audit.record(AuditRecord(run_id, None, __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), model_state=status.state, error=status.detail, duration_ms=0, final_status="blocked"))
        return base
    base["genuine_local_inference"] = True
    base["next_action"] = "inspect and explicitly approve the candidate through ReviewService"
    provider = LocalModelProvider(config)
    base["stages"]["runtime_validation"] = "passed"
    result = CapabilityWorkflow(provider, Path(data_dir) / "candidates").run(task)
    base["stages"]["acquisition"] = result.state
    base["message"] = result.message
    if result.candidate:
        service = ReviewService(data_dir, audit)
        candidate = service._record(result.candidate)
        base["candidate_id"] = candidate.candidate_id
        base["artifact_location"] = str(candidate.path)
        verification = service.verify(candidate.candidate_id)
        base["verification"] = verification.status.value
        base["manifest_digest"] = service.registry._manifest(candidate.path)[1]
        base["unresolved_checks"] = [verification.diagnostics] if verification.diagnostics else []
        base["stages"].update(candidate_staging="passed", independent_static_verification=verification.status.value, human_acceptance="required")
    else:
        base["failure_stage"] = "task_analysis_or_proposal"
    base["status"] = result.state
    base["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    audit.record(AuditRecord(run_id, base["candidate_id"], __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), model_state="ready", duration_ms=base["elapsed_ms"], verification=base.get("verification") or "not_run", final_status=result.state, error=None if result.candidate else result.message))
    return base


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m chimera.demo_run")
    parser.add_argument("task", help="A small local utility to acquire")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    data_dir = Path(os.environ.get("CHIMERA_DATA_DIR", ".chimera"))
    result = run(args.task, data_dir)
    print(json.dumps(result, indent=2) if args.as_json else _summary(result))
    return 0 if result["status"] in ("pending_review", "blocked") else 2


def _summary(result):
    return "\n".join([f"Status: {result.get('status')}", f"Candidate: {result.get('candidate_id') or 'none'}", f"Verification: {result.get('verification') or 'not run'}", f"Next action: {result.get('next_action')}", f"Details: {result.get('message') or result.get('error', '')}"])


if __name__ == "__main__":
    raise SystemExit(main())
