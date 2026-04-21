"""State definitions for the Forge LangGraph workflow.

This module defines the GraphState TypedDict that flows through all nodes
in the multi-agent workflow.
"""

from typing import TypedDict, Optional, List


class GraphState(TypedDict, total=False):
    """State dictionary that flows through the LangGraph workflow.

    Attributes:
        topic: Initial input topic or URL.
        source_platform: Source platform: "xhs" or "zhihu" (auto-detected or specified).
        target_platform: Target platform: "xhs_video", "zhihu_article", "zhihu_video".
        generate_video: Whether to generate AI video (default False, user must enable).
        raw_content: Raw content scraped from platform (title, text, images, likes, etc).
        article_list: List of articles for user selection (multiple articles mode).
        rewritten_draft: AI-rewritten content draft.
        reflection_feedback: Feedback from reviewer node for revision.
        final_script: Final approved video script and narration.
        video_path: Local file path of generated video.
        publish_status: Publication result (success/failure reason).
        revision_count: Number of rewrite iterations (prevents infinite loops).

        # AI Detection & Humanization (去AI化流程)
        ai_score: Current AI probability score (0.0-1.0).
        humanize_revisions: Number of humanization iterations.
        humanize_feedback: Feedback from AI detector explaining why content seems AI-generated.
        ruibo_feedback: Feedback about Ruibo brand embedding issues.
    """

    # Input
    topic: str
    source_platform: str
    target_platform: str
    generate_video: bool  # 是否生成 AI 视频

    # Scout node output
    raw_content: dict
    article_list: List[dict]  # Multiple articles for user selection

    # Editor node output
    rewritten_draft: str

    # AI Detection & Humanization (去AI化流程)
    ai_score: float               # AI概率得分 (0.0-1.0)
    humanize_revisions: int       # 人性化重写次数
    humanize_feedback: str        # 为什么像AI的反馈
    ruibo_feedback: str           # 锐博品牌嵌入问题反馈

    # Reviewer node output
    reflection_feedback: str
    final_script: str

    # Director node output
    video_path: str
    script_path: str  # Path to saved script txt file

    # Publisher node output
    publish_status: str

    # Control flow
    revision_count: int
    skip_publish: bool  # Dry-run mode: skip actual browser publishing


def create_initial_state(topic: str, target_platform: str = "xhs_video", skip_publish: bool = False) -> GraphState:
    """Create an initial state with topic and target platform.

    Args:
        topic: The input topic or URL to process.
        target_platform: Target publishing platform.
        skip_publish: If True, skip actual browser publishing (dry-run mode).

    Returns:
        Initial GraphState with topic, target_platform, revision_count=0, and skip_publish.
    """
    return GraphState(
        topic=topic,
        target_platform=target_platform,
        revision_count=0,
        ai_score=0.0,
        humanize_revisions=0,
        humanize_feedback="",
        ruibo_feedback="",
        skip_publish=skip_publish,
    )