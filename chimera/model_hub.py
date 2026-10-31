"""Persistent model profiles, explicit routing, and optional API adaptation."""
from __future__ import annotations
import json, os, time, urllib.error, urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path

@dataclass(frozen=True)
class ModelProfile:
    profile_id: str
    provider_id: str
    model_id: str
    hosted: bool
    display_name: str | None = None
    model_path: str | None = None
    base_url: str | None = None
    context_limit: int | None = None
    modalities: tuple[str, ...] = ("text",)
    streaming: bool = False
    tool_calling: bool = False
    structured_output: bool = False
    capability_evidence: str = "declared"
    enabled: bool = True

class ModelProfileStore:
    def __init__(self, path: Path): self.path = Path(path)
    def list(self) -> list[ModelProfile]:
        if not self.path.is_file(): return []
        data = json.loads(self.path.read_text(encoding="utf-8")); return [ModelProfile(**item) for item in data]
    def save(self, profile: ModelProfile) -> None:
        profiles = [item for item in self.list() if item.profile_id != profile.profile_id] + [profile]
        self.path.parent.mkdir(parents=True, exist_ok=True); self.path.write_text(json.dumps([asdict(item) for item in profiles], indent=2), encoding="utf-8")
    def remove(self, profile_id: str) -> None:
        self.path.write_text(json.dumps([asdict(item) for item in self.list() if item.profile_id != profile_id], indent=2), encoding="utf-8")
    def set_enabled(self, profile_id: str, enabled: bool) -> None:
        profile = next((item for item in self.list() if item.profile_id == profile_id), None)
        if profile is None: raise KeyError("model profile not found")
        self.save(ModelProfile(**{**asdict(profile), "enabled": bool(enabled)}))
    def default(self) -> str | None:
        path = self.path.with_name("model_default.json")
        try: return json.loads(path.read_text(encoding="utf-8")).get("profile_id") if path.is_file() else None
        except (OSError, ValueError): return None
    def set_default(self, profile_id: str | None) -> None:
        if profile_id and not any(item.profile_id == profile_id and item.enabled for item in self.list()): raise ValueError("default model profile is unavailable")
        path = self.path.with_name("model_default.json"); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps({"profile_id": profile_id}), encoding="utf-8")

class CredentialStore:
    """Windows Credential Manager only; secrets never enter JSON or logs."""
    def set(self, provider_id: str, secret: str) -> None:
        if os.name != "nt": raise RuntimeError("secure credential storage is unavailable on this platform")
        import win32cred
        win32cred.CredWrite({"Type": win32cred.CRED_TYPE_GENERIC, "TargetName": f"Chimera/{provider_id}", "UserName": provider_id, "CredentialBlob": secret, "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE}, 0)
    def get(self, provider_id: str) -> str:
        if os.name != "nt": raise RuntimeError("secure credential storage is unavailable on this platform")
        import win32cred
        return win32cred.CredRead(f"Chimera/{provider_id}", win32cred.CRED_TYPE_GENERIC, 0)["CredentialBlob"]
    def delete(self, provider_id: str) -> None:
        if os.name != "nt": raise RuntimeError("secure credential storage is unavailable on this platform")
        import win32cred; win32cred.CredDelete(f"Chimera/{provider_id}", win32cred.CRED_TYPE_GENERIC, 0)

class OpenAICompatibleAdapter:
    def __init__(self, profile: ModelProfile, credentials: CredentialStore | None = None):
        if not profile.hosted or not profile.base_url: raise ValueError("API profile requires an explicit base URL")
        self.profile, self.credentials = profile, credentials or CredentialStore()
    def generate(self, messages: list[dict], timeout: float = 60, cancel_event=None, on_delta=None) -> dict:
        if cancel_event and cancel_event.is_set(): raise InterruptedError("inference cancelled")
        key = self.credentials.get(self.profile.provider_id)
        payload = {"model": self.profile.model_id, "messages": messages, "stream": bool(on_delta)}
        request = urllib.request.Request(self.profile.base_url.rstrip("/") + "/chat/completions", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, method="POST")
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if on_delta:
                    parts=[]
                    for raw in response:
                        if cancel_event and cancel_event.is_set(): raise InterruptedError("inference cancelled")
                        line=raw.decode(errors="replace").strip()
                        if line.startswith("data:"):
                            line=line[5:].strip()
                            if line == "[DONE]": break
                            try: text=json.loads(line)["choices"][0].get("delta",{}).get("content","")
                            except (ValueError, KeyError, IndexError): text=""
                            if text: parts.append(text); on_delta(text)
                    content="".join(parts)
                else:
                    data=json.loads(response.read().decode()); content=data["choices"][0]["message"].get("content","")
            return {"text": content, "provider": self.profile.provider_id, "model": self.profile.model_id, "duration_ms": int((time.perf_counter()-started)*1000), "reasoning_source": "openai_compatible"}
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"configured provider request failed with HTTP {exc.code}") from None
    @staticmethod
    def _messages(messages):
        result=[]
        for item in messages:
            role=getattr(item, "role", None) or item.get("role", "user")
            content=getattr(item, "content", None) if not isinstance(item, dict) else item.get("content", "")
            if role == "tool":
                try:
                    data=json.loads(content); result.append({"role":"tool", "tool_call_id":data.get("invocation_id",""), "content":json.dumps(data.get("result",data), separators=(",",":"))})
                except (TypeError, ValueError): result.append({"role":"tool", "content":str(content)})
            else: result.append({"role":role, "content":content})
        return result
    @staticmethod
    def _tools(catalog):
        tools=[]
        for item in catalog.get("tools", []):
            if not item.get("id", "").startswith(("workspace.", "computer.")): continue
            properties={}
            for name, kind in item.get("arguments", {}).items():
                properties[name]={"type": "boolean" if kind is bool else "integer" if kind in (int,"integer") else "string" if kind in (str,"string") else "object"}
            tools.append({"type":"function", "function":{"name":item["id"], "description":item.get("description", ""), "parameters":{"type":"object", "properties":properties, "additionalProperties":False}}})
        return tools
    def decide(self, messages, catalog, session=None, cancel_event=None, on_delta=None):
        if not self.profile.tool_calling: raise RuntimeError("selected API model does not support tool calling")
        key=self.credentials.get(self.profile.provider_id)
        payload={"model":self.profile.model_id, "messages":self._messages(messages), "tools":self._tools(catalog), "tool_choice":"auto", "stream":False}
        request=urllib.request.Request(self.profile.base_url.rstrip("/")+"/chat/completions", data=json.dumps(payload).encode(), headers={"Content-Type":"application/json","Authorization":f"Bearer {key}"}, method="POST")
        if cancel_event and cancel_event.is_set(): raise InterruptedError("inference cancelled")
        try:
            with urllib.request.urlopen(request, timeout=60) as response: data=json.loads(response.read().decode())
        except urllib.error.HTTPError as exc: raise RuntimeError(f"configured provider request failed with HTTP {exc.code}") from None
        try: message=data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError, ValueError): raise RuntimeError("provider returned an invalid chat response") from None
        calls=message.get("tool_calls") or []
        if len(calls)>1: raise RuntimeError("provider returned multiple tool calls; Chimera requires one call per turn")
        if calls:
            call=calls[0]; function=call.get("function", {}); name=function.get("name")
            try: arguments=json.loads(function.get("arguments", "{}"))
            except (TypeError, ValueError): raise RuntimeError("provider returned malformed tool arguments") from None
            if not isinstance(arguments, dict) or not isinstance(name, str): raise RuntimeError("provider returned an invalid tool call")
            return {"type":"tool_call", "tool":name, "arguments":arguments, "invocation_id":call.get("id"), "_metrics":{"provider":self.profile.provider_id,"model":self.profile.model_id,"reasoning_source":"openai_compatible"}}
        content=message.get("content")
        if not isinstance(content, str) or not content.strip(): raise RuntimeError("provider returned neither a message nor a tool call")
        return {"type":"message", "content":content, "_metrics":{"provider":self.profile.provider_id,"model":self.profile.model_id,"reasoning_source":"openai_compatible"}}

class ModelRouter:
    def choose(self, profiles: list[ModelProfile], *, require_tools=False, local_only=True, modality="text", pinned_id=None) -> tuple[ModelProfile | None, str]:
        enabled=[p for p in profiles if p.enabled and (not local_only or not p.hosted) and modality in p.modalities and (not require_tools or p.tool_calling)]
        if pinned_id:
            pinned=next((p for p in enabled if p.profile_id == pinned_id), None)
            return (pinned, "pinned model selected" if pinned else "pinned model unavailable for requirements")
        return (enabled[0], "first enabled compatible model") if enabled else (None, "no enabled model satisfies privacy and capability requirements")
