"""Persistent, bounded supervision for GUI acceptance runs.

This module observes the real Chimera application; it does not implement an
agent loop and never writes Chimera task or runtime events.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    import win32api, win32con, win32gui, win32process
except ImportError:  # pragma: no cover - non-Windows environments
    win32api = win32con = win32gui = win32process = None

DEFAULT_PROMPT = (
    "Inspect this workspace. Use your registered workspace tools to list the "
    "project files and read README.md. Tell me the project name, version, "
    "directory structure and the main purpose of the application. Base your "
    "answer on the actual files you inspect. Do not modify or create any files."
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def process_snapshot() -> list[dict]:
    if os.name != "nt":
        return []
    command = "Get-CimInstance Win32_Process | Select ProcessId,ParentProcessId,CreationDate,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
    result = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=10)
    if result.returncode != 0 or not result.stdout.strip(): return []
    value = json.loads(result.stdout)
    if isinstance(value, dict): value = [value]
    return [{"pid": int(item.get("ProcessId")), "parent_pid": int(item.get("ParentProcessId") or 0), "creation_time": item.get("CreationDate"), "executable": item.get("ExecutablePath"), "command_line": item.get("CommandLine")} for item in value]


def owned_processes(processes: list[dict], launcher_pid: int) -> list[dict]:
    owned = {launcher_pid}; changed = True
    while changed:
        changed = False
        for item in processes:
            if item["parent_pid"] in owned and item["pid"] not in owned:
                owned.add(item["pid"]); changed = True
    return [item for item in processes if item["pid"] in owned]


def window_snapshot() -> list[dict]:
    if os.name != "nt" or win32gui is None: return []
    result = []
    def collect(hwnd, _):
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            result.append({"hwnd": int(hwnd), "pid": int(pid), "title": win32gui.GetWindowText(hwnd), "class_name": win32gui.GetClassName(hwnd), "visible": bool(win32gui.IsWindowVisible(hwnd)), "enabled": bool(win32gui.IsWindowEnabled(hwnd)), "rect": list(win32gui.GetWindowRect(hwnd))})
        except Exception: pass
        return True
    win32gui.EnumWindows(collect, None)
    return result


def discover_window(run: "AcceptanceRun", deadline_seconds: int = 45) -> dict | None:
    deadline = time.monotonic() + deadline_seconds
    launcher_pid = run.manifest.get("chimera_pid")
    latest = []
    while time.monotonic() < deadline:
        processes = process_snapshot(); owned = owned_processes(processes, launcher_pid) if launcher_pid else []
        windows = window_snapshot(); owned_pids = {item["pid"] for item in owned}
        candidates = [item for item in windows if item["pid"] in owned_pids and item["visible"] and item["enabled"] and item["class_name"] == "FLUTTER_RUNNER_WIN32_WINDOW"]
        latest = {"processes": owned, "windows": candidates}
        if candidates:
            if len(candidates) > 1:
                run.event("window.discovery_ambiguous", {"candidates": candidates})
                return None
            candidate = candidates[0]
            run_dir = run.run_dir / "evidence"; run_dir.mkdir(exist_ok=True)
            atomic_write(run_dir / "window-discovery.json", latest)
            run.update(window_handle=candidate["hwnd"], window_owner_pid=candidate["pid"], window_owner_process_creation_time=next((p.get("creation_time") for p in owned if p["pid"] == candidate["pid"]), None), owned_processes=owned, window_discovery_state="attached", ui_automation_state="available", last_discovery_checkpoint="window_attached")
            run.event("window.attached", {"hwnd": candidate["hwnd"], "pid": candidate["pid"]})
            return candidate
        run.event("window.discovery_retry", {"owned_pids": [p["pid"] for p in owned], "candidate_count": 0})
        time.sleep(0.5)
    atomic_write(run.run_dir / "evidence" / "window-discovery.json", latest or {"processes": [], "windows": []})
    run.update(window_discovery_state="failed", last_discovery_checkpoint="window_discovery_failed")
    run.event("window.discovery_failed", {"reason": "no owned visible enabled Flet window"})
    return None


class AcceptanceRun:
    def __init__(self, run_dir: Path, manifest: dict):
        self.run_dir = run_dir
        self.manifest_path = run_dir / "run.json"
        self.events_path = run_dir / "events.jsonl"
        self.manifest = manifest

    @classmethod
    def create(cls, root: Path, scenario: str, prompt: str, workspace: Path | None = None):
        run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        run_dir = root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "screenshots").mkdir()
        (run_dir / "evidence").mkdir()
        manifest = {
            "run_id": run_id, "scenario": scenario, "created_at": now(),
            "state": "created", "stage": "created", "stage_started_at": now(),
            "stage_deadline_seconds": 60, "last_checkpoint": None,
            "supervisor_pid": os.getpid(), "chimera_pid": None, "launcher_process_creation_time": None, "owned_processes": [],
            "chimera_creation_time": None, "window_handle": None,
            "window_owner_pid": None, "workspace": str(workspace) if workspace else None,
            "data_dir": str(run_dir / "chimera-data"), "session_id": None,
            "task_id": None, "provider_id": None, "model_id": None,
            "turn_id": None, "invocation_id": None,
            "prompt": prompt, "prompt_source": "acceptance_harness",
            "submission_count": 0, "cleanup": {"status": "pending", "closed_pids": [], "remaining_pids": []},
        }
        run = cls(run_dir, manifest); run.save(); run.event("run.created", {"prompt_source": "acceptance_harness"}); return run

    @classmethod
    def open(cls, root: Path, run_id: str):
        run_dir = root / "runs" / run_id
        path = run_dir / "run.json"
        if not path.is_file(): raise FileNotFoundError(f"acceptance run not found: {run_id}")
        return cls(run_dir, json.loads(path.read_text(encoding="utf-8")))

    def save(self): atomic_write(self.manifest_path, self.manifest)

    def update(self, **values):
        self.manifest.update(values); self.save()

    def event(self, kind: str, payload: dict | None = None):
        record = {"time": now(), "run_id": self.manifest["run_id"], "event": kind, "payload": payload or {}}
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    def checkpoint(self, stage: str, deadline_seconds: int):
        self.update(stage=stage, stage_started_at=now(), stage_deadline_seconds=deadline_seconds, last_checkpoint=stage)
        self.event("checkpoint", {"stage": stage, "deadline_seconds": deadline_seconds})


def launch(run: AcceptanceRun, workspace: Path | None):
    run_dir = run.run_dir
    stdout = (run_dir / "stdout.log").open("a", encoding="utf-8")
    stderr = (run_dir / "stderr.log").open("a", encoding="utf-8")
    environment = os.environ.copy()
    environment.update({"CHIMERA_DATA_DIR": str(run.manifest["data_dir"]), "CHIMERA_INTRO": "0"})
    if workspace: environment["CHIMERA_ACCEPTANCE_WORKSPACE"] = str(workspace)
    process = subprocess.Popen([sys.executable, "-m", "chimera"], cwd=Path(__file__).resolve().parents[1], env=environment, stdout=stdout, stderr=stderr, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    launcher = next((item for item in process_snapshot() if item["pid"] == process.pid), {})
    run.update(state="running", stage="application_startup", chimera_pid=process.pid, launcher_process_creation_time=launcher.get("creation_time"), launcher_command_line=launcher.get("command_line"))
    run.event("chimera.launched", {"pid": process.pid})
    discover_window(run)
    return process


def stop_process(run: AcceptanceRun):
    pid = run.manifest.get("chimera_pid")
    closed = []
    if pid:
        if os.name == "nt":
            result = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
            run.event("chimera.stop_requested", {"pid": pid, "returncode": result.returncode})
        closed.append(pid)
    run.update(state="stopped", stage="stopped", cleanup={"status": "complete", "closed_pids": closed, "remaining_pids": []})
    run.event("cleanup.complete", {"closed_pids": closed, "remaining_pids": []})


def report(run: AcceptanceRun) -> str:
    lines = [f"# Chimera acceptance run {run.manifest['run_id']}", "", f"State: {run.manifest['state']}", f"Stage: {run.manifest['stage']}", f"Prompt source: {run.manifest['prompt_source']}", f"Submission count: {run.manifest['submission_count']}", "", "Evidence is observational; no model decision or tool invocation is inferred from process health."]
    (run.run_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m scripts.gui_acceptance")
    parser.add_argument("command", choices=("start", "status", "report", "stop"))
    parser.add_argument("--run-id")
    parser.add_argument("--scenario", default="local-readonly-inspection")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--root", type=Path, default=Path(tempfile.gettempdir()) / "ChimeraAcceptance")
    args = parser.parse_args(argv)
    if args.command == "start":
        run = AcceptanceRun.create(args.root, args.scenario, args.prompt, args.workspace); launch(run, args.workspace); print(run.manifest["run_id"]); return 0
    if not args.run_id: parser.error("--run-id is required")
    run = AcceptanceRun.open(args.root, args.run_id)
    if args.command == "status": print(json.dumps(run.manifest, indent=2)); return 0
    if args.command == "stop": stop_process(run); print(json.dumps(run.manifest, indent=2)); return 0
    print(report(run)); return 0


if __name__ == "__main__": raise SystemExit(main())
