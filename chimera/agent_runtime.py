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

    @staticmethod
    def _event(db, session_id: str, event_type: str, payload: dict) -> None:
        next_sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM agent_events WHERE session_id=?", (session_id,)).fetchone()[0]
        db.execute("INSERT INTO agent_events VALUES (?,?,?,?,?,?)", (uuid.uuid4().hex, session_id, event_type, next_sequence, json.dumps(payload, separators=(",", ":")), _now()))


class AgentRuntime:
    """Conversation orchestration boundary; provider failures remain truthful."""

    def __init__(self, store: AgentSessionStore, provider):
        self.store, self.provider = store, provider
        self._listeners = {}
        self._listener_lock = threading.Lock()
        self._cancelled = set()
        self._cancel_events = {}
        self.computer = ComputerSessionManager()
        self._computer_sessions = {}

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
        with self._listener_lock: listeners = tuple(self._listeners.get(session_id, ()))
        for callback in listeners:
            try: callback(event)
            except Exception: pass

    def create_session(self, workspace: Path | None = None, mode: str = "plan") -> str:
        model = str(getattr(getattr(self.provider, "config", None), "model_path", "")) or None
        return self.store.create_session(workspace, model, mode)

    def _protocol_catalog(self):
        aliases = {("workspace." + name if "." not in name else name): name for name in tool_catalog()["operations"]}
        context = []
        try:
            for app in self.computer.backend.list_applications()[:20]: context.append({"pid": app["pid"], "executable": app.get("executable"), "windows": app.get("windows", [])[:6]})
        except Exception: pass
        return {"version": 1, "tools": [{"id": key, **tool_catalog()["operations"][value]} for key, value in aliases.items()], "computer_observation": context, "interaction_inventory": self.computer.interaction_inventory()}

    @staticmethod
    def _decision(value):
        if not isinstance(value, dict) or value.get("type") not in {"message", "tool_call"}:
            raise ValueError("agent response must be a message or tool_call object")
        if value["type"] == "message":
            if not isinstance(value.get("content"), str) or not value["content"].strip(): raise ValueError("message content must be non-empty")
            return value
        if not isinstance(value.get("tool"), str) or not (value["tool"].startswith("workspace.") or value["tool"].startswith("computer.")): raise ValueError("tool identifier is invalid")
        if not isinstance(value.get("arguments"), dict): raise ValueError("tool arguments must be an object")
        invocation = value.get("invocation_id")
        if invocation is not None and (not isinstance(invocation, str) or not invocation.strip()): raise ValueError("tool invocation_id is invalid")
        return value

    @staticmethod
    def _validate_arguments(operation, arguments):
        contracts = {
            "list_files": {"relative": str, "recursive": bool},
            "inspect_file": {"path": str},
            "search_files": {"pattern": str},
            "search_text": {"query": str},
            "read_text": {"path": str},
            "create_artifact": {"name": str, "content": str},
            "computer.list_applications": {},
            "computer.list_windows": {},
            "computer.inspect_controls": {"hwnd": int},
            "computer.inspect_elements": {"session_id": str},
            "computer.focus": {"hwnd": int},
            "computer.focus_window": {"hwnd": int},
            "computer.launch": {"executable": str},
            "computer.type_text": {"hwnd": int, "text": str},
            "computer.screenshot": {"hwnd": int, "path": str},
            "computer.close": {"hwnd": int},
            "computer.inspect_window": {"session_id": str},
            "computer.inspect_pattern_support": {"session_id": str, "reference_id": str},
            "computer.invoke_element": {"session_id": str, "reference_id": str},
            "computer.set_value": {"session_id": str, "reference_id": str, "value": str},
            "computer.read_text": {"session_id": str, "reference_id": str},
            "computer.focus_element": {"session_id": str, "reference_id": str},
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

    def _decide(self, messages, session, cancel_event=None, turn_id=None):
        if not hasattr(self.provider, "decide"):
            return None
        kwargs = {"session": session, "cancel_event": cancel_event}
        accepted = inspect.signature(self.provider.decide).parameters
        return self._decision(self.provider.decide(messages, self._protocol_catalog(), **{key: value for key, value in kwargs.items() if key in accepted}))

    def send(self, session_id: str, content: str) -> dict:
        session = self.store.session(session_id)
        if not session:
            raise KeyError("agent session not found")
        message = self.store.add_message(session_id, "user", content)
        cancel_event = threading.Event(); self._cancel_events[session_id] = cancel_event
        with sqlite3.connect(self.store.db_path) as db: db.execute("UPDATE agent_sessions SET state='reasoning',updated_at=? WHERE session_id=?", (_now(), session_id))
        self._record(session_id, "turn.started", {"message_id": message.message_id})
        try:
            if hasattr(self.provider, "decide"):
                return self._agent_loop(session_id, session, message, cancel_event=cancel_event)
            kwargs = {"session": session, "cancel_event": cancel_event, "on_delta": lambda text: self._record(session_id, "assistant.text.delta", {"text": text})}
            accepted = inspect.signature(self.provider.converse).parameters
            response = self.provider.converse(self.store.messages(session_id), **{key: value for key, value in kwargs.items() if key in accepted})
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

    def _agent_loop(self, session_id, session, message, approved_invocation=None, max_turns=8, max_tools=12, cancel_event=None):
        turn_id = uuid.uuid4().hex
        workspace = Path(session["workspace"]) if session.get("workspace") else None
        if workspace is None or not workspace.is_dir(): raise ValueError("a selected workspace is required for tool use")
        output = workspace / ".chimera-agent-artifacts" / session_id
        tools = WorkspaceTools(workspace, output)
        for _ in range(max_turns):
            if session_id in self._cancelled:
                self._cancelled.discard(session_id)
                return {"status": "cancelled"}
            self._record(session_id, "model.request.started", {"turn_id": turn_id})
            decision = self._decide(self.store.messages(session_id), session, cancel_event, turn_id)
            self._record(session_id, "model.request.completed", {"turn_id": turn_id, "decision_type": decision["type"]})
            if decision["type"] == "message":
                assistant = self.store.add_message(session_id, "assistant", decision["content"])
                with sqlite3.connect(self.store.db_path) as db: db.execute("UPDATE agent_sessions SET state='idle',updated_at=? WHERE session_id=?", (_now(), session_id))
                self._record(session_id, "turn.completed", {"turn_id": turn_id})
                return {"status": "completed", "message": assistant, "tools": []}
            if max_tools <= 0: raise RuntimeError("maximum tool-call limit reached")
            tool_id = decision["tool"]; operation = tool_id.removeprefix("workspace.") if tool_id.startswith("workspace.") else tool_id
            if operation not in tool_catalog()["operations"]: raise ValueError(f"unregistered tool: {tool_id}")
            self._validate_arguments(operation, decision["arguments"])
            invocation = decision.get("invocation_id") or uuid.uuid4().hex
            self._record(session_id, "tool.call.requested", {"turn_id": turn_id, "invocation_id": invocation, "tool": tool_id, "arguments": decision["arguments"]})
            permission = tool_catalog()["operations"][operation]["permission"]
            if permission not in {"workspace.read", "computer.observe"}:
                self.store.set_pending_tool(session_id, turn_id, invocation, tool_id, decision["arguments"])
                with sqlite3.connect(self.store.db_path) as db: db.execute("UPDATE agent_sessions SET state='awaiting_approval',updated_at=? WHERE session_id=?", (_now(), session_id))
                self._record(session_id, "tool.permission.required", {"turn_id": turn_id, "invocation_id": invocation, "tool": tool_id, "arguments": decision["arguments"]})
                return {"status": "awaiting_approval", "tool": tool_id, "invocation_id": invocation, "arguments": decision["arguments"]}
            self._record(session_id, "tool.started", {"turn_id": turn_id, "invocation_id": invocation, "tool": tool_id, "arguments": decision["arguments"]})
            if session_id in self._cancelled:
                self._cancelled.discard(session_id)
                return {"status": "cancelled"}
            if tool_id.startswith("computer."):
                computer = WindowsComputerUse(workspace)
                if operation in {"computer.list_applications", "computer.list_windows"}:
                    result_data = []
                    for app in computer.list_applications():
                        identity = self.computer.create(app["pid"], app.get("executable")); self._computer_sessions[identity.session_id] = identity; app["computer_session_id"] = identity.session_id; result_data.append(app)
                    result_status, result_error = "completed", None
                elif operation == "computer.inspect_controls": result_data = computer.controls(decision["arguments"]["hwnd"]); result_status, result_error = "completed", None
                elif operation == "computer.inspect_elements":
                    observation = self.computer.observe(decision["arguments"]["session_id"]); result_data = {"observation_id": observation.observation_id, "session_id": observation.session_id, "generation": observation.generation, "backend": observation.backend, "elements": observation.elements[:250]}; result_status, result_error = "completed", None
                elif operation == "computer.inspect_window":
                    observation = self.computer.observe(decision["arguments"]["session_id"]); result_data = {"session_id": observation.session_id, "windows": observation.windows, "backend": observation.backend}; result_status, result_error = "completed", None
                elif operation == "computer.inspect_pattern_support":
                    reference = self.computer.references.get(decision["arguments"]["reference_id"]); result_data = {"patterns": [], "status": "failed" if not reference else "completed"};
                    if reference:
                        observation = self.computer.observe(reference.session_id); result_data["patterns"] = next((e.get("patterns", []) for e in observation.elements if e.get("reference_id") == decision["arguments"]["reference_id"]), [])
                    result_status, result_error = result_data["status"], None if result_data["status"] == "completed" else "unknown reference"
                elif operation in {"computer.invoke_element", "computer.set_value", "computer.read_text", "computer.focus_element", "computer.toggle_element", "computer.select_element", "computer.expand_element", "computer.collapse_element"}:
                    action = operation.removeprefix("computer."); value = decision["arguments"].get("value")
                    strategy = self.computer.choose_interaction(action, evidence={"semantic_control": True})
                    result_data = self.computer.act_reference(decision["arguments"]["session_id"], decision["arguments"]["reference_id"], action, value, approved=True)
                    result_data["strategy"] = strategy
                    result_status, result_error = result_data.get("status"), result_data.get("reason")
                elif operation in {"computer.focus", "computer.focus_window"}: result_data = computer.focus(decision["arguments"]["hwnd"]); result_status, result_error = result_data.get("status"), result_data.get("reason")
                elif operation == "computer.focus": result_data = computer.focus(decision["arguments"]["hwnd"]); result_status, result_error = result_data.get("status"), result_data.get("reason")
                else: result_data, result_status, result_error = {}, "failed", "unsupported computer operation"
                result = type("ComputerResult", (), {"data": {"items": result_data}, "status": result_status, "error": result_error})()
            else:
                result = tools.run(operation, decision["arguments"])
            self.store.add_message(session_id, "tool", json.dumps({"invocation_id": invocation, "tool": tool_id, "result": result.data, "status": result.status, "error": result.error}, separators=(",", ":")))
            self._record(session_id, "tool.completed" if result.status == "completed" else "tool.failed", {"turn_id": turn_id, "invocation_id": invocation, "tool": tool_id, "status": result.status, "data": result.data, "error": result.error})
            max_tools -= 1
        raise RuntimeError("maximum agent-turn limit reached")

    def approve_pending(self, session_id: str, approve: bool) -> dict:
        pending = self.store.pending_tool(session_id)
        if not pending: raise ValueError("no pending tool approval")
        session = self.store.session(session_id)
        if not session or session["state"] != "awaiting_approval": raise ValueError("session is not awaiting approval")
        if not approve:
            self.store.clear_pending_tool(session_id)
            self.store.add_message(session_id, "tool", json.dumps({"invocation_id": pending["invocation_id"], "status": "denied", "error": "user denied approval"}))
            with sqlite3.connect(self.store.db_path) as db: db.execute("UPDATE agent_sessions SET state='idle',updated_at=? WHERE session_id=?", (_now(), session_id))
            self._record(session_id, "tool.permission.denied", {"invocation_id": pending["invocation_id"]})
            return {"status": "denied"}
        workspace = Path(session["workspace"])
        if pending["tool"].startswith("computer.") and pending["tool"] in {"computer.invoke_element", "computer.set_value", "computer.toggle_element", "computer.select_element", "computer.expand_element", "computer.collapse_element"}:
            operation = pending["tool"].removeprefix("computer."); args = pending["arguments"]; result_data = self.computer.act_reference(args["session_id"], args["reference_id"], operation, args.get("value"), approved=True); result = type("ComputerResult", (), {"data": result_data, "status": result_data.get("status", "failed"), "error": result_data.get("reason")})()
        elif pending["tool"] == "computer.launch":
            result_data = WindowsComputerUse(workspace).launch(pending["arguments"]["executable"], approved=True); result = type("ComputerResult", (), {"data": result_data, "status": result_data.get("status", "failed"), "error": result_data.get("reason")})()
        elif pending["tool"] == "computer.type_text":
            result_data = WindowsComputerUse(workspace).type_text(pending["arguments"]["hwnd"], pending["arguments"]["text"], approved=True); result = type("ComputerResult", (), {"data": result_data, "status": result_data.get("status", "failed"), "error": result_data.get("reason")})()
        elif pending["tool"] == "computer.screenshot":
            result_data = WindowsComputerUse(workspace).screenshot(pending["arguments"]["hwnd"], Path(pending["arguments"]["path"]), approved=True); result = type("ComputerResult", (), {"data": result_data, "status": result_data.get("status", "failed"), "error": result_data.get("reason")})()
        elif pending["tool"] == "computer.close":
            result_data = WindowsComputerUse(workspace).close(pending["arguments"]["hwnd"], approved=True); result = type("ComputerResult", (), {"data": result_data, "status": result_data.get("status", "failed"), "error": result_data.get("reason")})()
        else:
            result = WorkspaceTools(workspace, workspace / ".chimera-agent-artifacts" / session_id).run(pending["tool"].removeprefix("workspace."), pending["arguments"], approved=True)
        self.store.clear_pending_tool(session_id)
        self.store.add_message(session_id, "tool", json.dumps({"invocation_id": pending["invocation_id"], "tool": pending["tool"], "result": result.data, "status": result.status, "error": result.error}))
        self._record(session_id, "tool.completed" if result.status == "completed" else "tool.failed", {"invocation_id": pending["invocation_id"], "status": result.status, "data": result.data, "error": result.error})
        self.store.add_message(session_id, "system", "The approved tool operation completed. Decide the next step using its actual result.")
        session = self.store.session(session_id)
        return self._agent_loop(session_id, session, self.store.messages(session_id)[-1], max_turns=8, max_tools=11)
