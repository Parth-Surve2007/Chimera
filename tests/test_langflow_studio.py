import json
from chimera.langflow_studio import LangflowService, validate_flow, save_flow

def test_langflow_is_optional_when_unconfigured(monkeypatch):
    monkeypatch.delenv("CHIMERA_LANGFLOW_URL", raising=False)
    status = LangflowService().status()
    assert not status.configured and not status.available

def test_flow_validation_rejects_code_components():
    try: validate_flow({"nodes": [{"id": "x", "component": "python_code"}]})
    except ValueError as exc: assert "untrusted" in str(exc)
    else: assert False

def test_flow_round_trip(tmp_path):
    flow = {"version": 1, "nodes": [{"id": "x", "component": "chimera.inspect"}], "edges": []}
    path = save_flow(tmp_path / "flow.json", flow)
    assert json.loads(path.read_text())["nodes"][0]["component"] == "chimera.inspect"
