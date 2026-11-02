"""Universal provider usage accounting and cost attribution."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
from .schemas import ExecutionProvenance, UsageMetrics

# Standard default rate baselines (can be overridden per provider/model)
# Format: (input_per_1M, output_per_1M, cached_per_1M)
DEFAULT_PRICING: dict[str, tuple[float, float, float]] = {
    "muse-spark-1.3": (1.25, 4.25, 0.15),
    "gpt-4o": (2.50, 10.00, 1.25),
    "gpt-4o-mini": (0.15, 0.60, 0.075),
    "claude-3-5-sonnet": (3.00, 15.00, 0.30),
    "gemini-1.5-pro": (1.25, 5.00, 0.30),
    "gemini-1.5-flash": (0.075, 0.30, 0.01875),
}


class UniversalUsageAccountant:
    """Persistent, provider-independent accounting for model execution tokens and spend."""

    def __init__(self, db_path: Path | None = None, default_spend_limit_usd: float = 20.0):
        self.db_path = Path(db_path) if db_path else Path(os.environ.get("CHIMERA_DATA_DIR", ".chimera")) / "universal_usage.json"
        self.default_spend_limit_usd = default_spend_limit_usd
        self.spend_limits: dict[str, float] = {}

    def set_limit(self, provider_id: str, limit_usd: float) -> None:
        self.spend_limits[provider_id] = limit_usd

    def _load(self) -> list[dict[str, Any]]:
        if not self.db_path.is_file():
            return []
        try:
            return json.loads(self.db_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self, records: list[dict[str, Any]]) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    def record_usage(
        self,
        provider_id: str,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        duration_ms: int = 0,
        actual_cost: float | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        provenance: ExecutionProvenance = ExecutionProvenance.LIVE_REMOTE,
    ) -> UsageMetrics:
        if actual_cost is not None:
            estimated_cost = actual_cost
        else:
            rates = DEFAULT_PRICING.get(model_id, (0.0, 0.0, 0.0))
            uncached = max(0, input_tokens - cached_tokens)
            estimated_cost = (
                (uncached * (rates[0] / 1_000_000))
                + (cached_tokens * (rates[2] / 1_000_000))
                + (output_tokens * (rates[1] / 1_000_000))
            )

        metrics = UsageMetrics(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            estimated_cost=round(estimated_cost, 6),
            actual_cost=actual_cost,
            duration_ms=duration_ms,
            request_count=1,
            provenance=provenance,
        )

        record = {
            "provider_id": provider_id,
            "model_id": model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "estimated_cost": metrics.estimated_cost,
            "actual_cost": actual_cost,
            "duration_ms": duration_ms,
            "task_id": task_id,
            "session_id": session_id,
            "provenance": provenance.value,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        records = self._load()
        records.append(record)
        self._save(records)
        return metrics

    def summary(self, provider_id: str | None = None) -> dict[str, Any]:
        records = self._load()
        if provider_id:
            records = [r for r in records if r.get("provider_id") == provider_id]

        total_requests = len(records)
        total_input = sum(r.get("input_tokens", 0) for r in records)
        total_output = sum(r.get("output_tokens", 0) for r in records)
        total_cached = sum(r.get("cached_tokens", 0) for r in records)
        total_cost = sum(r.get("estimated_cost", 0.0) for r in records)

        limit = self.spend_limits.get(provider_id or "", self.default_spend_limit_usd)
        return {
            "total_requests": total_requests,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cached_tokens": total_cached,
            "total_estimated_cost_usd": round(total_cost, 6),
            "spend_limit_usd": limit,
            "warning_reached": total_cost >= (limit * 0.8),
            "limit_reached": total_cost >= limit,
        }
