"""LangGraph workflow assembly for the Forge multi-agent pipeline.

This module constructs the StateGraph with all nodes and edges,
implementing the conditional routing logic for the content workflow.
"""

import logging
from typing import Literal

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

from forge.graph.state import GraphState
from forge.agents.nodes import (
    scout_node,
    editor_node,
    reviewer_node,
    director_node,
    publisher_node,
)

logger = logging.getLogger(__name__)


def route_after_review(state: GraphState) -> Literal["director", "publisher", "editor"]:
    """Determine the next node after reviewer.

    Routing logic:
    - If needs revision (has feedback and revision_count < 3) -> editor_node
    - If approved (final_script or revision_count >= 3):
      - zhihu_article target -> publisher_node (skip video generation)
      - other targets -> director_node (generate video first)

    Args:
        state: Current workflow state.

    Returns:
        Next node name: "director", "publisher", or "editor".
    """
    final_script = state.get("final_script", "")
    reflection_feedback = state.get("reflection_feedback", "")
    revision_count = state.get("revision_count", 0)
    target_platform = state.get("target_platform", "xhs_video")

    logger.info(f"[Router] Routing after review - revision_count: {revision_count}")
    logger.info(f"[Router] Has final_script: {bool(final_script)}")
    logger.info(f"[Router] Has feedback: {bool(reflection_feedback)}")
    logger.info(f"[Router] Target platform: {target_platform}")

    # Needs revision: has feedback and under max revisions
    if reflection_feedback and revision_count < 3:
        logger.info("[Router] Needs revision -> routing back to editor")
        return "editor"

    # Approved or force-approved: check target platform
    if target_platform == "zhihu_article":
        # Zhihu articles don't need video generation
        logger.info("[Router] zhihu_article target -> routing to publisher (skip director)")
        return "publisher"
    else:
        # Video platforms need director first
        logger.info("[Router] Video target -> routing to director")
        return "director"


def build_graph() -> StateGraph:
    """Build the LangGraph workflow.

    Returns:
        Compiled StateGraph ready for execution.
    """
    logger.info("[GraphBuilder] Starting graph construction")

    # Create the graph with GraphState
    graph = StateGraph(GraphState)

    # Add nodes
    logger.info("[GraphBuilder] Adding nodes...")
    graph.add_node("scout", scout_node)
    graph.add_node("editor", editor_node)
    graph.add_node("reviewer", reviewer_node)
    graph.add_node("director", director_node)
    graph.add_node("publisher", publisher_node)

    # Add edges
    logger.info("[GraphBuilder] Adding edges...")

    # START -> scout
    graph.add_edge(START, "scout")

    # scout -> editor
    graph.add_edge("scout", "editor")

    # editor -> reviewer
    graph.add_edge("editor", "reviewer")

    # reviewer -> conditional routing
    graph.add_conditional_edges(
        "reviewer",
        route_after_review,
        {
            "director": "director",
            "publisher": "publisher",
            "editor": "editor",
        }
    )

    # director -> publisher
    graph.add_edge("director", "publisher")

    # publisher -> END
    graph.add_edge("publisher", END)

    logger.info("[GraphBuilder] Graph construction completed")

    return graph


def create_workflow() -> CompiledStateGraph:
    """Create and compile the workflow graph.

    Returns:
        Compiled LangGraph application ready for invocation.
    """
    graph = build_graph()
    compiled = graph.compile()
    logger.info("[Workflow] Graph compiled successfully")
    return compiled


# Pre-compiled workflow instance
workflow = create_workflow()


def visualize_graph(workflow: CompiledStateGraph, output_path: str | None = None) -> str:
    """Generate ASCII visualization of the workflow graph.

    Args:
        workflow: Compiled workflow graph.
        output_path: Optional path to save visualization.

    Returns:
        ASCII representation of the graph.
    """
    try:
        # Get the graph structure
        ascii_graph = workflow.get_graph().draw_ascii()

        if output_path:
            with open(output_path, "w") as f:
                f.write(ascii_graph)
            logger.info(f"[Visualize] Graph saved to {output_path}")

        return ascii_graph
    except Exception as e:
        logger.warning(f"[Visualize] Could not generate visualization: {e}")
        return "Graph visualization not available"