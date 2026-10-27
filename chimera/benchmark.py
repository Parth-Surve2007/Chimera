"""Small local inference benchmark; reports only observed measurements."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json, os
from pathlib import Path
from .local_model import LocalModelProvider

@dataclass(frozen=True)
class BenchmarkRun:
    outcome: str; status: str; duration_ms: int; generated_tokens: int; tokens_per_second: float | None; error: str | None

def run(provider: LocalModelProvider, request: str, repeats: int = 1) -> dict:
    results = []
    for _ in range(max(1, repeats)):
        result = provider.plan(request); rate = result.generated_tokens / (result.duration_ms / 1000) if result.duration_ms and result.generated_tokens else None
        results.append(BenchmarkRun(request[:80], result.status, result.duration_ms, result.generated_tokens, rate, result.error))
    return {"timestamp": datetime.now(timezone.utc).isoformat(), "runs": [asdict(r) for r in results], "configuration": asdict(provider.config), "model_file_size": provider.config.model_path.stat().st_size if provider.config.model_path and provider.config.model_path.is_file() else None, "resource_metrics": {"peak_process_ram": "unavailable", "gpu_memory": "unavailable"}}

def write_report(report: dict, directory: Path = Path(".chimera") / "benchmarks") -> Path:
    directory.mkdir(parents=True, exist_ok=True); target = directory / "local_model.json"; target.write_text(json.dumps(report, default=str, indent=2), encoding="utf-8"); return target

