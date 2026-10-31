"""Managed loopback llama-server transport for the existing local provider."""
from __future__ import annotations
import json, os, socket, subprocess, threading, time, urllib.error, urllib.request
from pathlib import Path

class ServerUnavailable(RuntimeError): pass

def policy_path(data_dir: Path | None = None) -> Path:
    root = data_dir or Path(os.environ.get("CHIMERA_DATA_DIR", ".chimera")); root.mkdir(parents=True, exist_ok=True); return root / "server_policy.json"

def load_policy(data_dir: Path | None = None) -> dict:
    path = policy_path(data_dir)
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        return {"mode": value.get("mode", "keep_loaded"), "idle_timeout": max(0, int(value.get("idle_timeout", 0))), "startup_timeout": max(5, int(value.get("startup_timeout", 60))), "request_timeout": max(5, int(value.get("request_timeout", 60)))}
    except (OSError, ValueError, TypeError): return {"mode": "keep_loaded", "idle_timeout": 0, "startup_timeout": 60, "request_timeout": 60}

def save_policy(policy: dict, data_dir: Path | None = None) -> Path:
    allowed = {"keep_loaded", "idle_shutdown", "manual"}; mode = policy.get("mode", "keep_loaded")
    if mode not in allowed: raise ValueError("unsupported server policy")
    value = {"mode": mode, "idle_timeout": max(0, int(policy.get("idle_timeout", 0))), "startup_timeout": max(5, int(policy.get("startup_timeout", 60))), "request_timeout": max(5, int(policy.get("request_timeout", 60)))}
    path = policy_path(data_dir); path.write_text(json.dumps(value, indent=2), encoding="utf-8"); return path

def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); return int(sock.getsockname()[1])

class LlamaServerManager:
    def __init__(self, executable: str | None, model: Path | None, context=2048, threads=4, gpu_layers=0, timeout=60, port=None, idle_timeout=0):
        self.executable, self.model, self.context, self.threads = executable, model, context, threads
        self.gpu_layers, self.timeout, self.port = gpu_layers, timeout, port or free_port()
        self.process = None; self.state = "stopped"; self.started_at = None; self.last_activity = 0; self.idle_timeout = max(0, idle_timeout); self.lock = threading.RLock(); self.request_lock = threading.Lock(); self.last_error = None
    @property
    def url(self): return f"http://127.0.0.1:{self.port}"
    def _health(self):
        try:
            with urllib.request.urlopen(self.url + "/health", timeout=1) as response: return response.status == 200
        except urllib.error.HTTPError as exc: return exc.code == 200
        except OSError: return False
    def start(self):
        with self.lock:
            if self.process and self.process.poll() is None and self._health(): self.state = "ready"; return
            if not self.executable or not Path(self.executable).is_file(): raise ServerUnavailable("llama-server executable is unavailable")
            if not self.model or not self.model.is_file(): raise ServerUnavailable("GGUF model is unavailable")
            self.state = "starting"; self.last_error = None
            command = [self.executable, "--host", "127.0.0.1", "--port", str(self.port), "--model", str(self.model), "--ctx-size", str(self.context), "--threads", str(self.threads), "--n-gpu-layers", str(self.gpu_layers), "--parallel", "1", "--no-webui"]
            kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL, "shell": False, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
            self.process = subprocess.Popen(command, **kwargs); self.started_at = time.monotonic()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.state = "failed"; raise ServerUnavailable(f"llama-server exited with code {self.process.returncode}")
            if self._health(): self.state = "ready"; self.last_activity = time.monotonic(); return
            self.state = "model_loading"; time.sleep(.25)
        self.state = "failed"; self.stop(); raise ServerUnavailable("llama-server did not become healthy before timeout")
    def status(self):
        self.reap_idle()
        if self.process and self.process.poll() is not None: self.state = "failed"
        elif self.process and self._health(): self.state = "ready"
        return {"state": self.state, "url": self.url, "pid": self.process.pid if self.process else None, "error": self.last_error}
    def complete(self, prompt: str, max_tokens: int, grammar: str | None = None, cancel_event=None, on_delta=None):
        with self.request_lock:
            self.start(); self.state = "busy"; self.last_activity = time.monotonic()
            payload = {"prompt": prompt, "n_predict": max_tokens, "stream": True, "temperature": 0.2}
            if grammar: payload["grammar"] = grammar
            request = urllib.request.Request(self.url + "/completion", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
            pieces = []; started = time.perf_counter()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    for raw in response:
                        if cancel_event and cancel_event.is_set(): raise InterruptedError("local inference cancelled")
                        line = raw.decode(errors="replace").strip()
                        if not line: continue
                        if line.startswith("data:"): line = line[5:].strip()
                        if line == "[DONE]": break
                        try: item = json.loads(line)
                        except json.JSONDecodeError: continue
                        text = item.get("content") or item.get("token") or item.get("response") or ""
                        if text:
                            pieces.append(text)
                            if on_delta: on_delta(text)
                self.state = "ready"; self.last_activity = time.monotonic()
                return "".join(pieces), {"duration_ms": int((time.perf_counter()-started)*1000), "server_url": self.url, "server_pid": self.process.pid if self.process else None, "provider": "llama-server", "model": str(self.model), "grammar": bool(grammar)}
            except Exception:
                self.state = "ready" if self.process and self._health() else "failed"; raise
    def reap_idle(self):
        if self.idle_timeout and self.process and not self.request_lock.locked() and time.monotonic() - self.last_activity >= self.idle_timeout:
            self.stop(); return True
        return False
    def stop(self):
        with self.lock:
            process = self.process; self.process = None
            if process and process.poll() is None:
                process.terminate()
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=2)
            self.state = "stopped"
