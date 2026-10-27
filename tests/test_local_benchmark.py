import json
from chimera.benchmark import run, write_report
from chimera.local_model import LocalModelConfig, LocalModelProvider

def test_benchmark_report_serializes_observed_fake_runs(tmp_path):
    config = LocalModelConfig(None, None)
    provider = LocalModelProvider(config, lambda *_: '{"outcome":"test"}')
    report = run(provider, "private request", repeats=2); path = write_report(report, tmp_path)
    loaded = json.loads(path.read_text())
    assert len(loaded["runs"]) == 2 and loaded["resource_metrics"]["peak_process_ram"] == "unavailable"
