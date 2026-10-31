"""Bounded local-model planning over the trusted workspace tool family."""
from pathlib import Path
import json, uuid
from .tools import WorkspaceTools, tool_catalog
from .audit import AuditRecord, AuditStore

def plan_prompt(objective: str) -> str:
    return ("Return ONLY JSON object {\"operations\":[{\"operation_id\":string,\"arguments\":object}],\"completion\":string}. Choose only operation IDs from this catalog and use no shell, code, or invented tools: " + json.dumps(tool_catalog(), separators=(",", ":")) + " Objective: " + objective)

def execute_objective(objective: str, workspace: Path, output: Path, provider, approve_writes=False, plan_data=None) -> dict:
    run_id = uuid.uuid4().hex
    trace = {"run_id": run_id, "objective": objective, "workspace": str(Path(workspace).resolve()), "capability": {"capability_id": "builtin.workspace", "version": "1.0.0"}, "approval": {"writes": bool(approve_writes)}, "operations": [], "status": "blocked"}
    if provider.status().state != "ready": trace["error"] = "local model unavailable"; return _persist(trace, output)
    try:
        data, metrics = (plan_data, {"reused_plan": True}) if plan_data is not None else provider.structured_with_metrics(plan_prompt(objective))
        if not isinstance(data, dict) or not isinstance(data.get("operations"), list): raise ValueError("model plan must contain an operations array")
        trace["plan"] = data; trace["inference"] = metrics; tools = WorkspaceTools(workspace, output)
        for item in data["operations"][:6]:
            if not isinstance(item, dict): raise ValueError("operation plan item is malformed")
            operation_id = item.get("operation_id")
            if operation_id not in tool_catalog()["operations"]: raise ValueError(f"model selected unavailable operation: {operation_id}")
            result = tools.run(operation_id, item.get("arguments", {}), approve_writes)
            trace["operations"].append({"operation_id": operation_id, "arguments": item.get("arguments", {}), "result": result.__dict__})
            if result.status in ("failed", "awaiting_approval"):
                trace["status"] = "awaiting_approval" if result.status == "awaiting_approval" else "failed"; return _persist(trace, output)
        trace["status"] = "completed"; trace["verification"] = {"operations_completed": len(trace["operations"]), "artifacts": [x["result"]["data"].get("artifact") for x in trace["operations"] if x["result"]["data"].get("artifact")]}
        return _persist(trace, output)
    except Exception as exc: trace["error"] = str(exc); return _persist(trace, output)

def _persist(trace, output):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    (output / "execution-trace.json").write_text(json.dumps(trace, indent=2, default=str), encoding="utf-8")
    try:
        AuditStore(output.parents[2] / "chimera.db").record(AuditRecord(trace["run_id"], "builtin.workspace", __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), model_state="ready", duration_ms=trace.get("inference", {}).get("duration_ms"), verification="pass" if trace["status"] == "completed" else "not_run", final_status=trace["status"], error=trace.get("error")))
    except Exception: pass
    return trace
