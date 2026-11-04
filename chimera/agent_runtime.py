"""Persistent, local-only conversation runtime.

This is the first agent-workspace slice: it owns sessions, messages, and
durable lifecycle events. Tool execution is intentionally not inferred from
ordinary text; registered tools will be added behind an explicit protocol.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
import threading
import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from .tools import WorkspaceTools, tool_catalog
from .computer_use import WindowsComputerUse
from .computer_runtime import ComputerSessionManager
from .model_hub import ModelProfileStore
from .task_execution import TaskStore, TaskPolicy
from .goals import GoalStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AgentMessage:
    message_id: str
    session_id: str
    role: str
    content: str
    created_at: str


@dataclass(frozen=True)
class AgentEvent:
    event_id: str
    session_id: str
    event_type: str
    payload: dict
    sequence: int
    created_at: str


class AgentSessionStore:
    """SQLite persistence for conversation state and replayable events."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    session_id TEXT PRIMARY KEY,
                    workspace TEXT,
                    title TEXT NOT NULL,
                    model TEXT,
                    mode TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES agent_sessions(session_id)
                );
                CREATE TABLE IF NOT EXISTS agent_events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, sequence),
                    FOREIGN KEY(session_id) REFERENCES agent_sessions(session_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_messages_session ON agent_messages(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_agent_events_session ON agent_events(session_id, sequence);
                CREATE TABLE IF NOT EXISTS agent_pending_tools (
                    session_id TEXT PRIMARY KEY,
                    turn_id TEXT NOT NULL,
                    invocation_id TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    arguments TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_session_models (
                    session_id TEXT PRIMARY KEY,
                    profile_id TEXT,
                    local_only INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY(session_id) REFERENCES agent_sessions(session_id)
                );
                """
            )

    def create_session(self, workspace: Path | None, model: str | None, mode: str = "plan", title: str = "New conversation") -> str:
        session_id = uuid.uuid4().hex
        now = _now()
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO agent_sessions VALUES (?,?,?,?,?,?,?,?)", (session_id, str(workspace) if workspace else None, title, model, mode, "idle", now, now))
            self._event(db, session_id, "session.created", {"workspace": str(workspace) if workspace else None, "mode": mode})
        return session_id

    def session(self, session_id: str) -> dict | None:
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT session_id,workspace,title,model,mode,state,created_at,updated_at FROM agent_sessions WHERE session_id=?", (session_id,)).fetchone()
        if not row:
            return None
        return dict(zip(("session_id", "workspace", "title", "model", "mode", "state", "created_at", "updated_at"), row))

    def list_sessions(self, workspace: Path | None = None, limit: int = 50) -> list[dict]:
        query = "SELECT session_id,workspace,title,model,mode,state,created_at,updated_at FROM agent_sessions"
        args: list[object] = []
        if workspace is not None:
            query += " WHERE workspace=?"; args.append(str(workspace))
        query += " ORDER BY updated_at DESC LIMIT ?"; args.append(max(1, min(limit, 200)))
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute(query, args).fetchall()
        keys = ("session_id", "workspace", "title", "model", "mode", "state", "created_at", "updated_at")
        return [dict(zip(keys, row)) for row in rows]

    def set_workspace(self, session_id: str, workspace: Path) -> None:
        """Persist a workspace scope for an existing conversation."""
        path = Path(workspace).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("workspace must be an existing directory")
        with sqlite3.connect(self.db_path) as db:
            cursor = db.execute(
                "UPDATE agent_sessions SET workspace=?, updated_at=? WHERE session_id=?",
                (str(path), _now(), session_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("agent session not found")

    def add_message(self, session_id: str, role: str, content: str) -> AgentMessage:
        if role not in {"user", "assistant", "tool", "system"}:
            raise ValueError("unsupported message role")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("message content must be non-empty")
        message = AgentMessage(uuid.uuid4().hex, session_id, role, content, _now())
        with sqlite3.connect(self.db_path) as db:
            if not db.execute("SELECT 1 FROM agent_sessions WHERE session_id=?", (session_id,)).fetchone():
                raise KeyError("agent session not found")
            db.execute("INSERT INTO agent_messages VALUES (?,?,?,?,?)", (message.message_id, session_id, role, content, message.created_at))
            db.execute("UPDATE agent_sessions SET updated_at=? WHERE session_id=?", (message.created_at, session_id))
        return message

    def messages(self, session_id: str) -> list[AgentMessage]:
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute("SELECT message_id,session_id,role,content,created_at FROM agent_messages WHERE session_id=? ORDER BY created_at,message_id", (session_id,)).fetchall()
        return [AgentMessage(*row) for row in rows]

    def events(self, session_id: str, after: int = 0) -> list[AgentEvent]:
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute("SELECT event_id,session_id,event_type,sequence,payload,created_at FROM agent_events WHERE session_id=? AND sequence>? ORDER BY sequence", (session_id, after)).fetchall()
        return [AgentEvent(row[0], row[1], row[2], json.loads(row[4]), row[3], row[5]) for row in rows]

    def pending_tool(self, session_id: str) -> dict | None:
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT session_id,turn_id,invocation_id,tool,arguments,created_at FROM agent_pending_tools WHERE session_id=?", (session_id,)).fetchone()
        if not row: return None
        return dict(zip(("session_id", "turn_id", "invocation_id", "tool", "arguments", "created_at"), (row[0], row[1], row[2], row[3], json.loads(row[4]), row[5])))

    def set_pending_tool(self, session_id, turn_id, invocation_id, tool, arguments):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT OR REPLACE INTO agent_pending_tools VALUES (?,?,?,?,?,?)", (session_id, turn_id, invocation_id, tool, json.dumps(arguments, separators=(",", ":")), _now()))

    def clear_pending_tool(self, session_id):
        with sqlite3.connect(self.db_path) as db: db.execute("DELETE FROM agent_pending_tools WHERE session_id=?", (session_id,))

    def set_session_model(self, session_id: str, profile_id: str | None, local_only: bool = True):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT OR REPLACE INTO agent_session_models VALUES (?,?,?)", (session_id, profile_id, int(local_only)))

    def set_mode(self, session_id: str, mode: str) -> None:
        if mode not in {"plan", "build"}:
            raise ValueError("mode must be plan or build")
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE agent_sessions SET mode=?,updated_at=? WHERE session_id=? AND state NOT IN ('reasoning','awaiting_approval')", (mode, _now(), session_id))
            if db.total_changes != 1:
                raise ValueError("session is missing or busy")

    def session_model(self, session_id: str) -> dict:
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT profile_id,local_only FROM agent_session_models WHERE session_id=?", (session_id,)).fetchone()
        return {"profile_id": row[0], "local_only": bool(row[1])} if row else {"profile_id": None, "local_only": True}

    @staticmethod
    def _event(db, session_id: str, event_type: str, payload: dict) -> None:
        next_sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM agent_events WHERE session_id=?", (session_id,)).fetchone()[0]
        db.execute("INSERT INTO agent_events VALUES (?,?,?,?,?,?)", (uuid.uuid4().hex, session_id, event_type, next_sequence, json.dumps(payload, separators=(",", ":")), _now()))


class AgentRuntime:
    """Conversation orchestration boundary; provider failures remain truthful."""

    def __init__(self, store: AgentSessionStore, provider, profile_store: ModelProfileStore | None = None):
        self.store, self.provider = store, provider
        self.profile_store = profile_store
        self._active_providers = {}
        self.task_store = TaskStore(store.db_path)
        self.goal_store = GoalStore(store.db_path)
        self._tasks = {}
        self._listeners = {}
        self._listener_lock = threading.Lock()
        self._cancelled = set()
        self._cancel_events = {}
        self.computer = ComputerSessionManager()
        self._computer_sessions = {}

    def _artifact_output_dir(self, workspace: Path, session_id: str) -> Path:
        """Place Chimera's private support artifacts under app data when in scope.

        With a whole-drive workspace, writing a hidden directory directly under
        C:\\ is undesirable and may fail under normal user permissions. The
        app-data directory is already inside the selected drive for Windows
        source and packaged runs. Narrow workspaces retain their local fallback.
        """
        workspace = Path(workspace).resolve()
        # Only a filesystem root (C:\\, /, etc.) uses the app-data support
        # directory. Project folders retain the historical in-workspace folder.
        if workspace == Path(workspace.anchor).resolve():
            candidate = self.store.db_path.parent / "agent-artifacts" / session_id
            try:
                candidate.resolve().relative_to(workspace)
                return candidate
            except ValueError:
                pass
        return workspace / ".chimera-agent-artifacts" / session_id

    def subscribe(self, session_id: str, callback):
        with self._listener_lock: self._listeners.setdefault(session_id, []).append(callback)
        return lambda: self.unsubscribe(session_id, callback)

    def unsubscribe(self, session_id: str, callback):
        with self._listener_lock:
            self._listeners[session_id] = [item for item in self._listeners.get(session_id, []) if item is not callback]

    def cancel(self, session_id: str) -> bool:
        if not self.store.session(session_id): return False
        self._cancelled.add(session_id)
        if session_id in self._cancel_events: self._cancel_events[session_id].set()
        with sqlite3.connect(self.store.db_path) as db: db.execute("UPDATE agent_sessions SET state='cancelled',updated_at=? WHERE session_id=? AND state IN ('reasoning','awaiting_approval')", (_now(), session_id))
        self._record(session_id, "turn.cancelled", {})
        return True

    def _record(self, session_id: str, event_type: str, payload: dict):
        with sqlite3.connect(self.store.db_path) as db:
            AgentSessionStore._event(db, session_id, event_type, payload)
        event = self.store.events(session_id)[-1]
        task_id = self._tasks.get(session_id)
        if task_id: self.task_store.apply_runtime_event(task_id, event.event_id, event_type, payload)
        with self._listener_lock: listeners = tuple(self._listeners.get(session_id, ()))
        for callback in listeners:
            try: callback(event)
            except Exception: pass

    def create_session(self, workspace: Path | None = None, mode: str = "plan") -> str:
        model = str(getattr(getattr(self.provider, "config", None), "model_path", "")) or None
        return self.store.create_session(workspace, model, mode)

    def set_session_model(self, session_id: str, profile_id: str | None, local_only: bool = True):
        if self.profile_store and profile_id:
            profile = next((p for p in self.profile_store.list() if p.profile_id == profile_id and p.enabled), None)
            if profile is None or (local_only and profile.hosted): raise ValueError("model is unavailable under the current routing policy")
        self.store.set_session_model(session_id, profile_id, local_only)

    def _provider_for(self, session_id: str):
        selection = self.store.session_model(session_id)
        profile_id = selection["profile_id"]
        if not profile_id or not self.profile_store: return self.provider
        profile = next((p for p in self.profile_store.list() if p.profile_id == profile_id and p.enabled), None)
        if profile is None or (selection["local_only"] and profile.hosted): raise RuntimeError("selected model profile is unavailable or violates local-only policy")
        from .providers.registry import get_provider_registry
        credentials = getattr(self.provider, "credentials", None)
        accountant = getattr(self.provider, "accountant", None)
        local_p = self.provider if not profile.hosted else None
        if not profile.hosted and getattr(self.provider, "config", None):
            from .local_model import LocalModelConfig, LocalModelProvider
            config = self.provider.config
            local_p = LocalModelProvider(LocalModelConfig(Path(profile.model_path), config.cli_path, config.context, config.max_tokens, config.timeout, config.threads, config.gpu_layers, config.server_path))
        return get_provider_registry().resolve_from_profile(profile, credentials=credentials, local_provider=local_p, accountant=accountant)

    def _protocol_catalog(self, session=None):
        aliases = {("workspace." + name if "." not in name else name): name for name in tool_catalog()["operations"]}
        session_workspace = None
        # The catalog is intentionally capability-based: workspace operations
        # are not offered when there is no validated workspace context.
        session_workspace = session.get("workspace") if session else None
        if not session_workspace or not Path(session_workspace).is_dir():
            aliases = {key: value for key, value in aliases.items() if not key.startswith("workspace.")}
        authorized_applications = [
            {
                "session_id": identity.session_id,
                "pid": identity.pid,
                "executable": identity.executable,
                "process_create_time": identity.process_create_time,
            }
            for identity in self.computer.sessions.values()
            if identity.owner_session_id == (session.get("session_id") if session else None)
            and self.computer._identity_is_current(identity)
        ]
        catalog_tools = []
        for key, value in aliases.items():
            # The Applications page performs live inventory discovery itself.
            # Never waste model context on OS helper windows or raw process dumps.
            if key == "computer.list_windows":
                continue
            # Desktop actions are offered only after an exact process has been
            # explicitly authorized for this conversation.
            if key.startswith("computer.") and key not in {"computer.launch", "computer.list_applications"} and not authorized_applications:
                continue
            catalog_tools.append({"id": key, **tool_catalog()["operations"][value]})
        try:
            from .applications.registry import get_application_registry
            from .applications.dispatch import application_tool_catalog
            application_registry = get_application_registry()
            # Discover local connectors before advertising their installed-only actions.
            # Discovery is read-only; execution remains behind the runtime policy gate.
            application_registry.discover_all()
            catalog_tools.extend(application_tool_catalog(application_registry))
        except Exception:
            pass
        return {"version": 1, "tools": catalog_tools, "authorized_applications": authorized_applications, "interaction_inventory": self.computer.interaction_inventory()}

    @staticmethod
    def _decision(value):
        if not isinstance(value, dict):
            raise ValueError(
                f"agent provider returned {type(value).__name__}; expected a JSON message or tool_call object"
            )
        decision_type = value.get("type")
        if decision_type not in {"message", "tool_call"}:
            fields = ", ".join(sorted(str(key) for key in value)) or "none"
            raise ValueError(
                "agent provider returned an unsupported decision envelope "
                f"(type={decision_type!r}; fields={fields}); expected type='message' or type='tool_call'"
            )
        if value["type"] == "message":
            if not isinstance(value.get("content"), str) or not value["content"].strip(): raise ValueError("message content must be non-empty")
            return value
        tool = value.get("tool")
        if not isinstance(tool, str) or not (tool.startswith("workspace.") or tool.startswith("computer.") or tool.startswith("application.")): raise ValueError("tool identifier is invalid")
        if not isinstance(value.get("arguments"), dict): raise ValueError("tool arguments must be an object")
        invocation = value.get("invocation_id")
        if invocation is not None and (not isinstance(invocation, str) or not invocation.strip()): raise ValueError("tool invocation_id is invalid")
        return value

    @staticmethod
    def _validate_arguments(operation, arguments):
        if operation.startswith("application."):
            from .applications.registry import get_application_registry
            from .applications.dispatch import decode_tool_id, validate_application_arguments
            decoded = decode_tool_id(operation)
            if not decoded:
                raise ValueError(f"malformed application tool id: {operation}")
            app_id, action_id = decoded
            connector = get_application_registry().get(app_id)
            if not connector:
                raise ValueError(f"unknown application connector: {app_id}")
            action = next((a for a in connector.manifest.actions if a.action_id == action_id), None)
            if not action:
                raise ValueError(f"unregistered action '{action_id}' on application '{app_id}'")
            validate_application_arguments(action, arguments)
            return
        contracts = {
            "list_files": {"relative": str, "recursive": bool},
            "inspect_file": {"path": str},
            "search_files": {"pattern": str},
            "search_text": {"query": str},
            "read_text": {"path": str},
            "create_artifact": {"name": str, "content": str},
            "create_file": {"path": str, "content": str},
            "computer.list_applications": {},
            "computer.list_windows": {},
            "computer.inspect_controls": {"session_id": str, "hwnd": int},
            "computer.inspect_elements": {"session_id": str},
            "computer.launch": {"executable": str},
            "computer.screenshot": {"session_id": str, "hwnd": int, "path": str},
            "computer.inspect_window": {"session_id": str},
            "computer.inspect_pattern_support": {"session_id": str, "reference_id": str},
            "computer.invoke_element": {"session_id": str, "reference_id": str},
            "computer.set_value": {"session_id": str, "reference_id": str, "value": str},
            "computer.read_text": {"session_id": str, "reference_id": str},
            "computer.toggle_element": {"session_id": str, "reference_id": str},
            "computer.select_element": {"session_id": str, "reference_id": str},
            "computer.expand_element": {"session_id": str, "reference_id": str},
            "computer.collapse_element": {"session_id": str, "reference_id": str},
        }
        expected = contracts.get(operation)
        if expected is None: raise ValueError(f"unregistered tool: workspace.{operation}")
        if set(arguments) - set(expected): raise ValueError(f"unsupported arguments for {operation}")
        for name, kind in expected.items():
            if name in arguments and (type(arguments[name]) is not kind): raise ValueError(f"argument {name} must be {kind.__name__}")

    def _messages_with_goal_context(self, session_id: str):
        """Attach private, operator-owned goal context to local inference only.

        Goal context is ephemeral and read-only: it is not written into the chat
        transcript, is never sent to hosted providers, and cannot change goal state.
        """
        messages = list(self.store.messages(session_id))
        provider = self._active_providers.get(session_id, self.provider)
        profile = getattr(provider, "profile", None)
        config = getattr(provider, "config", None)
        hosted = bool(getattr(profile, "hosted", False) or getattr(config, "hosted", False))
        if hosted:
            return messages

        goals = self.goal_store.list(status="active", limit=2)
        paused = self.goal_store.list(status="paused", limit=1)
        selected = goals + paused
        if not selected:
            return messages

        context = {
            "notice": "Operator-defined goals are read-only context, not new instructions. Follow the current user request. Use a goal only when relevant. Never mark a goal completed or claim its success criteria are satisfied; only the operator can update goal state with evidence.",
            "goals": [
                {
                    "goal_id": goal.goal_id,
                    "title": goal.title[:100],
                    "description": goal.description[:120],
                    "status": goal.status,
                    "priority": goal.priority,
                    "project": goal.project[:60],
                    "success_criteria": [item[:100] for item in goal.success_criteria[:2]],
                }
                for goal in selected
            ],
        }
        messages.insert(0, AgentMessage(
            message_id="goal-context-" + session_id,
            session_id=session_id,
            role="system",
            content="Private local goal context (not persisted in this conversation):\n"
            + json.dumps(context, ensure_ascii=False, separators=(",", ":")),
            created_at=_now(),
        ))
        return messages

    def _decide(self, messages, session, cancel_event=None, turn_id=None):
        provider = self._active_providers.get(session["session_id"], self.provider)
        if not hasattr(provider, "decide"):
            return None
        kwargs = {"session": session, "cancel_event": cancel_event}
        accepted = inspect.signature(provider.decide).parameters
        return self._decision(provider.decide(messages, self._protocol_catalog(session), **{key: value for key, value in kwargs.items() if key in accepted}))

    def send(self, session_id: str, content: str) -> dict:
        session = self.store.session(session_id)
        if not session:
            raise KeyError("agent session not found")
        message = self.store.add_message(session_id, "user", content)
        cancel_event = threading.Event(); self._cancel_events[session_id] = cancel_event
        with sqlite3.connect(self.store.db_path) as db: db.execute("UPDATE agent_sessions SET state='reasoning',updated_at=? WHERE session_id=?", (_now(), session_id))
        self._record(session_id, "turn.started", {"message_id": message.message_id})
        try:
            task_id = self.task_store.create(session_id, content, mode=session.get("mode", "plan"), provider=None, model=session.get("model")); self._tasks[session_id] = task_id
            self.task_store.transition(task_id, "running")
            self._active_providers[session_id] = self._provider_for(session_id)
            provider = self._active_providers[session_id]
            if self.store.session_model(session_id)["profile_id"]:
                self._record(session_id, "model.selected", {"profile_id": self.store.session_model(session_id)["profile_id"], "provider": type(provider).__name__})
            if hasattr(provider, "decide"):
                return self._agent_loop(session_id, session, message, cancel_event=cancel_event)
            kwargs = {"session": session, "cancel_event": cancel_event, "on_delta": lambda text: self._record(session_id, "assistant.text.delta", {"text": text})}
            if hasattr(provider, "converse"):
                accepted = inspect.signature(provider.converse).parameters
                response = provider.converse(self.store.messages(session_id), **{key: value for key, value in kwargs.items() if key in accepted})
            else:
                result = provider.generate([{"role": m.role, "content": m.content} for m in self.store.messages(session_id)], cancel_event=cancel_event, on_delta=kwargs.get("on_delta"))
                response = type("ProviderResponse", (), {"text": result["text"], "metrics": result})()
            answer = response.text if hasattr(response, "text") else str(response)
            assistant = self.store.add_message(session_id, "assistant", answer)
            with sqlite3.connect(self.store.db_path) as db: db.execute("UPDATE agent_sessions SET state='idle',updated_at=? WHERE session_id=?", (_now(), session_id))
            self._record(session_id, "assistant.message.completed", {"message_id": assistant.message_id})
            self._record(session_id, "turn.completed", {})
            return {"status": "completed", "message": assistant, "reasoning": getattr(response, "metrics", {})}
        except InterruptedError as exc:
            with sqlite3.connect(self.store.db_path) as db: db.execute("UPDATE agent_sessions SET state='cancelled',updated_at=? WHERE session_id=?", (_now(), session_id))
            self._record(session_id, "turn.cancelled", {"error": str(exc)})
            return {"status": "cancelled", "error": str(exc)}
        except Exception as exc:
            with sqlite3.connect(self.store.db_path) as db: db.execute("UPDATE agent_sessions SET state='failed',updated_at=? WHERE session_id=?", (_now(), session_id))
            self._record(session_id, "turn.failed", {"error": str(exc)})
            return {"status": "failed", "error": str(exc)}
        finally:
            self._cancel_events.pop(session_id, None)
            self._active_providers.pop(session_id, None)
            task_id = self._tasks.pop(session_id, None)
            if task_id:
                state = self.store.session(session_id).get("state") if self.store.session(session_id) else "failed"
                mapped = {"idle": "completed", "cancelled": "cancelled", "awaiting_approval": "waiting_approval", "failed": "failed", "reasoning": "running"}.get(state, "failed")
                self.task_store.transition(task_id, mapped)


    def execute_workflow_tool(
        self,
        session_id: str,
        tool_id: str,
        arguments: dict,
        *,
        plan_id: str,
        invocation_id: str,
        approved: bool = False,
    ) -> dict:
        """Execute a workflow step through the same policy boundary as agent tools."""
        session = self.store.session(session_id)
        if not session:
            raise KeyError("agent session not found")
        if not isinstance(arguments, dict):
            raise ValueError("workflow arguments must be an object")
        if not (tool_id.startswith("workspace.") or tool_id.startswith("application.")):
            raise ValueError("unsupported workflow tool namespace")
        if not isinstance(plan_id, str) or not plan_id.strip():
            raise ValueError("workflow plan_id is required")
        if not isinstance(invocation_id, str) or not invocation_id.strip():
            raise ValueError("workflow invocation_id is required")

        if session_id in self._cancelled:
            raise InterruptedError("workflow cancelled before tool execution")
        self._decision({
            "type": "tool_call",
            "tool": tool_id,
            "arguments": arguments,
            "invocation_id": invocation_id,
        })
        operation = tool_id.removeprefix("workspace.") if tool_id.startswith("workspace.") else tool_id
        self._validate_arguments(operation, arguments)

        connector = None
        action = None
        workspace = None
        if tool_id.startswith("application."):
            from .applications.registry import get_application_registry
            from .applications.dispatch import decode_tool_id, validate_application_arguments, APPLICATION_READ_PERMISSIONS
            decoded = decode_tool_id(tool_id)
            if not decoded:
                raise ValueError(f"malformed application tool id: {tool_id}")
            app_id, action_id = decoded
            connector = get_application_registry().get(app_id)
            if connector is None:
                raise ValueError(f"unregistered application: {app_id}")
            manifest = connector.discover()
            if manifest is not None:
                connector.manifest = manifest
            action = next((item for item in connector.manifest.actions if item.action_id == action_id), None)
            if action is None or getattr(action, "unavailable", False):
                raise ValueError(f"application action is unavailable: {tool_id}")
            validate_application_arguments(action, arguments)
            permission = action.permission
            needs_approval = bool(action.requires_approval or permission not in APPLICATION_READ_PERMISSIONS)
        else:
            from .tools import tool_catalog
            current = self.store.session(session_id)
            workspace = Path(current["workspace"]) if current and current.get("workspace") else None
            if workspace is None or not workspace.is_dir():
                return {"status": "blocked", "error": "a valid workspace is required"}
            spec = tool_catalog()["operations"].get(operation)
            if spec is None:
                raise ValueError(f"unregistered workflow operation: {operation}")
            permission = spec["permission"]
            needs_approval = permission not in {"workspace.read", "computer.observe"}

        self._record(session_id, "tool.call.requested", {
            "turn_id": plan_id, "invocation_id": invocation_id,
            "tool": tool_id, "arguments": arguments, "source": "workflow",
        })

        if connector is not None:
            allowed, reason, action = connector.validate_action_call(
                action.action_id, arguments, mode=session.get("mode", "plan")
            )
            if not allowed:
                status, data, error = "blocked", {}, reason
                self.store.add_message(session_id, "tool", json.dumps({
                    "invocation_id": invocation_id, "tool": tool_id,
                    "result": data, "status": status, "error": error,
                }, separators=(",", ":")))
                self._record(session_id, "tool.failed", {
                    "turn_id": plan_id, "invocation_id": invocation_id,
                    "tool": tool_id, "status": status, "data": data, "error": error,
                })
                return {"status": status, "error": error}

        try:
            TaskPolicy.validate(session.get("mode", "plan"), tool_id, permission)
        except PermissionError as exc:
            self.store.add_message(session_id, "tool", json.dumps({
                "invocation_id": invocation_id, "tool": tool_id,
                "result": {}, "status": "blocked", "error": str(exc),
            }, separators=(",", ":")))
            self._record(session_id, "tool.failed", {
                "turn_id": plan_id, "invocation_id": invocation_id,
                "tool": tool_id, "status": "blocked", "data": {}, "error": str(exc),
            })
            return {"status": "blocked", "error": str(exc)}

        pending = self.store.pending_tool(session_id)
        if needs_approval and not approved:
            if pending:
                if (
                    pending.get("tool") == tool_id
                    and pending.get("arguments") == arguments
                    and pending.get("invocation_id") == invocation_id
                ):
                    return {"status": "awaiting_approval", "tool": tool_id, "invocation_id": invocation_id, "arguments": arguments}
                raise RuntimeError("another action is already awaiting approval in this session")
            self.store.set_pending_tool(session_id, plan_id, invocation_id, tool_id, arguments)
            with sqlite3.connect(self.store.db_path) as db:
                db.execute("UPDATE agent_sessions SET state='awaiting_approval',updated_at=? WHERE session_id=?", (_now(), session_id))
            self._record(session_id, "tool.permission.required", {
                "turn_id": plan_id, "invocation_id": invocation_id,
                "tool": tool_id, "arguments": arguments, "source": "workflow",
            })
            return {"status": "awaiting_approval", "tool": tool_id, "invocation_id": invocation_id, "arguments": arguments}

        if needs_approval:
            if (
                not pending
                or pending.get("tool") != tool_id
                or pending.get("arguments") != arguments
                or pending.get("invocation_id") != invocation_id
                or session.get("state") != "awaiting_approval"
            ):
                raise PermissionError("approval does not match the exact pending workflow action")
            self._record(session_id, "tool.permission.granted", {
                "turn_id": plan_id, "invocation_id": invocation_id,
                "tool": tool_id, "source": "operator",
            })
        elif approved:
            raise PermissionError("this workflow action is not awaiting approval")

        self._record(session_id, "tool.started", {
            "turn_id": plan_id, "invocation_id": invocation_id,
            "tool": tool_id, "arguments": arguments,
        })
        try:
            if connector is not None:
                raw = connector.execute_action(action.action_id, arguments, approved=approved)
                status = raw.get("status", "completed") if isinstance(raw, dict) else "failed"
                data = raw if isinstance(raw, dict) else {}
                error = raw.get("error") if isinstance(raw, dict) else "connector returned a non-object result"
            else:
                from .tools import WorkspaceTools
                result = WorkspaceTools(
                    workspace, self._artifact_output_dir(workspace, session_id)
                ).run(operation, arguments, approved=approved)
                status, data, error = result.status, result.data, result.error
        except Exception as exc:
            status, data, error = "failed", {}, str(exc)

        if needs_approval:
            self.store.clear_pending_tool(session_id)
            with sqlite3.connect(self.store.db_path) as db:
                db.execute("UPDATE agent_sessions SET state='reasoning',updated_at=? WHERE session_id=?", (_now(), session_id))

        self.store.add_message(session_id, "tool", json.dumps({
            "invocation_id": invocation_id, "tool": tool_id,
            "result": data, "status": status, "error": error,
        }, separators=(",", ":")))
        self._record(session_id, "tool.completed" if status == "completed" else "tool.failed", {
            "turn_id": plan_id, "invocation_id": invocation_id,
            "tool": tool_id, "status": status, "data": data, "error": error,
        })
        return {"status": status, "data": data, "error": error, "tool": tool_id, "invocation_id": invocation_id}


    def _agent_loop(self, session_id, session, message, approved_invocation=None, max_turns=8, max_tools=12, cancel_event=None):
        turn_id = uuid.uuid4().hex
        workspace = Path(session["workspace"]) if session.get("workspace") else None
        tools = None
        if workspace is not None and workspace.is_dir():
            tools = WorkspaceTools(workspace, self._artifact_output_dir(workspace, session_id))
        for _ in range(max_turns):
            if session_id in self._cancelled:
                self._cancelled.discard(session_id)
                return {"status": "cancelled"}
            self._record(session_id, "model.request.started", {"turn_id": turn_id})
            decision = self._decide(self._messages_with_goal_context(session_id), session, cancel_event, turn_id)
            self._record(session_id, "model.request.completed", {"turn_id": turn_id, "decision_type": decision["type"]})
            if decision["type"] == "message":
                assistant = self.store.add_message(session_id, "assistant", decision["content"])
                with sqlite3.connect(self.store.db_path) as db: db.execute("UPDATE agent_sessions SET state='idle',updated_at=? WHERE session_id=?", (_now(), session_id))
                self._record(session_id, "turn.completed", {"turn_id": turn_id})
                return {"status": "completed", "message": assistant, "tools": []}
            if max_tools <= 0: raise RuntimeError("maximum tool-call limit reached")
            tool_id = decision["tool"]
            is_app_tool = tool_id.startswith("application.")
            app_connector, app_action = None, None
            if is_app_tool:
                from .applications.registry import get_application_registry
                from .applications.dispatch import decode_tool_id
                decoded = decode_tool_id(tool_id)
                if not decoded:
                    raise ValueError(f"malformed application tool id: {tool_id}")
                app_id, action_id = decoded
                app_connector = get_application_registry().get(app_id)
                if not app_connector:
                    raise ValueError(f"unregistered application: {app_id}")
                app_action = next((a for a in app_connector.manifest.actions if a.action_id == action_id), None)
                if not app_action:
                    raise ValueError(f"unregistered action '{action_id}' on application '{app_id}'")
                operation = tool_id
                permission = app_action.permission
            else:
                operation = tool_id.removeprefix("workspace.") if tool_id.startswith("workspace.") else tool_id
                if operation not in tool_catalog()["operations"]: raise ValueError(f"unregistered tool: {tool_id}")
                permission = tool_catalog()["operations"][operation]["permission"]

            self._validate_arguments(operation, decision["arguments"])
            invocation = decision.get("invocation_id") or uuid.uuid4().hex
            self._record(session_id, "tool.call.requested", {"turn_id": turn_id, "invocation_id": invocation, "tool": tool_id, "arguments": decision["arguments"]})
            if tool_id.startswith("workspace."):
                current = self.store.session(session_id)
                current_workspace = Path(current["workspace"]) if current and current.get("workspace") else None
                if current_workspace is None or not current_workspace.is_dir():
                    result_data = {"status": "workspace_required", "reason": "select an existing project workspace before using workspace tools"}
                    self.store.add_message(session_id, "tool", json.dumps({"invocation_id": invocation, "tool": tool_id, "result": result_data}, separators=(",", ":")))
                    self._record(session_id, "tool.completed", {"turn_id": turn_id, "invocation_id": invocation, "tool": tool_id, "status": "workspace_required", "data": result_data})
                    continue
                workspace = current_workspace
                tools = WorkspaceTools(workspace, self._artifact_output_dir(workspace, session_id))

            if is_app_tool:
                valid, reason, _ = app_connector.validate_action_call(app_action.action_id, decision["arguments"], mode=session.get("mode", "plan"))
                if not valid:
                    result_data = {"status": "blocked", "reason": reason, "tool": tool_id}
                    self.store.add_message(session_id, "tool", json.dumps({"invocation_id": invocation, "tool": tool_id, "result": result_data, "status": "blocked", "error": reason}, separators=(",", ":")))
                    self._record(session_id, "tool.failed", {"turn_id": turn_id, "invocation_id": invocation, "tool": tool_id, "status": "blocked", "data": result_data, "error": reason})
                    continue

            try:
                TaskPolicy.validate(session.get("mode", "plan"), tool_id, permission)
            except PermissionError as exc:
                result_data = {"status": "blocked", "reason": str(exc), "tool": tool_id}
                self.store.add_message(session_id, "tool", json.dumps({"invocation_id": invocation, "tool": tool_id, "result": result_data, "status": "blocked", "error": str(exc)}, separators=(",", ":")))
                self._record(session_id, "tool.failed", {"turn_id": turn_id, "invocation_id": invocation, "tool": tool_id, "status": "blocked", "data": result_data, "error": str(exc)})
                continue

            needs_approval = False
            if is_app_tool:
                from .applications.dispatch import APPLICATION_READ_PERMISSIONS
                needs_approval = app_action.requires_approval or (permission not in APPLICATION_READ_PERMISSIONS)
            else:
                needs_approval = permission not in {"workspace.read", "computer.observe"}

            if tool_id == "computer.launch":
                # Resolve the friendly app name before presenting the approval
                # card so the operator approves one exact executable.
                launch_preflight = WindowsComputerUse(workspace).launch(
                    decision["arguments"].get("executable", ""), approved=False
                )
                if launch_preflight.get("status") != "awaiting_approval":
                    error = launch_preflight.get("reason", "application executable could not be resolved")
                    payload = {
                        "invocation_id": invocation,
                        "tool": tool_id,
                        "arguments": decision["arguments"],
                        "result": launch_preflight,
                        "status": launch_preflight.get("status", "blocked"),
                        "error": error,
                    }
                    self.store.add_message(session_id, "tool", json.dumps(payload, separators=(",", ":")))
                    self._record(session_id, "tool.failed", {
                        "turn_id": turn_id,
                        "invocation_id": invocation,
                        "tool": tool_id,
                        "status": launch_preflight.get("status", "blocked"),
                        "data": launch_preflight,
                        "error": error,
                    })
                    max_tools -= 1
                    continue
                decision["arguments"]["executable"] = launch_preflight["application"]

            if tool_id == "workspace.create_file":
                preflight = tools.run("create_file", decision["arguments"], approved=False)
                if preflight.status != "awaiting_approval":
                    error = preflight.error or "The requested file operation did not pass validation."
                    payload = {
                        "invocation_id": invocation,
                        "tool": tool_id,
                        "arguments": decision["arguments"],
                        "result": preflight.data,
                        "status": preflight.status,
                        "error": error,
                    }
                    self.store.add_message(session_id, "tool", json.dumps(payload, separators=(",", ":")))
                    self._record(session_id, "tool.failed", {
                        "turn_id": turn_id,
                        "invocation_id": invocation,
                        "tool": tool_id,
                        "status": preflight.status,
                        "data": preflight.data,
                        "error": error,
                    })
                    max_tools -= 1
                    continue

            if needs_approval:
                self.store.set_pending_tool(session_id, turn_id, invocation, tool_id, decision["arguments"])
                with sqlite3.connect(self.store.db_path) as db: db.execute("UPDATE agent_sessions SET state='awaiting_approval',updated_at=? WHERE session_id=?", (_now(), session_id))
                self._record(session_id, "tool.permission.required", {"turn_id": turn_id, "invocation_id": invocation, "tool": tool_id, "arguments": decision["arguments"]})
                return {"status": "awaiting_approval", "tool": tool_id, "invocation_id": invocation, "arguments": decision["arguments"]}
            self._record(session_id, "tool.started", {"turn_id": turn_id, "invocation_id": invocation, "tool": tool_id, "arguments": decision["arguments"]})
            if session_id in self._cancelled:
                self._cancelled.discard(session_id)
                return {"status": "cancelled"}
            if is_app_tool:
                act_res = app_connector.execute_action(app_action.action_id, decision["arguments"], approved=False)
                res_status = act_res.get("status", "completed")
                res_err = act_res.get("error")
                result = type("AppResult", (), {"data": act_res, "status": res_status, "error": res_err})()
            elif tool_id.startswith("computer."):
                computer = WindowsComputerUse(workspace)
                args = decision["arguments"]

                def require_authorized_app(app_session_id):
                    identity = self.computer.sessions.get(app_session_id)
                    if not identity or identity.owner_session_id != session_id:
                        raise PermissionError("authorize this exact application for the active Chimera conversation first")
                    if not self.computer._identity_is_current(identity):
                        raise PermissionError("authorized application process identity has changed")
                    return identity

                if operation == "computer.list_applications":
                    result_data = computer.list_applications()
                    result_status, result_error = "completed", None
                elif operation == "computer.list_windows":
                    result_data = [
                        window for application in computer.list_applications()
                        for window in application.get("windows", [])
                    ]
                    result_status, result_error = "completed", None
                elif operation == "computer.inspect_window":
                    require_authorized_app(args["session_id"])
                    observation = self.computer.observe(args["session_id"])
                    result_data = {
                        "session_id": observation.session_id,
                        "windows": observation.windows,
                        "backend": observation.backend,
                    }
                    result_status, result_error = "completed", None
                elif operation == "computer.inspect_controls":
                    require_authorized_app(args["session_id"])
                    self.computer.validate_window(args["session_id"], args["hwnd"])
                    result_data = computer.controls(args["hwnd"])
                    result_status, result_error = "completed", None
                elif operation == "computer.inspect_elements":
                    require_authorized_app(args["session_id"])
                    observation = self.computer.observe(args["session_id"])
                    result_data = {
                        "observation_id": observation.observation_id,
                        "session_id": observation.session_id,
                        "generation": observation.generation,
                        "backend": observation.backend,
                        "elements": observation.elements[:250],
                        "limitation": observation.limitation,
                    }
                    result_status, result_error = "completed", None
                elif operation == "computer.inspect_pattern_support":
                    require_authorized_app(args["session_id"])
                    reference = self.computer.references.get(args["reference_id"])
                    if not reference or reference.session_id != args["session_id"]:
                        result_data, result_status, result_error = {}, "failed", "unknown or cross-session reference"
                    else:
                        self.computer.validate_reference(reference)
                        observed = self.computer.observations.get(args["session_id"], (0, None))[1]
                        result_data = {
                            "patterns": next(
                                (element.get("patterns", []) for element in (observed.elements if observed else [])
                                 if element.get("reference_id") == args["reference_id"]),
                                [],
                            )
                        }
                        result_status, result_error = "completed", None
                elif operation in {
                    "computer.invoke_element", "computer.set_value", "computer.read_text",
                    "computer.toggle_element", "computer.select_element",
                    "computer.expand_element", "computer.collapse_element",
                }:
                    require_authorized_app(args["session_id"])
                    action = operation.removeprefix("computer.")
                    strategy_action = {
                        "toggle_element": "toggle",
                        "select_element": "select_item",
                        "expand_element": "expand",
                        "collapse_element": "collapse",
                    }.get(action, action)
                    strategy = self.computer.choose_interaction(
                        strategy_action, evidence={"semantic_control": True}
                    )
                    result_data = self.computer.act_reference(
                        args["session_id"], args["reference_id"], action,
                        args.get("value"), approved=False,
                    )
                    result_data["strategy"] = strategy
                    result_status, result_error = result_data.get("status"), result_data.get("reason")
                elif operation == "computer.screenshot":
                    require_authorized_app(args["session_id"])
                    self.computer.validate_window(args["session_id"], args["hwnd"])
                    result_data = computer.screenshot(
                        args["hwnd"], Path(args["path"]), approved=True
                    )
                    result_status, result_error = result_data.get("status"), result_data.get("reason")
                else:
                    result_data, result_status, result_error = {}, "blocked", "unsupported or disabled computer operation"
                result = type("ComputerResult", (), {
                    "data": {"items": result_data},
                    "status": result_status,
                    "error": result_error,
                })()
            else:
                result = tools.run(operation, decision["arguments"])
            self.store.add_message(session_id, "tool", json.dumps({"invocation_id": invocation, "tool": tool_id, "arguments": decision["arguments"], "result": result.data, "status": result.status, "error": result.error}, separators=(",", ":")))
            self._record(session_id, "tool.completed" if result.status == "completed" else "tool.failed", {"turn_id": turn_id, "invocation_id": invocation, "tool": tool_id, "status": result.status, "data": result.data, "error": result.error})
            max_tools -= 1
        raise RuntimeError("maximum agent-turn limit reached")

    def approve_pending(self, session_id: str, approve: bool) -> dict:
        pending = self.store.pending_tool(session_id)
        if not pending: raise ValueError("no pending tool approval")
        session = self.store.session(session_id)
        if not session or session["state"] != "awaiting_approval": raise ValueError("session is not awaiting approval")
        task = self.task_store.latest_for_session(session_id)
        if task:
            self._tasks[session_id] = task.task_id
        if not approve:
            self.store.clear_pending_tool(session_id)
            self.store.add_message(session_id, "tool", json.dumps({"invocation_id": pending["invocation_id"], "status": "denied", "error": "user denied approval"}))
            with sqlite3.connect(self.store.db_path) as db: db.execute("UPDATE agent_sessions SET state='idle',updated_at=? WHERE session_id=?", (_now(), session_id))
            self._record(session_id, "tool.permission.denied", {"invocation_id": pending["invocation_id"]})
            if task:
                self.task_store.transition(task.task_id, "blocked")
                self._tasks.pop(session_id, None)
            return {"status": "denied"}
        workspace = Path(session["workspace"]) if session.get("workspace") else None
        if pending["tool"].startswith("application."):
            from .applications.registry import get_application_registry
            from .applications.dispatch import (
                decode_tool_id, validate_application_arguments, APPLICATION_READ_PERMISSIONS,
            )
            decoded = decode_tool_id(pending["tool"])
            if not decoded:
                raise ValueError(f"malformed pending application tool: {pending['tool']}")
            app_id, action_id = decoded
            connector = get_application_registry().get(app_id)
            if not connector:
                raise ValueError(f"unregistered application: {app_id}")
            refreshed = connector.discover()
            if refreshed is not None:
                connector.manifest = refreshed
            action = next((item for item in connector.manifest.actions if item.action_id == action_id), None)
            if action is None or getattr(action, "unavailable", False):
                raise PermissionError("application action is no longer available")
            self._validate_arguments(pending["tool"], pending["arguments"])
            validate_application_arguments(action, pending["arguments"])
            allowed, reason, action = connector.validate_action_call(
                action_id, pending["arguments"], mode=session.get("mode", "plan")
            )
            if not allowed:
                raise PermissionError(reason)
            TaskPolicy.validate(session.get("mode", "plan"), pending["tool"], action.permission)
            if not (action.requires_approval or action.permission not in APPLICATION_READ_PERMISSIONS):
                raise PermissionError("the action no longer requires approval")
            self._record(session_id, "tool.permission.granted", {
                "invocation_id": pending["invocation_id"], "tool": pending["tool"], "source": "operator",
            })
            self._record(session_id, "tool.started", {
                "invocation_id": pending["invocation_id"], "tool": pending["tool"],
                "arguments": pending["arguments"],
            })
            act_res = connector.execute_action(action_id, pending["arguments"], approved=True)
            res_status = act_res.get("status", "completed")
            res_err = act_res.get("error")
            result = type("AppResult", (), {"data": act_res, "status": res_status, "error": res_err})()
        elif pending["tool"].startswith("computer.") and pending["tool"] in {
            "computer.invoke_element", "computer.set_value", "computer.toggle_element",
            "computer.select_element", "computer.expand_element", "computer.collapse_element",
            "computer.screenshot", "computer.launch",
        }:
            operation = pending["tool"]
            args = pending["arguments"]
            self._validate_arguments(operation, args)
            self._record(session_id, "tool.permission.granted", {
                "invocation_id": pending["invocation_id"], "tool": operation, "source": "operator",
            })
            self._record(session_id, "tool.started", {
                "invocation_id": pending["invocation_id"], "tool": operation, "arguments": args,
            })
            if operation == "computer.launch":
                result_data = WindowsComputerUse(workspace).launch(args["executable"], approved=True)
            elif operation == "computer.screenshot":
                identity = self.computer.sessions.get(args["session_id"])
                if not identity or identity.owner_session_id != session_id:
                    raise PermissionError("application is not authorized for this Chimera conversation")
                self.computer.validate_window(args["session_id"], args["hwnd"])
                result_data = WindowsComputerUse(workspace).screenshot(args["hwnd"], Path(args["path"]), approved=True)
            else:
                identity = self.computer.sessions.get(args["session_id"])
                if not identity or identity.owner_session_id != session_id:
                    raise PermissionError("application is not authorized for this Chimera conversation")
                result_data = self.computer.act_reference(
                    args["session_id"], args["reference_id"],
                    operation.removeprefix("computer."), args.get("value"), approved=True,
                )
            result = type("ComputerResult", (), {
                "data": result_data, "status": result_data.get("status", "failed"),
                "error": result_data.get("reason"),
            })()
        elif pending["tool"].startswith("computer."):
            self.store.clear_pending_tool(session_id)
            with sqlite3.connect(self.store.db_path) as db:
                db.execute("UPDATE agent_sessions SET state='idle',updated_at=? WHERE session_id=?", (_now(), session_id))
            self._record(session_id, "tool.permission.denied", {
                "invocation_id": pending["invocation_id"], "tool": pending["tool"],
                "reason": "legacy or unsupported desktop interaction is disabled",
            })
            return {"status": "blocked", "error": "legacy or unsupported desktop interaction is disabled"}
        else:
            operation = pending["tool"].removeprefix("workspace.")
            self._validate_arguments(operation, pending["arguments"])
            spec = tool_catalog()["operations"].get(operation)
            if spec is None:
                raise ValueError(f"unregistered workspace operation: {operation}")
            TaskPolicy.validate(session.get("mode", "plan"), pending["tool"], spec["permission"])
            if spec["permission"] == "workspace.read":
                raise PermissionError("workspace reads do not require approval")
            result = WorkspaceTools(
                workspace, self._artifact_output_dir(workspace, session_id)
            ).run(operation, pending["arguments"], approved=True)
        self.store.clear_pending_tool(session_id)
        self.store.add_message(session_id, "tool", json.dumps({"invocation_id": pending["invocation_id"], "tool": pending["tool"], "arguments": pending["arguments"], "result": result.data, "status": result.status, "error": result.error}))
        self._record(session_id, "tool.completed" if result.status == "completed" else "tool.failed", {"invocation_id": pending["invocation_id"], "status": result.status, "data": result.data, "error": result.error})
        self.store.add_message(session_id, "system", "The approved tool operation completed. Decide the next step using its actual result.")
        session = self.store.session(session_id)
        try:
            result = self._agent_loop(session_id, session, self.store.messages(session_id)[-1], max_turns=8, max_tools=11)
            if task:
                current = self.store.session(session_id)
                mapped = {"idle": "completed", "cancelled": "cancelled", "awaiting_approval": "waiting_approval", "failed": "failed"}.get(current.get("state"), "failed")
                self.task_store.transition(task.task_id, mapped)
            return result
        finally:
            self._tasks.pop(session_id, None)
