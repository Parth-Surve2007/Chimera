import json
from chimera.model_hub import ModelProfile, ModelProfileStore, ModelRouter, OpenAICompatibleAdapter

class FakeCredentials:
    def get(self, provider): return "secret-not-persisted"

class FakeResponse:
    def __init__(self, payload): self.payload=payload
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return json.dumps(self.payload).encode()
    def __iter__(self): return iter(())

def test_profiles_round_trip_without_credentials(tmp_path):
    store=ModelProfileStore(tmp_path/"models.json"); p=ModelProfile("local","llama-server","llama",False,model_path="x.gguf",tool_calling=True)
    store.save(p); assert store.list()[0].profile_id == "local"

def test_router_respects_local_only_and_tools():
    local=ModelProfile("local","llama","m",False,tool_calling=True); api=ModelProfile("api","remote","m",True,base_url="https://example.invalid",tool_calling=True)
    chosen, reason=ModelRouter().choose([api,local],require_tools=True,local_only=True); assert chosen == local

def test_router_reports_missing_capability():
    chosen, reason=ModelRouter().choose([ModelProfile("x","p","m",False)],require_tools=True); assert chosen is None and "no enabled" in reason

def test_profile_enable_and_default_are_persisted(tmp_path):
    store=ModelProfileStore(tmp_path/"models.json"); store.save(ModelProfile("local","llama","m",False)); store.set_default("local"); store.set_enabled("local",False)
    assert store.default() == "local" and not store.list()[0].enabled

def test_openai_adapter_normalizes_tool_call(monkeypatch):
    profile=ModelProfile("api","provider","model",True,base_url="http://127.0.0.1:1",tool_calling=True)
    payload={"choices":[{"message":{"tool_calls":[{"id":"call-1","function":{"name":"workspace.list_files","arguments":json.dumps({"relative":".","recursive":True})}}]}}]}
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: FakeResponse(payload))
    decision=OpenAICompatibleAdapter(profile, FakeCredentials()).decide([], {"tools":[{"id":"workspace.list_files","arguments":{"relative":"string","recursive":"boolean"}}]})
    assert decision["type"] == "tool_call" and decision["arguments"]["recursive"] is True

def test_openai_adapter_rejects_tool_capability_absence():
    profile=ModelProfile("api","provider","model",True,base_url="http://127.0.0.1:1",tool_calling=False)
    try: OpenAICompatibleAdapter(profile, FakeCredentials()).decide([], {"tools":[]})
    except RuntimeError as exc: assert "does not support" in str(exc)
    else: assert False
