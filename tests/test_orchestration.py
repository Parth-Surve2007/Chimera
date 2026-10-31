from chimera.orchestration import AgentGraph, default_agent_graph

def test_graph_routes_conditional_cycle_and_checkpoints():
    seen = []
    graph = AgentGraph(
        {"start": lambda s: {"current_action": {"id": "x"}}, "execute": lambda s: {"operation_results": [{"status": "completed"}]}, "finish": lambda s: {}},
        {"start": "execute", "execute": lambda s: "finish"}, "start", terminal={"finish"},
    )
    result = graph.run({}, on_checkpoint=seen.append)
    assert result["next_node"] == "finish" and len(seen) == 2

def test_graph_cancellation_is_fail_closed():
    result = default_agent_graph().run({}, cancel=lambda: True)
    assert result["cancellation_state"] == "requested"
    assert result["next_node"] == "cancelled"

def test_graph_bounds_cycles():
    graph = AgentGraph({"loop": lambda s: {}}, {"loop": "loop"}, "loop", terminal=set())
    result = graph.run({}, max_steps=2)
    assert result["next_node"] == "failed"
