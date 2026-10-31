import json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from chimera.agent_runtime import AgentRuntime, AgentSessionStore
from chimera.model_hub import ModelProfile, ModelProfileStore, OpenAICompatibleAdapter

class Credentials:
    def get(self, provider): return "mock-secret"

class Handler(BaseHTTPRequestHandler):
    calls=[]
    def do_POST(self):
        length=int(self.headers["Content-Length"]); payload=json.loads(self.rfile.read(length)); Handler.calls.append(payload)
        if len(Handler.calls)==1:
            message={"role":"assistant","tool_calls":[{"id":"call-1","type":"function","function":{"name":"workspace.list_files","arguments":json.dumps({"relative":".","recursive":True})}}]}
        else:
            assert any(item.get("role")=="tool" for item in payload["messages"])
            message={"role":"assistant","content":"I inspected the workspace and found the requested files."}
        body=json.dumps({"choices":[{"message":message}]}).encode(); self.send_response(200); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *args): pass

def test_api_provider_drives_real_agent_tool_loop(tmp_path):
    Handler.calls=[]; server=ThreadingHTTPServer(("127.0.0.1",0),Handler); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        profiles=ModelProfileStore(tmp_path/"profiles.json"); profile=ModelProfile("mock","mock-provider","mock-model",True,base_url=f"http://127.0.0.1:{server.server_port}/v1",tool_calling=True,capability_evidence="tested"); profiles.save(profile)
        store=AgentSessionStore(tmp_path/"agent.db"); runtime=AgentRuntime(store, OpenAICompatibleAdapter(profile, Credentials()), profiles); sid=runtime.create_session(tmp_path); runtime.set_session_model(sid,"mock",local_only=False)
        result=runtime.send(sid,"Inspect this workspace")
        assert result["status"]=="completed"; assert len(Handler.calls)==2; assert any(m.role=="tool" for m in store.messages(sid)); assert "inspected" in store.messages(sid)[-1].content
        assert any(event.event_type=="model.selected" for event in store.events(sid)); assert Handler.calls[1]["messages"][-1]["role"]=="tool"
    finally: server.shutdown(); server.server_close()
