"""Optional Langflow companion-service boundary.

Langflow is never imported into Chimera's runtime and is never installed or
started implicitly.  Only an explicitly configured loopback service may be
used; Chimera still validates workflows and owns execution permissions.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

LANGFLOW_VERSION = "1.13.0"
ALLOWED_COMPONENT_PREFIXES = ("chimera.", "langflow.components.")
FORBIDDEN_COMPONENT_MARKERS = ("python", "code", "shell", "exec", "subprocess")

@dataclass(frozen=True)
class LangflowStatus:
    configured: bool
    available: bool
    url: str | None
    reason: str
    version: str = LANGFLOW_VERSION

def _url() -> str | None:
    value = os.environ.get("CHIMERA_LANGFLOW_URL", "").strip().rstrip("/")
    if not value: return None
    if not value.startswith("http://127.0.0.1:") and not value.startswith("http://localhost:"):
        raise ValueError("Langflow must be bound to localhost or 127.0.0.1")
    return value

class LangflowService:
    def __init__(self, url: str | None = None, command: list[str] | None = None, root: Path | None = None):
        self.root = Path(root or (Path.home() / "AppData" / "Local" / "Chimera" / "langflow"))
        self.url = (url or _url())
        self.command = command
        self.process = None
        self.editor_process = None
        self.log_path = self.root / "langflow.log"
    @property
    def environment(self) -> Path: return self.root / "venv"
    @property
    def executable(self) -> Path: return self.environment / "Scripts" / "langflow.exe"
    def installed(self) -> bool: return self.executable.is_file()
    def install(self, version: str = LANGFLOW_VERSION) -> dict:
        """Install only after an explicit user action, into Chimera's isolated directory."""
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.environment.exists():
            subprocess.run([os.fspath(os.sys.executable), "-m", "venv", os.fspath(self.environment)], check=True, timeout=120)
        pip = self.environment / "Scripts" / "pip.exe"
        command = [os.fspath(pip), "install", "--disable-pip-version-check", f"langflow=={version}"]
        with self.log_path.open("ab") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=900, check=False)
        if result.returncode: return {"status": "failed", "reason": f"Langflow installation failed with exit code {result.returncode}", "log": str(self.log_path)}
        version_result = subprocess.run([os.fspath(self.executable), "--version"], capture_output=True, text=True, timeout=20, check=False)
        return {"status": "installed" if version_result.returncode == 0 else "failed", "version": version_result.stdout.strip() or version, "log": str(self.log_path)}
    def status(self, timeout: float = 1.0) -> LangflowStatus:
        if not self.url: return LangflowStatus(False, False, None, "Langflow is not configured")
        try:
            request = urllib.request.Request(self.url, method="GET")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return LangflowStatus(True, 200 <= response.status < 500, self.url, f"HTTP {response.status}")
        except (OSError, urllib.error.URLError) as exc:
            return LangflowStatus(True, False, self.url, f"loopback service unavailable: {exc}")
    def start(self) -> LangflowStatus:
        if not self.command:
            if not self.installed(): return LangflowStatus(False, False, self.url, "Langflow is not installed; explicit setup is required")
            port = 7860
            self.url = self.url or f"http://127.0.0.1:{port}"
            self.command = [os.fspath(self.executable), "run", "--host", "127.0.0.1", "--port", str(port), "--no-open-browser"]
        if not self.url: return LangflowStatus(False, False, None, "a loopback URL is required before starting Langflow")
        if self.process and self.process.poll() is None: return self.status()
        self.root.mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("ab")
        self.process = subprocess.Popen(self.command, stdout=log, stderr=subprocess.STDOUT, shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = self.status(.5)
            if result.available: return result
            time.sleep(.25)
        return self.status()
    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=3)
            except subprocess.TimeoutExpired: self.process.kill()
        self.process = None
        self.close_editor()
    def studio_url(self) -> str | None:
        return self.url
    def open_editor(self) -> dict:
        if not self.url: return {"status": "blocked", "reason": "Langflow URL is not configured"}
        if self.editor_process and self.editor_process.poll() is None: return {"status": "completed", "pid": self.editor_process.pid, "url": self.url}
        edge = shutil.which("msedge.exe") or shutil.which("msedge")
        if not edge:
            return {"status": "blocked", "reason": "Windows Edge executable was not found for the managed companion window"}
        self.editor_process = subprocess.Popen([edge, f"--app={self.url}", "--new-window"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"status": "completed", "pid": self.editor_process.pid, "url": self.url, "presentation": "managed_edge_app_window"}
    def close_editor(self) -> None:
        if self.editor_process and self.editor_process.poll() is None:
            self.editor_process.terminate()
        self.editor_process = None

def validate_flow(flow: dict) -> dict:
    if not isinstance(flow, dict): raise ValueError("workflow must be an object")
    nodes = flow.get("nodes"); edges = flow.get("edges", [])
    if not isinstance(nodes, list) or not nodes: raise ValueError("workflow must contain nodes")
    if not isinstance(edges, list): raise ValueError("workflow edges must be an array")
    ids = set()
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not isinstance(node.get("component"), str): raise ValueError("workflow node is malformed")
        if node["id"] in ids: raise ValueError("workflow contains duplicate node IDs")
        ids.add(node["id"])
        component = node["component"].lower()
        if any(marker in component for marker in FORBIDDEN_COMPONENT_MARKERS) or not component.startswith(ALLOWED_COMPONENT_PREFIXES):
            raise ValueError(f"untrusted workflow component: {node['component']}")
    for edge in edges:
        if not isinstance(edge, dict) or edge.get("source") not in ids or edge.get("target") not in ids: raise ValueError("workflow edge references an unknown node")
    return {"version": int(flow.get("version", 1)), "nodes": nodes, "edges": edges, "metadata": flow.get("metadata", {})}

def load_flow(path: Path) -> dict:
    return validate_flow(json.loads(Path(path).read_text(encoding="utf-8")))

def save_flow(path: Path, flow: dict) -> Path:
    validated = validate_flow(flow); target = Path(path); target.parent.mkdir(parents=True, exist_ok=True); target.write_text(json.dumps(validated, indent=2), encoding="utf-8"); return target
