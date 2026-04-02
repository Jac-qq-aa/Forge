"""State definitions for the Forge LangGraph workflow.

This module defines the GraphState TypedDict that flows through all nodes
in the multi-agent workflow.
"""

from typing import TypedDict, Optional


class GraphState(TypedDict, total=False):
    """State dictionary that flows through the LangGraph workflow.

    Attributes:
        topic: Initial input topic or URL.
        source_platform: Source platform: "xhs" or "zhihu" (auto-detected or specified).
        target_platform: Target platform: "xhs_video", "zhihu_article", "zhihu_video".
        raw_content: Raw content scraped from platform (title, text, images, likes, etc).
        rewritten_draft: AI-rewritten content draft.
        reflection_feedback: Feedback from reviewer node for revision.
        final_script: Final approved video script and narration.
        video_path: Local file path of generated video.
        publish_status: Publication result (success/failure reason).
        revision_count: Number of rewrite iterations (prevents infinite loops).
    """

    # Input
    topic: str
    source_platform: str
    target_platform: str

    # Scout node output
    raw_content: dict

    # Editor node output
    rewritten_draft: str

    # Reviewer node output
    reflection_feedback: str
    final_script: str

    # Director node output
    video_path: str

    # Publisher node output
    publish_status: str

    # Control flow
    revision_count: int


def create_initial_state(topic: str, target_platform: str = "xhs_video") -> GraphState:
    """Create an initial state with topic and target platform.

    Args:
        topic: The input topic or URL to process.
        target_platform: Target publishing platform.

    Returns:
        Initial GraphState with topic, target_platform, and revision_count=0.
    """
    return GraphState(
        topic=topic,
        target_platform=target_platform,
        revision_count=0,
    )