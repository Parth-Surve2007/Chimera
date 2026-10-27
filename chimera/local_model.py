"""Strictly local llama.cpp provider; no network or hosted fallback exists here."""
from dataclasses import dataclass
import json, os, subprocess, time
from pathlib import Path
from typing import Callable, Any
from .contracts import TaskSpec, CapabilityProposal, ProposedFile

@dataclass(frozen=True)
class LocalModelConfig:
    model_path: Path | None
    cli_path: str | None
    context: int = 2048
    max_tokens: int = 256
    timeout: float = 60
    threads: int = 4
    gpu_layers: int = 0

@dataclass(frozen=True)
class ModelStatus:
    state: str
    detail: str
    model_path: str | None = None
    cli_path: str | None = None

@dataclass(frozen=True)
class InferenceResult:
    status: str
    task: TaskSpec | None
    raw_output: str
    duration_ms: int
    generated_tokens: int
    error: str | None = None

def config_from_env(env: dict[str, str] | None = None) -> LocalModelConfig:
    env = env or os.environ
    def positive(name, default):
        try: value = int(env.get(name, default))
        except ValueError: raise ValueError(f"{name} must be a positive integer")
        if value < 1: raise ValueError(f"{name} must be a positive integer")
        return value
    try: gpu_layers = int(env.get("CHIMERA_MODEL_GPU_LAYERS", "0"))
    except ValueError: raise ValueError("CHIMERA_MODEL_GPU_LAYERS must be an integer")
    if gpu_layers < 0: raise ValueError("CHIMERA_MODEL_GPU_LAYERS cannot be negative")
    return LocalModelConfig(Path(env["CHIMERA_MODEL_PATH"]) if env.get("CHIMERA_MODEL_PATH") else None, env.get("CHIMERA_LLAMA_CLI"), positive("CHIMERA_MODEL_CONTEXT", 2048), positive("CHIMERA_MODEL_MAX_TOKENS", 256), float(env.get("CHIMERA_MODEL_TIMEOUT", "60")), positive("CHIMERA_MODEL_THREADS", 4), gpu_layers)

def check_status(config: LocalModelConfig) -> ModelStatus:
    if not config.model_path: return ModelStatus("missing", "CHIMERA_MODEL_PATH is not configured")
    if not config.model_path.is_file(): return ModelStatus("missing", "configured GGUF model file does not exist", str(config.model_path), config.cli_path)
    if config.model_path.suffix.lower() != ".gguf": return ModelStatus("incompatible", "model path is not a .gguf file", str(config.model_path), config.cli_path)
    if not config.cli_path: return ModelStatus("missing", "CHIMERA_LLAMA_CLI is not configured", str(config.model_path), None)
    if not Path(config.cli_path).is_file(): return ModelStatus("unavailable", "configured llama.cpp executable does not exist", str(config.model_path), config.cli_path)
    return ModelStatus("ready", "local model and llama.cpp executable are present", str(config.model_path), config.cli_path)

def tool_version(executable: str | None) -> str | None:
    if not executable: return None
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=5, shell=False)
        return (result.stdout or result.stderr).strip() or None
    except (OSError, subprocess.TimeoutExpired): return None

def _task(data: Any) -> TaskSpec:
    if not isinstance(data, dict) or not isinstance(data.get("outcome"), str) or not data["outcome"].strip(): raise ValueError("structured output requires a non-empty outcome")
    fields = ("inputs", "deliverables", "constraints", "resources", "permissions", "acceptance_criteria", "evidence")
    values = {f: data.get(f, {} if f in ("inputs", "resources") else []) for f in fields}
    if not isinstance(values["inputs"], dict) or not isinstance(values["resources"], dict): raise ValueError("inputs and resources must be objects")
    if any(not isinstance(values[f], list) for f in fields if f not in ("inputs", "resources")): raise ValueError("task list fields must be arrays")
    return TaskSpec(outcome=data["outcome"].strip(), **values)

class LocalModelProvider:
    def __init__(self, config: LocalModelConfig, runner: Callable[..., Any] | None = None): self.config, self.runner = config, runner
    def status(self): return check_status(self.config)
    def plan(self, request: str) -> InferenceResult:
        status = self.status()
        if status.state != "ready" and self.runner is None: return InferenceResult(status.state, None, "", 0, 0, status.detail)
        prompt = "Return only JSON matching TaskSpec fields. Do not execute anything.\nUser outcome: " + request
        started = time.perf_counter()
        try:
            if self.runner: output = self.runner(prompt, self.config)
            else:
                command = [self.config.cli_path, "-m", str(self.config.model_path), "-n", str(self.config.max_tokens), "-c", str(self.config.context), "-t", str(self.config.threads), "-ngl", str(self.config.gpu_layers), "-p", prompt]
                process = subprocess.run(command, capture_output=True, text=True, timeout=self.config.timeout, shell=False)
                if process.returncode: raise RuntimeError(f"local inference exited with code {process.returncode}")
                output = process.stdout
            data = json.loads(str(output).strip()); task = _task(data)
            return InferenceResult("ready", task, str(output), int((time.perf_counter()-started)*1000), len(str(output).split()))
        except subprocess.TimeoutExpired: return InferenceResult("timeout", None, "", int((time.perf_counter()-started)*1000), 0, "local inference timed out")
        except (json.JSONDecodeError, ValueError) as exc: return InferenceResult("malformed", None, str(output) if 'output' in locals() else "", int((time.perf_counter()-started)*1000), 0, str(exc))
        except Exception as exc: return InferenceResult("failed", None, "", int((time.perf_counter()-started)*1000), 0, str(exc))

def parse_proposal(data: Any) -> CapabilityProposal:
    required = ("candidate_id", "name", "purpose", "problem", "inputs", "outputs", "assumptions", "dependencies", "permissions", "files", "validation_plan")
    if not isinstance(data, dict) or any(k not in data for k in required): raise ValueError("proposal is missing required fields")
    if not all(isinstance(data[k], str) for k in ("candidate_id", "name", "purpose", "problem")) or not all(isinstance(data[k], dict) for k in ("inputs", "outputs")): raise ValueError("proposal scalar fields are invalid")
    if not all(isinstance(data[k], list) for k in ("assumptions", "dependencies", "permissions", "files", "validation_plan")): raise ValueError("proposal list fields are invalid")
    files, seen = [], set()
    for item in data["files"]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("content"), str): raise ValueError("proposal file is malformed")
        path = Path(item["path"])
        if path.is_absolute() or ".." in path.parts or path.as_posix() in seen: raise ValueError("proposal contains unsafe or duplicate paths")
        if len(item["content"]) > 100_000: raise ValueError("proposal file is too large")
        seen.add(path.as_posix()); files.append(ProposedFile(path.as_posix(), item["content"]))
    if len(files) > 100: raise ValueError("proposal contains too many files")
    if set(data["permissions"]) - {"filesystem.read", "filesystem.write", "process.execute"}: raise ValueError("proposal requests unsupported permissions")
    return CapabilityProposal(data["candidate_id"], data["name"], data["purpose"], data["problem"], data["inputs"], data["outputs"], tuple(data["assumptions"]), tuple(data["dependencies"]), tuple(data["permissions"]), tuple(files), tuple(data["validation_plan"]), tuple(data.get("limitations", [])))

def _provider_structured(self, prompt: str):
    status = self.status()
    if status.state != "ready" and self.runner is None: raise RuntimeError(status.detail)
    if self.runner: output = self.runner(prompt, self.config)
    else:
        command = [self.config.cli_path, "-m", str(self.config.model_path), "-n", str(self.config.max_tokens), "-c", str(self.config.context), "-t", str(self.config.threads), "-ngl", str(self.config.gpu_layers), "-p", prompt]
        process = subprocess.run(command, capture_output=True, text=True, timeout=self.config.timeout, shell=False)
        if process.returncode: raise RuntimeError(f"local inference exited with code {process.returncode}")
        output = process.stdout
    return json.loads(str(output).strip())

def propose(self, task: TaskSpec):
    return parse_proposal(_provider_structured(self, "Return only JSON for a CapabilityProposal. Do not execute anything.\nTask: " + task.outcome))

LocalModelProvider.propose = propose

