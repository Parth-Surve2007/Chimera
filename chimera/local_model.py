"""Strictly local llama.cpp provider; no network or hosted fallback exists here."""
from dataclasses import dataclass
import json, os, re, subprocess, time, shutil, threading, queue
from difflib import SequenceMatcher
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
    context: int = 4096
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

def save_persisted_config(
    model_path: Path | None,
    cli_path: str | None,
    data_dir: Path | None = None,
    *,
    server_path: str | None = None,
    context: int | None = None,
    max_tokens: int | None = None,
    threads: int | None = None,
    gpu_layers: int | None = None,
) -> Path:
    """Persist an explicit runtime choice and optional inference tuning."""
    root = data_dir or config_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "model_config.json"
    payload = {
        "model_path": str(model_path.expanduser().resolve()) if model_path else None,
        "cli_path": str(Path(cli_path).expanduser().resolve()) if cli_path else None,
        "server_path": str(Path(server_path).expanduser().resolve()) if server_path else None,
    }
    for key, value in {
        "context": context,
        "max_tokens": max_tokens,
        "threads": threads,
        "gpu_layers": gpu_layers,
    }.items():
        if value is not None:
            payload[key] = int(value)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path

def config_from_env(env: dict[str, str] | None = None) -> LocalModelConfig:
    env = env or os.environ
    persisted, _ = load_persisted_config()
    model_value = env.get("CHIMERA_MODEL_PATH") or persisted.get("model_path")
    cli_value = env.get("CHIMERA_LLAMA_CLI") or persisted.get("cli_path")
    server_value = env.get("CHIMERA_LLAMA_SERVER") or persisted.get("server_path")
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
    try: gpu_layers = int(env.get("CHIMERA_MODEL_GPU_LAYERS", persisted.get("gpu_layers", 0)))
    except (TypeError, ValueError): raise ValueError("CHIMERA_MODEL_GPU_LAYERS must be an integer")
    if gpu_layers < 0: raise ValueError("CHIMERA_MODEL_GPU_LAYERS cannot be negative")
    if not server_value and os.environ.get("LOCALAPPDATA"):
        matches = list(Path(os.environ["LOCALAPPDATA"]).glob(r"Microsoft\WinGet\Packages\*llamacpp*\**\llama-server.exe"))
        if matches: server_value = str(matches[0])
    return LocalModelConfig(
        Path(model_value).expanduser().resolve() if model_value else None,
        str(Path(cli_value).expanduser().resolve()) if cli_value else None,
        positive("CHIMERA_MODEL_CONTEXT", persisted.get("context", 4096)),
        positive("CHIMERA_MODEL_MAX_TOKENS", persisted.get("max_tokens", 256)),
        float(env.get("CHIMERA_MODEL_TIMEOUT", "120")),
        positive("CHIMERA_MODEL_THREADS", persisted.get("threads", 4)),
        gpu_layers,
        str(Path(server_value).expanduser().resolve()) if server_value else None,
    )

def check_status(config: LocalModelConfig) -> ModelStatus:
    if not config.model_path:
        return ModelStatus("missing", "A local GGUF model path is not configured")
    if not config.model_path.is_file():
        return ModelStatus("missing", "configured GGUF model file does not exist", str(config.model_path), config.server_path or config.cli_path)
    if config.model_path.suffix.lower() != ".gguf":
        return ModelStatus("incompatible", "model path is not a .gguf file", str(config.model_path), config.server_path or config.cli_path)

    # Runtime selection is explicit. A configured server must work as a server;
    # Chimera never silently swaps it for CLI or a hosted provider.
    if config.server_path:
        if not Path(config.server_path).is_file():
            return ModelStatus("missing", "configured llama-server executable does not exist; no alternate runtime will be selected", str(config.model_path), config.server_path)
        return ModelStatus("ready", "GGUF and the explicitly selected llama-server are present; model loading is verified at startup", str(config.model_path), config.server_path)

    if config.cli_path and Path(config.cli_path).is_file():
        return ModelStatus("ready", "GGUF and explicitly selected llama-cli are present; this mode loads the model per request", str(config.model_path), config.cli_path)
    return ModelStatus("missing", "Configure a llama-server for a persistent loaded model, or explicitly select llama-cli mode", str(config.model_path), None)

def config_source(env: dict[str, str] | None = None, data_dir: Path | None = None) -> dict[str, str]:
    env = env or os.environ
    persisted, _ = load_persisted_config(data_dir)
    result = {}
    result["model"] = "environment" if env.get("CHIMERA_MODEL_PATH") else "persisted" if persisted.get("model_path") else "discovered" if Path(r"C:\ChimeraModels\Llama-3.2-3B-Instruct-Q4_K_M.gguf").is_file() else "unconfigured"
    result["runtime"] = (
        "environment server" if env.get("CHIMERA_LLAMA_SERVER")
        else "persisted server" if persisted.get("server_path")
        else "discovered server" if env.get("LOCALAPPDATA") and list(Path(env["LOCALAPPDATA"]).glob(r"Microsoft\WinGet\Packages\*llamacpp*\**\llama-server.exe"))
        else "environment CLI" if env.get("CHIMERA_LLAMA_CLI")
        else "persisted CLI" if persisted.get("cli_path")
        else "discovered CLI"
    )
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

def _normalized_agent_text(value: str) -> str:
    """Normalize a short text response for exact/near-exact prompt comparison."""
    return " ".join(re.sub(r"[^\w\s]", " ", str(value).casefold()).split())


def _is_unhelpful_prompt_echo(response: str, messages) -> bool:
    """Reject a model message that only repeats the latest user request."""
    latest_user = next(
        (item for item in reversed(list(messages))
         if str(getattr(item, "role", "user")).casefold() == "user"),
        None,
    )
    if latest_user is None:
        return False
    request = str(getattr(latest_user, "content", "")).strip()
    answer = str(response or "").strip()
    request_norm = _normalized_agent_text(request)
    answer_norm = _normalized_agent_text(answer)
    if len(request_norm) < 24 or not answer_norm:
        return False
    if re.match(r"^(?:repeat|quote|echo|copy|transcribe)\b", request.casefold()):
        return False
    if request_norm == answer_norm:
        return True
    size_ratio = min(len(request_norm), len(answer_norm)) / max(len(request_norm), len(answer_norm))
    return size_ratio >= 0.90 and SequenceMatcher(None, request_norm, answer_norm).ratio() >= 0.965


AGENT_DECISION_SCHEMA = {
    "type": "object",
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "type": {"enum": ["message"]},
                "content": {"type": "string"},
            },
            "required": ["type", "content"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "type": {"enum": ["tool_call"]},
                "tool": {"type": "string"},
                "arguments": {"type": "object", "additionalProperties": True},
                "invocation_id": {"type": "string"},
            },
            "required": ["type", "tool", "arguments"],
            "additionalProperties": False,
        },
    ],
}

class LocalModelProvider:
    def __init__(self, config: LocalModelConfig, runner: Callable[..., Any] | None = None):
        self.config, self.runner, self.last_inference_metrics = config, runner, {}
        policy = load_policy()
        self.server = (
            LlamaServerManager(
                config.server_path, config.model_path, config.context, config.threads,
                config.gpu_layers, policy["request_timeout"],
                idle_timeout=policy["idle_timeout"] if policy["mode"] == "idle_shutdown" else 0,
            )
            if not runner and config.server_path else None
        )
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
                output, metrics = self.server.complete(
                    prompt, self.config.max_tokens, cancel_event=cancel_event, on_delta=on_delta
                )
            except ServerUnavailable as exc:
                self.last_inference_metrics = {"provider": "llama-server", "status": "failed", "error": str(exc)}
                raise RuntimeError(
                    f"the selected llama-server failed; no CLI or hosted fallback was attempted: {exc}"
                ) from exc
            metrics.update({"reasoning_source": "local_llama_server", "model": str(self.config.model_path)})
            self.last_inference_metrics = metrics
            return ConversationResult(output, metrics)
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

    def verify_agent_protocol(self) -> dict:
        """Run a real, local, structured model request using the same decision protocol as chat."""
        from types import SimpleNamespace
        status = self.status()
        if status.state != "ready":
            raise RuntimeError(status.detail)
        started = time.perf_counter()
        probe = SimpleNamespace(
            role="user",
            content="For this runtime self-test, reply with one short sentence confirming that the local agent protocol is working.",
        )
        decision = self.decide(
            [probe],
            {"version": 1, "tools": [], "authorized_applications": [], "interaction_inventory": []},
        )
        if not isinstance(decision, dict) or decision.get("type") != "message":
            raise RuntimeError("local model did not return a valid agent message decision")
        answer = decision.get("content")
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError("local model returned an empty agent message")
        metrics = decision.get("_metrics", {})
        return {
            "state": "verified",
            "model": str(self.config.model_path),
            "runtime": str(self.config.server_path or self.config.cli_path),
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "generated_tokens_estimate": metrics.get("generated_tokens_estimate"),
            "response_preview": answer.strip()[:180],
            "reasoning_source": metrics.get("reasoning_source", "local_llama_cpp"),
        }


    def decide(self, messages, catalog, session=None, cancel_event=None, on_delta=None):
        """Return one compact JSON-Schema-constrained decision for AgentRuntime."""
        def compact_message(item):
            role = str(getattr(item, "role", "user")).lower()
            content = str(getattr(item, "content", ""))
            if role == "tool":
                try:
                    payload = json.loads(content)
                except (TypeError, json.JSONDecodeError):
                    payload = None
                if isinstance(payload, dict) and payload.get("tool") == "computer.list_applications":
                    result = payload.get("result") or []
                    # Runtime computer tool messages wrap results in {"items": ...}.
                    if isinstance(result, dict):
                        result = result.get("items", [])
                    apps = result if isinstance(result, list) else []
                    lines = []
                    for app in apps[:10]:
                        if not isinstance(app, dict):
                            continue
                        name = Path(str(app.get("executable") or "Application")).name
                        titles = [
                            str(window.get("title") or "").strip()
                            for window in app.get("windows", [])[:2]
                            if isinstance(window, dict)
                        ]
                        lines.append(f"- {name}: {', '.join(value for value in titles if value) or 'visible window'}")
                    content = "Desktop inventory summary:\n" + (
                        "\n".join(lines) if lines else "No user-facing applications found."
                    )
                else:
                    content = content[:700]
            elif role == "user":
                content = content[:1500]
            elif role == "system":
                # Keep the ephemeral operator-goal context intact enough to
                # preserve its success criteria; do not clip it like telemetry.
                content = content[:1800]
            else:
                content = content[:650]
            return f"{role.upper()}: {content}"

        recent = list(messages)[-6:]
        transcript = "\n".join(compact_message(item) for item in recent)
        workspace_root = (session or {}).get("workspace")
        known_folders = {}
        if workspace_root:
            root_path = Path(workspace_root).expanduser()
            desktop_candidates = [Path.home() / "Desktop"]
            one_drive = os.environ.get("OneDrive")
            if one_drive:
                desktop_candidates.append(Path(one_drive) / "Desktop")
            for desktop in desktop_candidates:
                try:
                    if desktop.is_dir():
                        known_folders["desktop"] = desktop.resolve().relative_to(root_path.resolve()).as_posix()
                        break
                except (OSError, ValueError):
                    continue

        session_state = {
            "mode": (session or {}).get("mode", "plan"),
            "workspace_attached": bool(workspace_root),
        }
        workspace_state = {
            "root": str(workspace_root) if workspace_root else None,
            "known_folders": known_folders,
        }

        tool_priority = {
            "agent.todo_write": 0,
            "agent.todo_read": 1,
            "computer.list_applications": 2,
            "computer.launch": 3,
            "workspace.list_files": 4,
            "workspace.search_files": 5,
            "workspace.read_text": 6,
            "workspace.create_document": 7,
            "workspace.create_file": 8,
            "workspace.inspect_file": 9,
            "workspace.search_text": 10,
        }
        available_tools = [
            (index, item)
            for index, item in enumerate((catalog or {}).get("tools", []))
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        available_tools.sort(
            key=lambda pair: (tool_priority.get(pair[1]["id"], 50), pair[0])
        )
        compact_tools = []
        for _, item in available_tools[:36]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            compact = {
                "id": item["id"],
                "description": str(item.get("description") or "")[:150],
                "arguments": item.get("arguments", {}),
                "permission": item.get("permission"),
            }
            if item.get("risk_level") is not None:
                compact["risk_level"] = item["risk_level"]
            if item.get("requires_approval") is not None:
                compact["requires_approval"] = bool(item["requires_approval"])
            compact_tools.append(compact)

        authorized = [
            {
                "session_id": item.get("session_id"),
                "pid": item.get("pid"),
                "application": Path(str(item.get("executable") or "application")).name,
            }
            for item in (catalog or {}).get("authorized_applications", [])[:6]
            if isinstance(item, dict)
        ]
        compact_catalog = {"tools": compact_tools}

        def render_prompt():
            return (
                "You are Chimera's local agent decision engine. Return exactly one JSON object: "
                '{"type":"message","content":"..."} OR '
                '{"type":"tool_call","tool":"<exact tool id>","arguments":{...},"invocation_id":"optional"}. '
                "Use only exact tool IDs and valid arguments from TOOLS. Never invent IDs or results. "
                "Never echo, copy, or merely restate the latest USER message as the answer. "
                "A message decision must provide useful information or ask one necessary clarification. "
                "When the user asks for code or an explanation in chat, answer directly as a message containing the code; "
                "do not invent a python, code, shell, or execution tool. Only call an external action tool when the user explicitly asks "
                "you to create/edit a file, run something, or interact with an application. You may use agent.todo_write and agent.todo_read "
                "to maintain progress metadata for a multi-step request; these internal tools do not execute external actions or grant permissions. "
                "Treat the latest USER message as the task; earlier messages are context. "
                "For multiple meaningful steps, first call agent.todo_write with a concise checklist of 2-8 outcome steps. "
                "Set exactly one next item to in_progress and the rest to pending. Do not create a checklist for greetings, simple answers, or one-step requests. "
                "After real tool results or verified milestones, update the checklist. Use agent.todo_read when resuming or when progress is unclear. "
                "Mark an item completed only when actual tool evidence supports the outcome; mark it blocked when a real prerequisite is missing. "
                "A checklist is progress metadata, never permission to execute an action or proof that work succeeded. "
                "Plan mode is read-only for protected operations; ask to switch to Build Mode first. "
                "The active local filesystem scope is SESSION.workspace_root; on Windows Chimera defaults to the whole system drive. "
                "Without an active workspace scope, do not call workspace tools; use WORKSPACE_SCOPE.root as the current file boundary. "
                "Use only workspace-relative paths. Use WORKSPACE_SCOPE.known_folders.desktop for Desktop requests when present; "
                "never invent Linux paths on Windows or pass an absolute host path to a workspace file tool. "
                "Choose the file tool from the requested format: use workspace.create_document for a real Microsoft Word .docx file, "
                "with path, title, and content; use workspace.create_file for a plain .txt or source/text file. "
                "Never silently downgrade a requested .docx to .txt or describe a text file as a Word document. "
                "For a requested .docx, the path must end in .docx. Wait for exact Build Mode write approval. "
                "Do not overwrite existing files. After approval, report success only when the tool result is completed and the file hash/size verification passes. "
                "Use desktop inspection/actions only for an application already listed in AUTHORIZED_APPLICATIONS. "
                "For explicit app-opening requests, inspect computer.list_applications when useful and call computer.launch "
                "with a known app name such as Word or Excel, or an exact executable path. Launch requires approval. After launch, request exact "
                "app authorization in Applications before interacting with its controls. Never claim an app is open until "
                "the launch tool confirms it. "
                "After a tool result, use actual evidence.\n"
                "SESSION: " + json.dumps(session_state, separators=(",", ":")) +
                "\nWORKSPACE_SCOPE: " + json.dumps(workspace_state, ensure_ascii=False, separators=(",", ":")) +
                "\nTOOLS: " + json.dumps(compact_catalog, ensure_ascii=False, separators=(",", ":")) +
                "\nAUTHORIZED_APPLICATIONS: " + json.dumps(authorized, separators=(",", ":")) +
                "\nCONVERSATION:\n" + transcript
            )

        prompt = render_prompt()
        budget = max(3200, int(max(256, self.config.context - self.config.max_tokens - 128) * 3.2))
        while len(prompt) > budget and len(compact_tools) > 8:
            compact_tools.pop()
            prompt = render_prompt()
        while len(prompt) > budget and len(recent) > 1:
            recent.pop(0)
            transcript = "\n".join(compact_message(item) for item in recent)
            prompt = render_prompt()

        # Constrain structured decoding to the exact tool IDs that survived
        # context budgeting. With no tools, only a conversational message is
        # a valid decision.
        available_tool_ids = sorted(
            item["id"] for item in compact_tools if isinstance(item.get("id"), str)
        )
        decision_schema = json.loads(json.dumps(AGENT_DECISION_SCHEMA))
        if available_tool_ids:
            decision_schema["oneOf"][1]["properties"]["tool"]["enum"] = available_tool_ids
        else:
            decision_schema["oneOf"] = [decision_schema["oneOf"][0]]

        parsed, metrics = self.structured_with_metrics(
            prompt,
            json_schema=decision_schema,
            cancel_event=cancel_event,
            on_delta=on_delta,
        )

        recovery_reason = None
        if (
            isinstance(parsed, dict)
            and parsed.get("type") == "message"
            and _is_unhelpful_prompt_echo(parsed.get("content", ""), recent)
        ):
            recovery_reason = "The previous candidate only repeated the user's task."
        elif isinstance(parsed, dict) and parsed.get("type") == "tool_call":
            selected_tool = parsed.get("tool")
            if not isinstance(selected_tool, str) or selected_tool not in available_tool_ids:
                recovery_reason = (
                    f"The previous candidate selected an unregistered tool ID: {selected_tool!r}. "
                    f"Valid tool IDs are: {json.dumps(available_tool_ids)}."
                )

        if recovery_reason:
            retry_prompt = (
                prompt
                + "\n\nRECOVERY: " + recovery_reason + " "
                "Re-evaluate the request from scratch. You may return a message with a useful direct answer, "
                "or select exactly one tool ID from the valid tool IDs in the schema. "
                "For a code-writing or programming question that asks for code in chat, return the code directly "
                "as a message. Do not invent python, code, shell, or execution tools. "
                "For opening an application, use computer.list_applications and computer.launch if present. "
                "If an explicitly requested action cannot be performed, state the exact blocker in one helpful sentence. "
                "Never claim an action succeeded without an actual tool result. Do not echo the user's request."
            )
            parsed, retry_metrics = self.structured_with_metrics(
                retry_prompt,
                json_schema=decision_schema,
                cancel_event=cancel_event,
                on_delta=None,
            )
            metrics["decision_recovery_attempted"] = True
            metrics["decision_recovery_reason"] = recovery_reason
            metrics["decision_recovery_duration_ms"] = retry_metrics.get("duration_ms")
            if (
                isinstance(parsed, dict)
                and parsed.get("type") == "message"
                and _is_unhelpful_prompt_echo(parsed.get("content", ""), recent)
            ):
                raise ValueError(
                    "the local model repeated the task instead of answering; "
                    "no tool was executed. Retry with a more specific request."
                )
            if isinstance(parsed, dict) and parsed.get("type") == "tool_call":
                selected_tool = parsed.get("tool")
                if not isinstance(selected_tool, str) or selected_tool not in available_tool_ids:
                    raise ValueError(
                        f"the local model selected an unregistered tool ID {selected_tool!r}; "
                        "no tool was executed. For code questions, ask for the code directly in chat."
                    )
        metrics["schema_provenance"] = "chimera.agent_decision.v3"
        metrics["prompt_chars"] = len(prompt)
        if not isinstance(parsed, dict):
            raise ValueError(
                f"local model returned {type(parsed).__name__}; expected a message or tool_call object"
            )
        decision_type = parsed.get("type")
        if decision_type == "message":
            if not isinstance(parsed.get("content"), str) or not parsed["content"].strip():
                raise ValueError("local model returned a message decision without non-empty content")
        elif decision_type == "tool_call":
            if not isinstance(parsed.get("tool"), str) or not parsed["tool"].strip():
                raise ValueError("local model returned a tool_call without a tool id")
            if parsed["tool"] not in available_tool_ids:
                raise ValueError(
                    f"local model returned an unregistered tool ID {parsed['tool']!r}; no tool was executed"
                )
            if not isinstance(parsed.get("arguments"), dict):
                raise ValueError("local model returned a tool_call without an arguments object")
            invocation_id = parsed.get("invocation_id")
            if invocation_id is not None and (
                not isinstance(invocation_id, str) or not invocation_id.strip()
            ):
                raise ValueError("local model returned an invalid invocation_id")
        else:
            fields = ", ".join(sorted(str(key) for key in parsed)) or "none"
            raise ValueError(
                f"local model returned an unsupported decision envelope (type={decision_type!r}; fields={fields}); "
                "expected type='message' or type='tool_call'"
            )
        parsed["_metrics"] = metrics
        return parsed

    def plan(self, request: str) -> InferenceResult:
        status = self.status()
        if status.state != "ready" and self.runner is None: return InferenceResult(status.state, None, "", 0, 0, status.detail)
        prompt = "Return ONLY one complete JSON object, with no markdown or explanation. Required keys: outcome (string), inputs (object), deliverables (array), constraints (array), resources (object), permissions (array), acceptance_criteria (array), evidence (array). Do not execute anything.\nUser outcome: " + request
        started = time.perf_counter()
        try:
            if self.runner:
                output = self.runner(prompt, self.config)
                data = _json_output(str(output))
            elif self.server:
                data, _ = self.structured_with_metrics(prompt)
                output = json.dumps(data, ensure_ascii=False)
            else:
                command = [self.config.cli_path, "-m", str(self.config.model_path), "-n", str(self.config.max_tokens), "-c", str(self.config.context), "-t", str(self.config.threads), "-ngl", str(self.config.gpu_layers), "-st", "-p", prompt]
                process = subprocess.run(command, capture_output=True, text=True, timeout=self.config.timeout, shell=False, **_hidden_process_kwargs())
                if process.returncode: raise RuntimeError(f"local inference exited with code {process.returncode}")
                output = process.stdout
                data = _json_output(str(output))
            task = _task(data)
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
    if status.state != "ready" and self.runner is None:
        raise RuntimeError(status.detail)
    if self.runner:
        output = self.runner(prompt, self.config)
        return _json_output(str(output))
    if self.server:
        parsed, _ = self.structured_with_metrics(prompt)
        return parsed
    command = [self.config.cli_path, "-m", str(self.config.model_path), "-n", str(self.config.max_tokens), "-c", str(self.config.context), "-t", str(self.config.threads), "-ngl", str(self.config.gpu_layers), "-st", "-p", prompt]
    process = subprocess.run(command, capture_output=True, text=True, timeout=self.config.timeout, shell=False, **_hidden_process_kwargs())
    if process.returncode:
        raise RuntimeError(f"local inference exited with code {process.returncode}")
    return _json_output(process.stdout)

def structured_with_metrics(self, prompt: str, json_schema: dict | None = None, grammar: str | None = None, cancel_event=None, on_delta=None):
    """Run one bounded structured request and retain honest runtime metrics."""
    if json_schema is not None and grammar is not None:
        raise ValueError("provide either json_schema or grammar, not both")
    status = self.status()
    if status.state != "ready" and self.runner is None: raise RuntimeError(status.detail)
    started = time.perf_counter(); output = ""; stderr = ""; generated = 0
    try:
        if self.runner:
            output = str(self.runner(prompt, self.config)); stderr = ""
        elif self.server:
            try:
                output, metrics = self.server.complete(
                    prompt,
                    self.config.max_tokens,
                    grammar=grammar,
                    json_schema=json_schema,
                    cancel_event=cancel_event,
                    on_delta=on_delta,
                )
            except ServerUnavailable as exc:
                self.last_inference_metrics = {
                    "provider": "llama-server",
                    "status": "failed",
                    "error": str(exc),
                    "diagnostics": getattr(exc, "as_dict", lambda: None)(),
                }
                raise RuntimeError(
                    f"the selected llama-server failed; no CLI or hosted fallback was attempted: {exc}"
                ) from exc
            metrics.update({
                "prompt_tokens_estimate": len(prompt.split()),
                "generated_tokens_estimate": len(output.split()),
                "reasoning_source": "local_llama_server",
                "structured_output": "json_schema" if json_schema is not None else "grammar" if grammar else "none",
            })
            self.last_inference_metrics = metrics
            parsed, completion = _json_completion(output)
            metrics["raw_completion"] = completion
            return parsed, metrics
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

