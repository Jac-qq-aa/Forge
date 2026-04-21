"""LangGraph workflow assembly for the Forge multi-agent pipeline.

This module constructs the StateGraph with all nodes and edges,
implementing the conditional routing logic for the content workflow.

New workflow with AI detection, humanization, and video generation:
Editor → AI_Detector → Humanizer_Editor → AI_Detector (loop) → Reviewer → Director → VideoGenerator → Publisher

LangSmith Integration:
Set LANGCHAIN_API_KEY in .env to enable tracing and visualization.
View traces at https://smith.langchain.com
"""

import logging
import os
from typing import Literal

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

from forge.graph.state import GraphState
from forge.agents.nodes import (
    scout_node,
    editor_node,
    ai_detector_node,
    humanizer_editor_node,
    reviewer_node,
    director_node,
    publisher_node,
    video_generator_node,
)

from forge.config import MAX_HUMANIZE_REVISIONS, AI_THRESHOLD, LANGCHAIN_API_KEY, LANGCHAIN_TRACING_V2, LANGCHAIN_PROJECT

# LangSmith tracing setup
if LANGCHAIN_API_KEY:
    os.environ["LANGCHAIN_API_KEY"] = LANGCHAIN_API_KEY
    os.environ["LANGCHAIN_TRACING_V2"] = LANGCHAIN_TRACING_V2
    os.environ["LANGCHAIN_PROJECT"] = LANGCHAIN_PROJECT
    logging.info(f"[LangSmith] Tracing enabled for project: {LANGCHAIN_PROJECT}")

logger = logging.getLogger(__name__)


def route_after_ai_detector(state: GraphState) -> Literal["humanizer_editor", "reviewer"]:
    """Determine the next node after AI_Detector.

    Routing logic:
    - If ai_score > AI_THRESHOLD AND humanize_revisions < MAX_HUMANIZE_REVISIONS -> humanizer_editor_node
    - If ai_score <= AI_THRESHOLD OR reached max revisions -> reviewer_node

    Args:
        state: Current workflow state.

    Returns:
        Next node name: "humanizer_editor" or "reviewer".
    """
    ai_score = state.get("ai_score", 0.0)
    humanize_revisions = state.get("humanize_revisions", 0)

    logger.info(f"[Router] AI_Detector results: ai_score={ai_score:.2f}, threshold={AI_THRESHOLD}")
    logger.info(f"[Router] Humanize attempts: {humanize_revisions}/{MAX_HUMANIZE_REVISIONS}")

    # Check if needs humanization
    needs_humanization = ai_score > AI_THRESHOLD
    can_continue = humanize_revisions < MAX_HUMANIZE_REVISIONS

    if needs_humanization and can_continue:
        logger.info("[Router] AI score too high, sending to Humanizer")
        return "humanizer_editor"

    # Passed or max attempts reached
    logger.info("[Router] Passed AI detection or max attempts reached → Reviewer")
    return "reviewer"


def route_after_review(state: GraphState) -> Literal["director", "editor"]:
    """Determine the next node after reviewer.

    Routing logic:
    - If needs revision (has feedback and revision_count < 3) -> editor_node
    - If approved (final_script or revision_count >= 3):
      - All platforms go through director_node
      - Director handles text-only for zhihu_article, video for others

    Args:
        state: Current workflow state.

    Returns:
        Next node name: "director" or "editor".
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

    # Approved: all platforms go through director
    # Director will handle text-only for zhihu_article, video for others
    logger.info("[Router] Approved -> routing to director")
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
    graph.add_node("ai_detector", ai_detector_node)
    graph.add_node("humanizer_editor", humanizer_editor_node)
    graph.add_node("reviewer", reviewer_node)
    graph.add_node("director", director_node)
    graph.add_node("video_generator", video_generator_node)
    graph.add_node("publisher", publisher_node)

    # Add edges
    logger.info("[GraphBuilder] Adding edges...")

    # START -> scout
    graph.add_edge(START, "scout")

    # scout -> editor
    graph.add_edge("scout", "editor")

    # editor -> ai_detector (新增)
    graph.add_edge("editor", "ai_detector")

    # ai_detector -> conditional routing (新增)
    graph.add_conditional_edges(
        "ai_detector",
        route_after_ai_detector,
        {
            "humanizer_editor": "humanizer_editor",
            "reviewer": "reviewer",
        }
    )

    # humanizer_editor -> ai_detector (循环)
    graph.add_edge("humanizer_editor", "ai_detector")

    # reviewer -> conditional routing
    graph.add_conditional_edges(
        "reviewer",
        route_after_review,
        {
            "director": "director",
            "editor": "editor",
        }
    )

    # director -> video_generator
    graph.add_edge("director", "video_generator")

    # video_generator -> publisher
    graph.add_edge("video_generator", "publisher")

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