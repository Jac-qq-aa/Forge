"""深度生成会话状态定义。"""

from typing import TypedDict, Literal, Optional, List
from datetime import datetime
import uuid


# 阶段状态枚举
SessionStage = Literal[
    "waiting_input",         # 等待用户填写改写需求
    "generating_outline",    # Agent 正在生成大纲
    "waiting_outline",       # 等待用户确认大纲
    "generating_content",    # Agent 正在生成全文
    "tuning",                # 微调对话阶段
    "completed",             # 已定稿
    "cancelled",             # 用户取消
]


class TuningMessage(TypedDict):
    """微调对话消息"""
    role: Literal["user", "agent"]
    content: str
    timestamp: str


class DeepModeSession(TypedDict):
    """深度生成会话状态"""

    # 基础信息
    session_id: str
    article_id: str              # 关联的原始文章
    created_at: str              # ISO datetime
    updated_at: str              # ISO datetime

    # 阶段状态
    stage: SessionStage

    # Plan-Execute Agent 输出
    profile: dict                # 已废弃，保留空 dict 兼容数据库
    outline: str                 # 大纲文本
    outline_version: int         # 大纲版本号
    draft_v1: str                # 初稿

    # ReAct Agent 输出（增量更新）
    current_draft: str           # 微调后的最新草稿
    tuning_history: List[TuningMessage]

    # 共享数据
    source_article: dict         # 原文章 {title, text, url, ...}
    rag_context: str             # RAG 知识库搜索结果

    # 最终输出
    final_draft: str
    finalized_at: Optional[str]


def create_session_id() -> str:
    """生成唯一会话 ID。"""
    return uuid.uuid4().hex[:12]


def create_initial_session(
    article_id: str,
    source_article: dict,
    profile: Optional[dict] = None
) -> DeepModeSession:
    """创建初始会话状态。"""
    now = datetime.now().isoformat()
    return DeepModeSession(
        session_id=create_session_id(),
        article_id=article_id,
        created_at=now,
        updated_at=now,
        stage="waiting_input",
        profile={},  # 已废弃，保留空 dict
        outline="",
        outline_version=0,
        draft_v1="",
        current_draft="",
        tuning_history=[],
        source_article=source_article,
        rag_context="",
        final_draft="",
        finalized_at=None,
    )