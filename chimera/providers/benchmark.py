"""Synthetic cross-provider benchmark.

Measures latency, streaming time-to-first-token, token usage, and tool accuracy
across model providers under identical reproducible workloads.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from chimera.providers.base import ModelProvider
from chimera.providers.schemas import (
    ExecutionProvenance,
    GenerationRequest,
    NormalizedMessage,
)


@dataclass
class BenchmarkTaskResult:
    task_name: str
    success: bool
    latency_ms: int
    ttft_ms: int | None = None  # Time to first token
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderBenchmarkReport:
    provider_id: str
    model_id: str
    dialect: str
    provenance: ExecutionProvenance
    total_tasks: int
    successful_tasks: int
    average_latency_ms: float
    total_input_tokens: int
    total_output_tokens: int
    failure_rate: float
    task_results: list[BenchmarkTaskResult] = field(default_factory=list)


class SyntheticProviderBenchmark:
    """Runs a controlled suite of synthetic tasks through any ModelProvider."""

    def __init__(self, provider: ModelProvider, model_id: str, provenance: ExecutionProvenance = ExecutionProvenance.MOCK):
        self.provider = provider
        self.model_id = model_id
        self.provenance = provenance

    def run_suite(self) -> ProviderBenchmarkReport:
        results: list[BenchmarkTaskResult] = []

        # Task 1: Basic Generation
        results.append(self._test_basic_generation())

        # Task 2: Streaming Latency & TTFT
        results.append(self._test_streaming_ttft())

        # Task 3: Tool Accuracy & Dispatch
        results.append(self._test_tool_dispatch())

        total = len(results)
        successful = sum(1 for r in results if r.success)
        avg_lat = sum(r.latency_ms for r in results) / total if total else 0.0
        tot_in = sum(r.input_tokens for r in results)
        tot_out = sum(r.output_tokens for r in results)
        failure_rate = (total - successful) / total if total else 0.0

        return ProviderBenchmarkReport(
            provider_id=self.provider.config.provider_id,
            model_id=self.model_id,
            dialect=str(self.provider.config.dialect),
            provenance=self.provenance,
            total_tasks=total,
            successful_tasks=successful,
            average_latency_ms=round(avg_lat, 2),
            total_input_tokens=tot_in,
            total_output_tokens=tot_out,
            failure_rate=round(failure_rate, 4),
            task_results=results,
        )

    def _test_basic_generation(self) -> BenchmarkTaskResult:
        t0 = time.perf_counter()
        try:
            req = GenerationRequest(
                model=self.model_id,
                messages=(NormalizedMessage(role="user", content="Synthetic benchmark query 1"),),
                temperature=0.0,
            )
            resp = self.provider.generate(req)
            dur = int((time.perf_counter() - t0) * 1000)
            return BenchmarkTaskResult(
                task_name="basic_generation",
                success=bool(resp.content),
                latency_ms=dur,
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                evidence={"preview": resp.content[:60]},
            )
        except Exception as exc:
            dur = int((time.perf_counter() - t0) * 1000)
            return BenchmarkTaskResult(
                task_name="basic_generation",
                success=False,
                latency_ms=dur,
                error=str(exc),
            )

    def _test_streaming_ttft(self) -> BenchmarkTaskResult:
        t0 = time.perf_counter()
        ttft = None
        chunks = []
        try:
            req = GenerationRequest(
                model=self.model_id,
                messages=(NormalizedMessage(role="user", content="Stream count"),),
                stream=True,
            )
            for event in self.provider.stream(req):
                if event.delta_text and ttft is None:
                    ttft = int((time.perf_counter() - t0) * 1000)
                if event.delta_text:
                    chunks.append(event.delta_text)
            dur = int((time.perf_counter() - t0) * 1000)
            text = "".join(chunks)
            return BenchmarkTaskResult(
                task_name="streaming_ttft",
                success=bool(text),
                latency_ms=dur,
                ttft_ms=ttft or dur,
                output_tokens=len(text) // 4,
                evidence={"chunks": len(chunks)},
            )
        except Exception as exc:
            dur = int((time.perf_counter() - t0) * 1000)
            return BenchmarkTaskResult(
                task_name="streaming_ttft",
                success=False,
                latency_ms=dur,
                error=str(exc),
            )

    def _test_tool_dispatch(self) -> BenchmarkTaskResult:
        t0 = time.perf_counter()
        tool_spec = {
            "id": "workspace.list_files",
            "description": "List files",
            "arguments": {"relative": "string"},
        }
        try:
            req = GenerationRequest(
                model=self.model_id,
                messages=(NormalizedMessage(role="user", content="List workspace files"),),
                tools=(tool_spec,),
            )
            resp = self.provider.generate(req)
            dur = int((time.perf_counter() - t0) * 1000)
            has_tool = bool(resp.tool_calls and resp.tool_calls[0].name == "workspace.list_files")
            return BenchmarkTaskResult(
                task_name="tool_dispatch",
                success=has_tool,
                latency_ms=dur,
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                evidence={"tool_name": resp.tool_calls[0].name if resp.tool_calls else None},
            )
        except Exception as exc:
            dur = int((time.perf_counter() - t0) * 1000)
            return BenchmarkTaskResult(
                task_name="tool_dispatch",
                success=False,
                latency_ms=dur,
                error=str(exc),
            )
