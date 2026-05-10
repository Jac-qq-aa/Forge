"""State definitions for the Forge LangGraph workflow.

This module defines the GraphState TypedDict that flows through all nodes
in the multi-agent workflow.

UnifiedState merges fast mode (GraphState) and deep mode (DeepModeSession)
into a single state structure for the unified workflow.
"""

from typing import TypedDict, List
from langchain_core.messages import BaseMessage


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


# ============================================================================
# UnifiedState - 融合快速模式和深度模式的状态定义
# ============================================================================

class UnifiedState(TypedDict, total=False):
    """统一状态结构 - 融合快速模式 (GraphState) 和深度模式 (DeepModeSession)。

    通过 mode 字段路由到不同的处理分支：
    - mode="fast": 快速改写流程 (Editor → AI_Detector → Reviewer → Director)
    - mode="deep": 深度生成流程 (Outline → Content → Tuning → Director)

    Attributes:
        # ===== 基础字段（两模式共享） =====
        session_id: 会话 ID，用于持久化和恢复
        mode: 模式选择 "fast" / "deep"

        # 输入
        topic: URL 或主题
        source_platform: 来源平台 zhihu/wechat/manual
        target_platform: 目标平台 xhs_video/zhihu_article/zhihu_video

        # Scout 输出
        raw_content: 包含 title, text, images, source_url 等原始内容

        # ===== 快速模式专用字段 =====
        rewritten_draft: Editor 输出的改写草稿
        ai_score: AI 检测得分 (0.0-1.0)
        humanize_revisions: 人性化迭代次数
        humanize_feedback: AI 检测反馈
        ruibo_feedback: 锐博品牌嵌入问题反馈
        revision_count: Reviewer 迭代次数
        reflection_feedback: Reviewer 反馈
        final_script: 最终脚本
        script_path: 脚本文件路径
        video_path: 视频路径
        skip_publish: 跳过发布

        # ===== 深度模式专用字段 =====
        user_input: 用户改写需求描述
        outline: 大纲内容
        outline_version: 大纲版本号 (最多3次修改)
        rag_context: RAG 知识库检索素材
        current_draft: 当前草稿 (深度模式)
        tuning_messages: 微调对话历史
        human_decision: 用户决策 accept/modify/finalize
        draft_v1: 初版草稿 (用于对比)

        # ===== 控制字段 =====
        stage: 阶段状态 (planning/waiting_outline/executing/tuning/completed)
        generate_video: 是否生成视频
        article_id: 文章 ID (兼容字段)
        profile: 用户配置 (兼容字段)
    """

    # ===== 基础字段 =====
    session_id: str                    # 会话 ID（用于持久化）
    mode: str                          # "fast" / "deep"

    # 输入
    topic: str                         # URL 或主题
    source_platform: str               # zhihu/wechat/manual
    target_platform: str               # xhs_video/zhihu_article/zhihu_video

    # Scout 输出
    raw_content: dict                  # {title, text, images, source_url}

    # ===== 快速模式字段 =====
    rewritten_draft: str
    ai_score: float
    humanize_revisions: int
    humanize_feedback: str
    ruibo_feedback: str
    revision_count: int
    reflection_feedback: str
    final_script: str
    script_path: str
    video_path: str
    skip_publish: bool

    # ===== 深度模式字段 =====
    user_input: str                    # 用户改写需求
    outline: str                       # 大纲内容
    outline_version: int               # 大纲版本号
    rag_context: str                   # RAG 知识库素材

    # ===== Research 阶段（新增） =====
    fact_sheet: str                    # Research Agent 输出的事实清单

    # ===== Reflection 阶段（新增） =====
    critique: str                       # Critic 反馈
    reflection_revision_count: int      # Reflection 循环次数（区别于快速模式的 revision_count）
    is_approved: bool                   # 是否通过 Critic 审查

    current_draft: str                 # 当前草稿
    tuning_messages: List[BaseMessage] # 微调对话历史
    human_decision: str                # 用户决策: accept/modify/finalize
    draft_v1: str                      # 初版草稿

    # ===== 控制字段 =====
    stage: str                         # 阶段状态
    generate_video: bool               # 是否生成视频
    article_id: str                    # 兼容字段
    profile: dict                      # 兼容字段


# ============================================================================
# Stage 常量（深度模式阶段）
# ============================================================================

STAGE_PLANNING = "planning"
STAGE_WAITING_OUTLINE = "waiting_outline"
STAGE_OUTLINE_REVISION = "outline_revision"
STAGE_EXECUTING = "executing"
STAGE_TUNING = "tuning"
STAGE_WAITING_FINALIZE = "waiting_finalize"
STAGE_COMPLETED = "completed"
STAGE_CANCELLED = "cancelled"


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


def create_unified_state(
    mode: str = "fast",
    topic: str = "",
    session_id: str = None,
    target_platform: str = "xhs_video",
    source_platform: str = "",
    raw_content: dict = None,
    user_input: str = "",
    generate_video: bool = False,
) -> UnifiedState:
    """创建统一状态的初始值。

    Args:
        mode: 模式选择 "fast" 或 "deep"
        topic: URL 或主题
        session_id: 会话 ID（可选，不提供时自动生成）
        target_platform: 目标平台
        source_platform: 来源平台
        raw_content: 原始内容（可选）
        user_input: 用户改写需求（深度模式）
        generate_video: 是否生成视频

    Returns:
        初始化的 UnifiedState
    """
    import uuid

    return UnifiedState(
        session_id=session_id or str(uuid.uuid4()),
        mode=mode,
        topic=topic,
        target_platform=target_platform,
        source_platform=source_platform,
        raw_content=raw_content or {},
        user_input=user_input,
        generate_video=generate_video,
        revision_count=0,
        ai_score=0.0,
        humanize_revisions=0,
        outline_version=0,
        stage=STAGE_PLANNING,
        skip_publish=True,  # 默认跳过发布
    )