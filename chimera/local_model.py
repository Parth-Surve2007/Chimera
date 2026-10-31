"""Strictly local llama.cpp provider; no network or hosted fallback exists here."""
from dataclasses import dataclass
import json, os, subprocess, time, shutil, threading, queue
from pathlib import Path
from typing import Callable, Any
from .contracts import TaskSpec, CapabilityProposal, ProposedFile, ClarificationQuestion, ReadinessAssessment
from .server_provider import LlamaServerManager, ServerUnavailable, load_policy, save_policy

def _hidden_process_kwargs() -> dict:
    if os.name != "nt": return {}
    info = subprocess.STARTUPINFO(); info.dwFlags |= subprocess.STARTF_USESHOWWINDOW; info.wShowWindow = 0
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000), "startupinfo": info}

def _run_streaming(command, timeout, cancel_event=None, on_stdout=None):
    """Run llama-cli with concurrent pipe drains and bounded cancellation."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, shell=False, **_hidden_process_kwargs())
    chunks = {"stdout": [], "stderr": []}; events = queue.Queue()
    def drain(name, stream):
        try:
            while True:
                data = stream.read(4096)
                if not data: break
                events.put((name, data)); chunks[name].append(data)
                if name == "stdout" and on_stdout: on_stdout(data.decode(errors="replace"))
        finally: events.put((name, None))
    threads = [threading.Thread(target=drain, args=(name, getattr(process, name)), daemon=True) for name in ("stdout", "stderr")]
    for thread in threads: thread.start()
    started = time.monotonic(); cancelled = False
    try:
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True; process.terminate()
                try: process.wait(timeout=1)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=1)
                break
            if time.monotonic() - started > timeout:
                process.terminate()
                try: process.wait(timeout=1)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=1)
                raise subprocess.TimeoutExpired(command, timeout)
            try: events.get(timeout=0.05)
            except queue.Empty: pass
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=1)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=1)
        raise
    finally:
        for stream in (process.stdout, process.stderr):
            try: stream.close()
            except Exception: pass
        for thread in threads: thread.join(timeout=1)
    result = type("StreamingProcessResult", (), {})()
    result.returncode = process.returncode
    result.stdout = b"".join(chunks["stdout"]).decode(errors="replace")
    result.stderr = b"".join(chunks["stderr"]).decode(errors="replace")
    result.cancelled = cancelled
    return result

@dataclass(frozen=True)
class LocalModelConfig:
    model_path: Path | None
    cli_path: str | None
    context: int = 2048
    max_tokens: int = 256
    timeout: float = 60
    threads: int = 4
    gpu_layers: int = 0
    server_path: str | None = None

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

@dataclass(frozen=True)
class ConversationResult:
    text: str
    metrics: dict

def config_data_dir() -> Path:
    value = os.environ.get("CHIMERA_DATA_DIR")
    if value: return Path(value).expanduser().resolve()
    if getattr(__import__("sys"), "frozen", False): return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Chimera"
    return Path(".chimera").resolve()

def load_persisted_config(data_dir: Path | None = None) -> tuple[dict, str | None]:
    path = (data_dir or config_data_dir()) / "model_config.json"
    if not path.is_file(): return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict): raise ValueError("configuration must be an object")
        return data, None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {}, f"Could not read {path.name}: {exc}. Defaults were used; save a new configuration to recover."

def save_persisted_config(model_path: Path | None, cli_path: str | None, data_dir: Path | None = None) -> Path:
    root = data_dir or config_data_dir(); root.mkdir(parents=True, exist_ok=True)
    path = root / "model_config.json"
    payload = {"model_path": str(model_path.resolve()) if model_path else None, "cli_path": str(Path(cli_path).expanduser().resolve()) if cli_path else None}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path

def config_from_env(env: dict[str, str] | None = None) -> LocalModelConfig:
    env = env or os.environ
    persisted, _ = load_persisted_config()
    model_value = env.get("CHIMERA_MODEL_PATH") or persisted.get("model_path")
    cli_value = env.get("CHIMERA_LLAMA_CLI") or persisted.get("cli_path")
    if not model_value:
        candidate = Path(r"C:\ChimeraModels\Llama-3.2-3B-Instruct-Q4_K_M.gguf")
        if candidate.is_file(): model_value = str(candidate)
    if not cli_value:
        found = shutil.which("llama-cli.exe") or shutil.which("llama-cli")
        if found: cli_value = found
        else:
            roots = list(Path(os.environ.get("LOCALAPPDATA", "" )).glob(r"Microsoft\WinGet\Packages\*llamacpp*\**\llama-cli.exe")) if os.environ.get("LOCALAPPDATA") else []
            if roots: cli_value = str(roots[0])
    def positive(name, default):
        try: value = int(env.get(name, default))
        except ValueError: raise ValueError(f"{name} must be a positive integer")
        if value < 1: raise ValueError(f"{name} must be a positive integer")
        return value
    try: gpu_layers = int(env.get("CHIMERA_MODEL_GPU_LAYERS", "0"))
    except ValueError: raise ValueError("CHIMERA_MODEL_GPU_LAYERS must be an integer")
    if gpu_layers < 0: raise ValueError("CHIMERA_MODEL_GPU_LAYERS cannot be negative")
    server_value = env.get("CHIMERA_LLAMA_SERVER")
    if not server_value and os.environ.get("LOCALAPPDATA"):
        matches = list(Path(os.environ["LOCALAPPDATA"]).glob(r"Microsoft\WinGet\Packages\*llamacpp*\**\llama-server.exe"))
        if matches: server_value = str(matches[0])
    return LocalModelConfig(Path(model_value).expanduser().resolve() if model_value else None, str(Path(cli_value).expanduser().resolve()) if cli_value else None, positive("CHIMERA_MODEL_CONTEXT", 2048), positive("CHIMERA_MODEL_MAX_TOKENS", 256), float(env.get("CHIMERA_MODEL_TIMEOUT", "60")), positive("CHIMERA_MODEL_THREADS", 4), gpu_layers, server_value)

def check_status(config: LocalModelConfig) -> ModelStatus:
    if not config.model_path: return ModelStatus("missing", "CHIMERA_MODEL_PATH is not configured")
    if not config.model_path.is_file(): return ModelStatus("missing", "configured GGUF model file does not exist", str(config.model_path), config.cli_path)
    if config.model_path.suffix.lower() != ".gguf": return ModelStatus("incompatible", "model path is not a .gguf file", str(config.model_path), config.cli_path)
    if not config.cli_path: return ModelStatus("missing", "CHIMERA_LLAMA_CLI is not configured", str(config.model_path), None)
    if not Path(config.cli_path).is_file(): return ModelStatus("unavailable", "configured llama.cpp executable does not exist", str(config.model_path), config.cli_path)
    return ModelStatus("ready", "local model and llama.cpp executable are present", str(config.model_path), config.cli_path)

def config_source(env: dict[str, str] | None = None, data_dir: Path | None = None) -> dict[str, str]:
    env = env or os.environ; persisted, _ = load_persisted_config(data_dir)
    result = {}
    result["model"] = "environment" if env.get("CHIMERA_MODEL_PATH") else "persisted" if persisted.get("model_path") else "discovered" if Path(r"C:\ChimeraModels\Llama-3.2-3B-Instruct-Q4_K_M.gguf").is_file() else "default"
    result["runtime"] = "environment" if env.get("CHIMERA_LLAMA_CLI") else "persisted" if persisted.get("cli_path") else "discovered"
    return result

def tool_version(executable: str | None) -> str | None:
    if not executable: return None
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=5, shell=False, **_hidden_process_kwargs())
        return (result.stdout or result.stderr).strip() or None
    except (OSError, subprocess.TimeoutExpired): return None

def _task(data: Any) -> TaskSpec:
    if not isinstance(data, dict) or not isinstance(data.get("outcome"), str) or not data["outcome"].strip(): raise ValueError("structured output requires a non-empty outcome")
    fields = ("inputs", "deliverables", "constraints", "resources", "permissions", "acceptance_criteria", "evidence")
    values = {f: data.get(f, {} if f in ("inputs", "resources") else []) for f in fields}
    if not isinstance(values["inputs"], dict) or not isinstance(values["resources"], dict): raise ValueError("inputs and resources must be objects")
    if any(not isinstance(values[f], list) for f in fields if f not in ("inputs", "resources")): raise ValueError("task list fields must be arrays")
    return TaskSpec(outcome=data["outcome"].strip(), **values)

def assess_readiness(task: TaskSpec) -> ReadinessAssessment:
    """Conservative pre-proposal check; it does not infer or execute anything."""
    questions = []
    if not task.acceptance_criteria:
        questions.append(ClarificationQuestion("acceptance", "What observable result should count as success?", "The proposal needs a reviewable acceptance criterion.", "acceptance_criteria"))
    if not task.deliverables:
        questions.append(ClarificationQuestion("deliverable", "What concrete artifact or output should be produced?", "The proposal cannot define its deliverable from the task alone.", "deliverables"))
    if questions:
        return ReadinessAssessment("needs_clarification", tuple(questions), "proposal prerequisites are underspecified")
    return ReadinessAssessment("ready", reason="task contains proposal-relevant acceptance and deliverable information")

def _json_output(raw: str) -> Any:
    text = raw.strip()
    try: return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for start, char in enumerate(text):
            if char not in "[{": continue
            try:
                value, _ = decoder.raw_decode(text[start:])
                if isinstance(value, (dict, list)): return value
            except json.JSONDecodeError:
                continue
        raise ValueError("no complete JSON object or array found in model completion")

def _json_completion(raw: str) -> tuple[Any, str]:
    """Extract one complete JSON value, excluding llama startup/telemetry text."""
    text = raw.strip()
    decoder = json.JSONDecoder()
    candidates = []
    for start, char in enumerate(text):
        if char not in "[{": continue
        try:
            value, end = decoder.raw_decode(text[start:])
            if isinstance(value, (dict, list)): candidates.append((value, text[start:start + end]))
        except json.JSONDecodeError:
            continue
    if candidates: return max(candidates, key=lambda item: len(item[1]))
    raise ValueError("no complete JSON completion found")

def _plain_completion(raw: str) -> str:
    """Remove llama-cli's interactive banner/prompt/telemetry from text output."""
    text = str(raw).replace("\r\n", "\n")
    if "ASSISTANT:" in text:
        text = text.rsplit("ASSISTANT:", 1)[1]
    if "\n> " in text:
        text = text.split("\n> ", 1)[1]
        if "\n" in text:
            text = text.split("\n", 1)[1]
    for marker in ("\n[ Prompt:", "\nExiting..."):
        if marker in text: text = text.split(marker, 1)[0]
    return text.strip()

AGENT_DECISION_GRAMMAR = r'''root ::= object
object ::= "{" ws (pair ("," ws pair)*)? "}" ws
pair ::= string ws ":" ws value
value ::= object | array | string | number | "true" | "false" | "null"
array ::= "[" ws (value ("," ws value)*)? "]" ws
string ::= "\"" ([^"\\] | "\\" escape)* "\""
escape ::= "\"" | "\\" | "/" | "b" | "f" | "n" | "r" | "t"
number ::= "-"? ([0-9] | [1-9] [0-9]+) ("." [0-9]+)?
ws ::= [ \t\n\r]*'''

class LocalModelProvider:
    def __init__(self, config: LocalModelConfig, runner: Callable[..., Any] | None = None):
        self.config, self.runner, self.last_inference_metrics = config, runner, {}
        policy = load_policy()
        self.server = None if runner else LlamaServerManager(config.server_path, config.model_path, config.context, config.threads, config.gpu_layers, policy["request_timeout"], idle_timeout=policy["idle_timeout"] if policy["mode"] == "idle_shutdown" else 0)
    def stop(self):
        if self.server: self.server.stop()
    def status(self):
        base = check_status(self.config)
        if base.state != "ready" or self.runner or not self.server: return base
        info = self.server.status()
        return ModelStatus(info["state"] if info["state"] != "stopped" else "ready", f"local model configured; server {info['state']}", base.model_path, self.config.server_path or base.cli_path)

    def converse(self, messages, session=None, cancel_event=None, on_delta=None) -> ConversationResult:
        """Generate ordinary conversational text; this path does not invent tool calls."""
        status = self.status()
        if status.state != "ready" and self.runner is None: raise RuntimeError(status.detail)
        history = list(messages)[-12:]
        transcript = "\n".join(f"{getattr(item, 'role', 'user').upper()}: {getattr(item, 'content', '')[:4000]}" for item in history)
        prompt = "You are Chimera, a local-first desktop assistant. Answer the user's request clearly and honestly. Do not claim to have inspected files, used tools, or completed actions unless the runtime provides those results. If the user is only greeting you, respond naturally.\n\nConversation:\n" + transcript + "\nASSISTANT:"
        started = time.perf_counter()
        if self.runner:
            output = self.runner(prompt, self.config)
        elif self.server:
            try:
                output, metrics = self.server.complete(prompt, self.config.max_tokens, cancel_event=cancel_event, on_delta=on_delta)
                metrics.update({"reasoning_source": "local_llama_server", "model": str(self.config.model_path)})
                self.last_inference_metrics = metrics
                return ConversationResult(output, metrics)
            except ServerUnavailable as exc:
                self.last_inference_metrics = {"provider": "llama-cli-fallback", "server_error": str(exc)}
                self.server.stop()
                self.server = None
                command = [self.config.cli_path, "-m", str(self.config.model_path), "-n", str(self.config.max_tokens), "-c", str(self.config.context), "-t", str(self.config.threads), "-ngl", str(self.config.gpu_layers), "-st", "--no-display-prompt", "-p", prompt]
                process = _run_streaming(command, self.config.timeout, cancel_event)
                if process.cancelled: raise InterruptedError("local inference cancelled")
                if process.returncode: raise RuntimeError(f"local inference exited with code {process.returncode}")
                output = process.stdout
        else:
            command = [self.config.cli_path, "-m", str(self.config.model_path), "-n", str(self.config.max_tokens), "-c", str(self.config.context), "-t", str(self.config.threads), "-ngl", str(self.config.gpu_layers), "-st", "--no-display-prompt", "-p", prompt]
            raw_stream = []; emitted = ""
            def handle_delta(chunk):
                nonlocal emitted
                raw_stream.append(chunk)
                clean = _plain_completion("".join(raw_stream))
                if on_delta and clean.startswith(emitted) and clean != emitted:
                    on_delta(clean[len(emitted):]); emitted = clean
            process = _run_streaming(command, self.config.timeout, cancel_event, handle_delta)
            if process.cancelled: raise InterruptedError("local inference cancelled")
            if process.returncode: raise RuntimeError(f"local inference exited with code {process.returncode}")
            output = process.stdout
        text = _plain_completion(output)
        if not text: raise ValueError("local model returned an empty conversational response")
        return ConversationResult(text, {"duration_ms": int((time.perf_counter() - started) * 1000), "generated_tokens_estimate": len(text.split()), "model": str(self.config.model_path), "runtime": str(self.config.cli_path), "reasoning_source": "local_llama_cpp"})

    def decide(self, messages, catalog, session=None, cancel_event=None, on_delta=None):
        """Return one strictly structured next action for the agent runtime."""
        transcript = "\n".join(f"{getattr(item, 'role', 'user').upper()}: {getattr(item, 'content', '')[:3000]}" for item in list(messages)[-10:])
        prompt = ("Return exactly one JSON object. For a normal answer use {\"type\":\"message\",\"content\":\"...\"}. "
                  "For workspace inspection use {\"type\":\"tool_call\",\"tool\":\"workspace.list_files\",\"arguments\":{\"relative\":\".\",\"recursive\":true}}. "
                  "Choose only a tool listed below. For every tool call, copy every required argument exactly from the tool contract and never omit required arguments. "
                  "For computer tools, session_id and reference_id must be copied from computer_observation; do not invent or omit them. "
                  "Do not include markdown, explanations, version fields, or invocation IDs. "
                  "After a tool result, use its actual contents before answering.\nTOOLS: " + json.dumps(catalog, separators=(",", ":")) + "\nCONVERSATION:\n" + transcript)
        parsed, metrics = self.structured_with_metrics(prompt, grammar=AGENT_DECISION_GRAMMAR, cancel_event=cancel_event, on_delta=on_delta)
        metrics["grammar_provenance"] = "chimera.agent_decision.v1"
        if not isinstance(parsed, dict): raise ValueError("agent decision must be an object")
        parsed["_metrics"] = metrics
        return parsed
    def plan(self, request: str) -> InferenceResult:
        status = self.status()
        if status.state != "ready" and self.runner is None: return InferenceResult(status.state, None, "", 0, 0, status.detail)
        prompt = "Return ONLY one complete JSON object, with no markdown or explanation. Required keys: outcome (string), inputs (object), deliverables (array), constraints (array), resources (object), permissions (array), acceptance_criteria (array), evidence (array). Do not execute anything.\nUser outcome: " + request
        started = time.perf_counter()
        try:
            if self.runner: output = self.runner(prompt, self.config)
            else:
                command = [self.config.cli_path, "-m", str(self.config.model_path), "-n", str(self.config.max_tokens), "-c", str(self.config.context), "-t", str(self.config.threads), "-ngl", str(self.config.gpu_layers), "-st", "-p", prompt]
                process = subprocess.run(command, capture_output=True, text=True, timeout=self.config.timeout, shell=False, **_hidden_process_kwargs())
                if process.returncode: raise RuntimeError(f"local inference exited with code {process.returncode}")
                output = process.stdout
            data = _json_output(str(output)); task = _task(data)
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
        if path.is_absolute() or not path.parts or path in (Path("."), Path("")) or ".." in path.parts or path.name == ".chimera-candidate.json" or path.as_posix() in seen: raise ValueError("proposal contains unsafe, reserved, or duplicate paths")
        if len(item["content"]) > 100_000: raise ValueError("proposal file is too large")
        seen.add(path.as_posix()); files.append(ProposedFile(path.as_posix(), item["content"]))
    if len(files) > 100: raise ValueError("proposal contains too many files")
    if set(data["permissions"]) - {"filesystem.read", "filesystem.write", "process.execute"}: raise ValueError("proposal requests unsupported permissions")
    coverage = data.get("requirement_coverage", [])
    if not isinstance(coverage, list) or any(not isinstance(item, dict) for item in coverage): raise ValueError("requirement coverage is malformed")
    return CapabilityProposal(data["candidate_id"], data["name"], data["purpose"], data["problem"], data["inputs"], data["outputs"], tuple(data["assumptions"]), tuple(data["dependencies"]), tuple(data["permissions"]), tuple(files), tuple(data["validation_plan"]), tuple(data.get("limitations", [])), tuple(coverage))

def _provider_structured(self, prompt: str):
    status = self.status()
    if status.state != "ready" and self.runner is None: raise RuntimeError(status.detail)
    if self.runner: output = self.runner(prompt, self.config)
    else:
        command = [self.config.cli_path, "-m", str(self.config.model_path), "-n", str(self.config.max_tokens), "-c", str(self.config.context), "-t", str(self.config.threads), "-ngl", str(self.config.gpu_layers), "-st", "-p", prompt]
        process = subprocess.run(command, capture_output=True, text=True, timeout=self.config.timeout, shell=False, **_hidden_process_kwargs())
        if process.returncode: raise RuntimeError(f"local inference exited with code {process.returncode}")
        output = process.stdout
    return _json_output(str(output))

def structured_with_metrics(self, prompt: str, json_schema: dict | None = None, grammar: str | None = None, cancel_event=None, on_delta=None):
    """Run one bounded structured request and retain honest runtime metrics."""
    status = self.status()
    if status.state != "ready" and self.runner is None: raise RuntimeError(status.detail)
    started = time.perf_counter(); output = ""; stderr = ""; generated = 0
    try:
        if self.runner:
            output = str(self.runner(prompt, self.config)); stderr = ""
        elif self.server and json_schema is None:
            try:
                output, metrics = self.server.complete(prompt, self.config.max_tokens, grammar=grammar, cancel_event=cancel_event, on_delta=on_delta)
                metrics.update({"prompt_tokens_estimate": len(prompt.split()), "generated_tokens_estimate": len(output.split()), "reasoning_source": "local_llama_server"})
                self.last_inference_metrics = metrics
                parsed, completion = _json_completion(output)
                metrics["raw_completion"] = completion
                return parsed, metrics
            except ServerUnavailable as exc:
                self.last_inference_metrics = {"provider": "llama-cli-fallback", "server_error": str(exc)}
                self.server.stop()
                self.server = None
                command = [self.config.cli_path, "-m", str(self.config.model_path), "-n", str(self.config.max_tokens), "-c", str(self.config.context), "-t", str(self.config.threads), "-ngl", str(self.config.gpu_layers), "-st", "--no-display-prompt"]
                if grammar is not None: command.extend(["--grammar", grammar])
                command.extend(["-p", prompt])
                process = _run_streaming(command, self.config.timeout, cancel_event)
                if process.cancelled: raise InterruptedError("local inference cancelled")
                if process.returncode: raise RuntimeError(f"local inference exited with code {process.returncode}")
                output = process.stdout; stderr = process.stderr or ""
        else:
            command = [self.config.cli_path, "-m", str(self.config.model_path), "-n", str(self.config.max_tokens), "-c", str(self.config.context), "-t", str(self.config.threads), "-ngl", str(self.config.gpu_layers), "-st", "--no-display-prompt"]
            if json_schema is not None:
                command.extend(["--json-schema", json.dumps(json_schema, separators=(",", ":"))])
            if grammar is not None:
                command.extend(["--grammar", grammar])
            command.extend(["-p", prompt])
            process = _run_streaming(command, self.config.timeout, cancel_event, on_delta)
            if process.cancelled: raise InterruptedError("local inference cancelled")
            stderr = process.stderr or ""
            if process.returncode: raise RuntimeError(f"local inference exited with code {process.returncode}")
            output = process.stdout
            if "Error:" in (output + "\n" + stderr):
                raise RuntimeError((output + "\n" + stderr).split("Error:", 1)[1].splitlines()[0].strip())
        generated = len(output.split())
        elapsed = int((time.perf_counter() - started) * 1000)
        import re
        telemetry = output + "\n" + stderr
        generation = re.search(r"Generation:\s*([0-9.]+)\s*t/s", telemetry)
        prompt_rate = re.search(r"Prompt:\s*([0-9.]+)\s*t/s", telemetry)
        metrics = {"duration_ms": elapsed, "prompt_tokens_estimate": len(prompt.split()), "generated_tokens_estimate": generated, "tokens_per_second": float(generation.group(1)) if generation else (generated / max(0.001, elapsed / 1000)), "prompt_tokens_per_second": float(prompt_rate.group(1)) if prompt_rate else None, "model_load_ms": None, "prompt_eval_ms": None, "generation_ms": None, "context": self.config.context, "max_tokens": self.config.max_tokens, "threads": self.config.threads, "gpu_layers": self.config.gpu_layers, "constrained_decoding": bool(json_schema or grammar), "model": str(self.config.model_path), "runtime": str(self.config.cli_path)}
        self.last_inference_metrics = metrics
        parsed, completion = _json_completion(output)
        metrics["raw_completion"] = completion
        metrics["runtime_diagnostics"] = stderr + "\n" + output[:output.find(completion)] if completion in output else stderr
        metrics["completion_tokens_estimate"] = len(completion.split())
        self.last_inference_metrics = metrics
        return parsed, metrics
    except (json.JSONDecodeError, SyntaxError, ValueError) as exc:
        preview = output.strip()[:500] if output else "<empty>"
        raise ValueError(f"structured model output was invalid: {exc}; preview={preview}") from exc
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"local inference timed out after {self.config.timeout}s") from exc

def propose(self, task: TaskSpec):
    prompt = "Return ONLY one complete JSON object, no markdown or explanation. Required CapabilityProposal keys: candidate_id, name, purpose, problem (strings); inputs and outputs (objects); assumptions, dependencies, permissions, files, validation_plan (arrays); optional requirement_coverage (array of objects with requirement_id, criterion_id, coverage, and unresolved). Each files item must contain path and content strings. Use permissions only as an empty array unless strictly required. Do not execute anything. Task: " + task.outcome
    return parse_proposal(_provider_structured(self, prompt))

LocalModelProvider.propose = propose
LocalModelProvider.structured_with_metrics = structured_with_metrics

