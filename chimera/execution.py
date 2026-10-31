"""End-to-end local application execution slice."""
from dataclasses import asdict, dataclass
from pathlib import Path
import json, time, uuid
from .adapters import PaintAdapter
from .audit import AuditRecord, AuditStore
from .policy import validate_path
from .image_spec import interpret_request, spec_dict
from .scene import scene_from_image_spec, plan_scene

@dataclass(frozen=True)
class ExecutionPlan:
    task_id: str
    objective: str
    application: str
    operation: str
    output: str

def plan(objective: str, task_id: str) -> ExecutionPlan:
    objective = objective.strip()
    if not objective: raise ValueError("objective must not be empty")
    return ExecutionPlan(task_id, objective, "Microsoft Paint", "create_and_verify_bmp", "chimera-paint-artifact.bmp")

def run(objective: str, data_dir: Path, open_in_app: bool = True, provider=None) -> dict:
    data_dir = Path(data_dir).resolve(); task_id = uuid.uuid4().hex; started = time.perf_counter()
    workspace = data_dir / "executions" / task_id; audit = AuditStore(data_dir / "chimera.db")
    execution_plan = plan(objective, task_id)
    adapter = PaintAdapter(); status = adapter.status()
    try:
        if provider is None:
            module = __import__("chimera.local_model", fromlist=["LocalModelProvider", "config_from_env"])
            provider = module.LocalModelProvider(module.config_from_env())
        if provider.status().state == "ready":
            scene, metrics = plan_scene(objective, provider); image_spec = None; reasoning = "local_model_scene"
        else:
            image_spec, reasoning = interpret_request(objective, provider); scene = scene_from_image_spec(image_spec); metrics = {"mode": "deterministic", "model": None}
    except Exception as exc:
        payload = {"task_id": task_id, "objective": objective, "status": "blocked", "error": str(exc), "reasoning": "unavailable", "inference": {"status": "failed", **getattr(provider, "last_inference_metrics", {})}, "elapsed_ms": int((time.perf_counter() - started) * 1000)}
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "composition-plan.json").write_text(json.dumps({"request": objective, "reasoning": "local_model", "inference": payload["inference"], "validation_error": str(exc)}, indent=2, default=str), encoding="utf-8")
        (workspace / "execution.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload
    result = adapter.execute(objective, validate_path(workspace, data_dir), open_in_app=open_in_app, image_spec=image_spec, scene=scene)
    if scene is not None:
        result.observation.setdefault("semantic_verification", "unavailable")
        result.observation.setdefault("verification_scope", "BMP structure and deterministic scene rendering only")
    payload = {"task_id": task_id, "objective": objective, "plan": asdict(execution_plan), "application": result.application,
               "status": result.status, "artifact": str(result.artifact) if result.artifact else None,
               "observation": result.observation, "error": result.error, "adapter": asdict(status),
               "image_spec": spec_dict(image_spec) if image_spec else None, "scene": {"width": scene.width, "height": scene.height, "layers": len(scene.layers), "source": scene.source, "objects": sum(len(layer.objects) for layer in scene.layers)} if scene else None, "reasoning": reasoning, "inference": metrics, "elapsed_ms": int((time.perf_counter() - started) * 1000)}
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "composition-plan.json").write_text(json.dumps({"request": objective, "reasoning": reasoning, "inference": metrics}, indent=2, default=str), encoding="utf-8")
    if scene: (workspace / "validated-scene.json").write_text(json.dumps(asdict(scene), indent=2, default=str), encoding="utf-8")
    audit.record(AuditRecord(task_id, None, __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                             command="PaintAdapter.create_and_verify_bmp", exit_code=0 if result.status == "completed" else 1,
                             error=result.error, duration_ms=payload["elapsed_ms"], verification="pass" if result.observation.get("valid") else "not_run",
                             final_status=result.status))
    (workspace / "execution.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
