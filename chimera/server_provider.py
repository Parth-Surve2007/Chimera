"""Managed loopback llama-server transport for the existing local provider."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


class ServerUnavailable(RuntimeError):
    """The explicitly selected local llama-server is unavailable."""


class ServerRequestError(ServerUnavailable):
    """A local llama-server rejected a request, with bounded diagnostics."""

    def __init__(
        self,
        *,
        status_code: int,
        endpoint: str,
        response_body: str,
        request_config: dict,
    ) -> None:
        self.status_code = int(status_code)
        self.endpoint = endpoint
        self.response_body = response_body[:4096]
        self.request_config = dict(request_config)
        self.body_truncated = len(response_body) > 4096

        message = self.response_body.strip() or "server returned an empty error response"
        error_type = None
        try:
            payload = json.loads(self.response_body)
            error = payload.get("error", payload) if isinstance(payload, dict) else payload
            if isinstance(error, dict):
                message = str(error.get("message") or error.get("detail") or message)
                error_type = error.get("type")
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

        # Keep the UI diagnostic single-line and bounded. The prompt itself is
        # deliberately never included in request_config or this exception.
        message = " ".join(message.split())[:800]
        type_suffix = f" [{error_type}]" if error_type else ""
        details = json.dumps(self.request_config, sort_keys=True, separators=(",", ":"))
        suffix = " (response body truncated)" if self.body_truncated else ""
        super().__init__(
            f"llama-server HTTP {self.status_code} at {self.endpoint}: "
            f"{message}{type_suffix}. Request: {details}{suffix}"
        )

    def as_dict(self) -> dict:
        """Return safe, bounded diagnostics suitable for logs and support reports."""
        return {
            "status_code": self.status_code,
            "endpoint": self.endpoint,
            "response_body": self.response_body,
            "body_truncated": self.body_truncated,
            "request_config": dict(self.request_config),
        }


def policy_path(data_dir: Path | None = None) -> Path:
    root = data_dir or Path(os.environ.get("CHIMERA_DATA_DIR", ".chimera"))
    root.mkdir(parents=True, exist_ok=True)
    return root / "server_policy.json"


def load_policy(data_dir: Path | None = None) -> dict:
    path = policy_path(data_dir)
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        return {
            "mode": value.get("mode", "keep_loaded"),
            "idle_timeout": max(0, int(value.get("idle_timeout", 0))),
            "startup_timeout": max(5, int(value.get("startup_timeout", 60))),
            "request_timeout": max(5, int(value.get("request_timeout", 60))),
        }
    except (OSError, ValueError, TypeError):
        return {"mode": "keep_loaded", "idle_timeout": 0, "startup_timeout": 60, "request_timeout": 60}


def save_policy(policy: dict, data_dir: Path | None = None) -> Path:
    allowed = {"keep_loaded", "idle_shutdown", "manual"}
    mode = policy.get("mode", "keep_loaded")
    if mode not in allowed:
        raise ValueError("unsupported server policy")
    value = {
        "mode": mode,
        "idle_timeout": max(0, int(policy.get("idle_timeout", 0))),
        "startup_timeout": max(5, int(policy.get("startup_timeout", 60))),
        "request_timeout": max(5, int(policy.get("request_timeout", 60))),
    }
    path = policy_path(data_dir)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")
    return path


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LlamaServerManager:
    def __init__(
        self,
        executable: str | None,
        model: Path | None,
        context=4096,
        threads=4,
        gpu_layers=0,
        timeout=60,
        port=None,
        idle_timeout=0,
    ):
        self.executable, self.model, self.context, self.threads = executable, model, context, threads
        self.gpu_layers, self.timeout, self.port = gpu_layers, timeout, port or free_port()
        self.process = None
        self.state = "stopped"
        self.started_at = None
        self.last_activity = 0
        self.idle_timeout = max(0, idle_timeout)
        self.lock = threading.RLock()
        self.request_lock = threading.Lock()
        self.last_error = None
        self.last_request_diagnostic = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def _health(self):
        try:
            with urllib.request.urlopen(self.url + "/health", timeout=1) as response:
                return response.status == 200
        except urllib.error.HTTPError as exc:
            return exc.code == 200
        except OSError:
            return False

    def start(self):
        with self.lock:
            if self.process and self.process.poll() is None and self._health():
                self.state = "ready"
                return
            if not self.executable or not Path(self.executable).is_file():
                raise ServerUnavailable("llama-server executable is unavailable")
            if not self.model or not self.model.is_file():
                raise ServerUnavailable("GGUF model is unavailable")
            self.state = "starting"
            self.last_error = None
            command = [
                self.executable, "--host", "127.0.0.1", "--port", str(self.port),
                "--model", str(self.model), "--ctx-size", str(self.context),
                "--threads", str(self.threads), "--n-gpu-layers", str(self.gpu_layers),
                "--parallel", "1", "--no-webui",
            ]
            # Never leave an unread PIPE attached to the long-lived inference process.
            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.STDOUT,
                "stdin": subprocess.DEVNULL,
                "shell": False,
                "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
            }
            self.process = subprocess.Popen(command, **kwargs)
            self.started_at = time.monotonic()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.state = "failed"
                raise ServerUnavailable(f"llama-server exited with code {self.process.returncode}")
            if self._health():
                self.state = "ready"
                self.last_activity = time.monotonic()
                return
            self.state = "model_loading"
            time.sleep(.25)
        self.state = "failed"
        self.stop()
        raise ServerUnavailable("llama-server did not become healthy before timeout")

    def status(self):
        self.reap_idle()
        if self.process and self.process.poll() is not None:
            self.state = "failed"
        elif self.process and self._health():
            self.state = "ready"
        return {
            "state": self.state,
            "url": self.url,
            "pid": self.process.pid if self.process else None,
            "error": self.last_error,
            "last_request_diagnostic": self.last_request_diagnostic,
        }

    def complete(
        self,
        prompt: str,
        max_tokens: int,
        grammar: str | None = None,
        json_schema: dict | None = None,
        cancel_event=None,
        on_delta=None,
    ):
        """Send one request to the selected local /completion endpoint.

        Exactly one structured-output contract may be supplied. JSON Schema is
        preferred for the agent protocol; grammar remains for legacy callers.
        """
        if grammar is not None and json_schema is not None:
            raise ValueError("provide either grammar or json_schema, not both")
        with self.request_lock:
            self.start()
            self.state = "busy"
            self.last_activity = time.monotonic()
            payload = {
                "prompt": prompt,
                "n_predict": int(max_tokens),
                "stream": True,
                "temperature": 0.2,
            }
            structured_mode = "none"
            if json_schema is not None:
                payload["json_schema"] = json_schema
                structured_mode = "json_schema"
            elif grammar:
                payload["grammar"] = grammar
                structured_mode = "grammar"

            endpoint = self.url + "/completion"
            request_config = {
                "endpoint": endpoint,
                "n_predict": int(max_tokens),
                "stream": True,
                "temperature": 0.2,
                "structured_output": structured_mode,
                "prompt_chars": len(prompt),
                "context": int(self.context),
                "threads": int(self.threads),
                "gpu_layers": int(self.gpu_layers),
                "schema_bytes": (
                    len(json.dumps(json_schema, separators=(",", ":")).encode("utf-8"))
                    if json_schema is not None else 0
                ),
                "grammar_bytes": len(grammar.encode("utf-8")) if grammar else 0,
            }
            request = urllib.request.Request(
                endpoint,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
                method="POST",
            )
            pieces = []
            started = time.perf_counter()
            self.last_request_diagnostic = None
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    for raw in response:
                        if cancel_event and cancel_event.is_set():
                            raise InterruptedError("local inference cancelled")
                        line = raw.decode(errors="replace").strip()
                        if not line:
                            continue
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if line == "[DONE]":
                            break
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(item, dict):
                            continue
                        text = item.get("content") or item.get("token") or item.get("response") or ""
                        if text:
                            pieces.append(text)
                            if on_delta:
                                on_delta(text)
                self.state = "ready"
                self.last_activity = time.monotonic()
                self.last_error = None
                return "".join(pieces), {
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "server_url": self.url,
                    "server_pid": self.process.pid if self.process else None,
                    "provider": "llama-server",
                    "model": str(self.model),
                    "grammar": bool(grammar),
                    "json_schema": bool(json_schema),
                }
            except urllib.error.HTTPError as exc:
                try:
                    response_body = exc.read(4097).decode("utf-8", errors="replace")
                except Exception:
                    response_body = "<unable to read server error body>"
                diagnostic = ServerRequestError(
                    status_code=exc.code,
                    endpoint=endpoint,
                    response_body=response_body,
                    request_config=request_config,
                )
                self.last_request_diagnostic = diagnostic.as_dict()
                self.last_error = str(diagnostic)
                self.state = "ready" if self.process and self._health() else "failed"
                raise diagnostic from exc
            except (TimeoutError, socket.timeout) as exc:
                self.last_error = f"llama-server request timed out after {self.timeout}s"
                self.state = "ready" if self.process and self._health() else "failed"
                raise TimeoutError(self.last_error) from exc
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.state = "ready" if self.process and self._health() else "failed"
                raise

    def reap_idle(self):
        if (
            self.idle_timeout
            and self.process
            and not self.request_lock.locked()
            and time.monotonic() - self.last_activity >= self.idle_timeout
        ):
            self.stop()
            return True
        return False

    def stop(self):
        with self.lock:
            process = self.process
            self.process = None
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            self.state = "stopped"
