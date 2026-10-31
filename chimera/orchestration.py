"""Small provider-independent orchestration graph used by Chimera.

This is intentionally a graph primitive, not a second execution engine.  Nodes
are pure orchestration boundaries; existing AgentRuntime, stores, permissions,
and tools remain authoritative for side effects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, TypedDict


class AgentGraphState(TypedDict, total=False):
    session_id: str
    turn_id: str
    objective: str
    workspace_id: str | None
    selected_model: str | None
    agent_mode: str
    task_context: dict
    capabilities: list[dict]
    interaction_inventory: list[dict]
    selected_strategy: dict | None
    current_plan: dict | None
    current_action: dict | None
    approval_state: str
    operation_results: list[dict]
    observations: list[dict]
    verification_results: list[dict]
    failure_history: list[dict]
    retry_budget: int
    token_budget: int | None
    elapsed_time_ms: int
    cancellation_state: str
    final_response: str | None
    next_node: str | None


Node = Callable[[AgentGraphState], dict[str, Any] | None]
Route = Callable[[AgentGraphState], str]


@dataclass
class GraphCheckpoint:
    step: int
    node: str
    state: AgentGraphState


class AgentGraph:
    """Deterministic conditional graph with bounded cycles and checkpoints."""
    def __init__(self, nodes: dict[str, Node], edges: dict[str, str | Route], start: str, terminal: set[str] | None = None):
        self.nodes = dict(nodes)
        self.edges = dict(edges)
        self.start = start
        self.terminal = terminal or {"finish", "failed", "cancelled"}
        if start not in self.nodes: raise ValueError("graph start node is not registered")

    def run(self, initial: AgentGraphState, *, max_steps: int = 32, cancel: Callable[[], bool] | None = None, on_checkpoint: Callable[[GraphCheckpoint], None] | None = None) -> AgentGraphState:
        state: AgentGraphState = dict(initial)
        node = self.start
        for step in range(max(1, max_steps)):
            if cancel and cancel():
                state["cancellation_state"] = "requested"
                state["next_node"] = "cancelled"
                return state
            if node in self.terminal:
                state["next_node"] = node
                return state
            action = self.nodes.get(node)
            if action is None: raise ValueError(f"graph node is not registered: {node}")
            update = action(state) or {}
            state.update(update)
            checkpoint = GraphCheckpoint(step, node, dict(state))
            if on_checkpoint: on_checkpoint(checkpoint)
            edge = self.edges.get(node)
            node = edge(state) if callable(edge) else edge
            if not node: raise ValueError(f"graph edge is missing after node: {checkpoint.node}")
        state["next_node"] = "failed"
        state.setdefault("failure_history", []).append({"reason": "graph step limit reached", "max_steps": max_steps})
        return state


def default_agent_graph() -> AgentGraph:
    """Reference lifecycle graph; side effects are injected by AgentRuntime."""
    def classify(state): return {"next_node": "classify"}
    def classify_node(state): return {"agent_mode": state.get("agent_mode", "plan")}
    def decide(state): return {"next_node": "decide"}
    def route(state):
        if state.get("cancellation_state") == "requested": return "cancelled"
        if state.get("approval_state") == "pending": return "approval"
        if state.get("current_action"): return "execute"
        return "finish"
    def execute(state): return {"next_node": "observe"}
    def observe(state): return {"next_node": "verify"}
    def verify(state): return {"next_node": "decide" if state.get("retry_budget", 0) > 0 else "finish"}
    def approval(state): return {"next_node": "approval"}
    return AgentGraph(
        {"classify": classify_node, "decide": decide, "execute": execute, "observe": observe, "verify": verify, "approval": approval},
        {"classify": "decide", "decide": route, "execute": "observe", "observe": "verify", "verify": lambda s: "decide" if s.get("retry_budget", 0) > 0 else "finish", "approval": lambda s: "approval"},
        "classify",
    )
