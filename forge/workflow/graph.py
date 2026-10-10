from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from forge.workflow.nodes import WorkflowNodes
from forge.workflow.state import WorkflowState
from forge.infrastructure.telemetry import observed_node


def build_workflow(nodes: WorkflowNodes, *, recorder=None):
    graph = StateGraph(WorkflowState)
    for name in (
        "intent", "planner", "fact_approval", "outline", "outline_approval", "approval", "validate_user_facts",
        "plan_conflict_decision", "generator", "critic", "revise",
        "conflict_decision", "answer", "clarify",
    ):
        graph.add_node(name, observed_node(getattr(nodes, name), recorder))

    graph.add_edge(START, "intent")
    graph.add_conditional_edges(
        "intent",
        lambda state: state["intent"],
        {"generate": "planner", "modify": "validate_user_facts", "question": "answer", "clarify": "clarify"},
    )
    graph.add_edge("planner", "fact_approval")
    graph.add_edge("fact_approval", "validate_user_facts")
    graph.add_edge("outline", "outline_approval")
    graph.add_edge("outline_approval", "approval")
    graph.add_conditional_edges(
        "approval",
        lambda state: (
            "changed" if not state.get("approval")
            else "existing" if state.get("draft")
            else "new"
        ),
        {"new": "generator", "existing": "revise", "changed": "validate_user_facts"},
    )
    graph.add_conditional_edges(
        "validate_user_facts",
        lambda state: (
            "error" if state.get("error")
            else "conflict" if state.get("pending_conflicts")
            else "confirmed" if state.get("facts_confirmed") else "confirm"
        ),
        {"conflict": "plan_conflict_decision", "confirm": "fact_approval", "confirmed": "outline", "error": END},
    )
    graph.add_edge("plan_conflict_decision", "validate_user_facts")
    graph.add_edge("generator", "critic")
    graph.add_conditional_edges(
        "critic",
        lambda state: state["critic_status"],
        {
            "pass": END,
            "fail": "revise",
            "needs_user": "conflict_decision",
            "incomplete": END,
            "stopped": END,
        },
    )
    graph.add_edge("revise", "critic")
    graph.add_conditional_edges(
        "conflict_decision", lambda state: state["critic_status"],
        {"fail": "revise", "stopped": END, "pass": END},
    )
    graph.add_edge("answer", END)
    graph.add_edge("clarify", END)
    return graph
