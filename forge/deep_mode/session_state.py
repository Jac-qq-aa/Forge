"""会话状态数据结构定义。"""

from typing import TypedDict, List, Dict, Any, Optional, Literal
from datetime import datetime
import uuid


class SourceArticle(TypedDict):
    """源文章结构。"""
    title: str
    text: str
    url: Optional[str]


class TuningMessage(TypedDict):
    """微调对话消息。"""
    role: Literal["user", "agent"]
    content: str
    is_question: bool
    timestamp: str
    metadata: Optional[Dict[str, Any]]


class OutlineSection(TypedDict, total=False):
    """大纲章节结构。"""
    id: str
    title: str
    keywords: List[str]
    word_count: int
    subsections: List[Dict[str, Any]]


class Outline(TypedDict, total=False):
    """结构化大纲。"""
    sections: List[OutlineSection]
    total_word_count: int
    tone: str
    target_audience: str


class DeepModeSession(TypedDict, total=False):
    """深度生成会话状态。"""
    session_id: str
    source_article: SourceArticle
    user_input: str
    stage: str
    outline: Optional[Outline]
    outline_version: int
    rag_context: Optional[str]
    current_draft: Optional[str]
    tuning_history: List[TuningMessage]
    is_active: bool
    last_heartbeat: Optional[str]
    created_at: str
    updated_at: Optional[str]
    finalized_at: Optional[str]
    final_draft: Optional[str]
    # 兼容旧字段
    article_id: Optional[str]
    draft_v1: Optional[str]
    profile: Optional[Dict[str, Any]]


# Stage 常量
STAGE_PLANNING = "planning"
STAGE_WAITING_INPUT = "waiting_input"
STAGE_GENERATING_OUTLINE = "generating_outline"
STAGE_WAITING_OUTLINE = "waiting_outline"
STAGE_GENERATING_CONTENT = "generating_content"
STAGE_EXECUTING = "executing"
STAGE_TUNING = "tuning"
STAGE_COMPLETED = "completed"
STAGE_CANCELLED = "cancelled"


# SessionStage 兼容类（已废弃，使用字符串常量代替）
class SessionStage:
    """会话阶段枚举（兼容旧代码）。"""
    PLANNING = STAGE_PLANNING
    WAITING_INPUT = STAGE_WAITING_INPUT
    GENERATING_OUTLINE = STAGE_GENERATING_OUTLINE
    WAITING_OUTLINE = STAGE_WAITING_OUTLINE
    GENERATING_CONTENT = STAGE_GENERATING_CONTENT
    EXECUTING = STAGE_EXECUTING
    TUNING = STAGE_TUNING
    COMPLETED = STAGE_COMPLETED
    CANCELLED = STAGE_CANCELLED


def create_session_id() -> str:
    """生成唯一会话 ID。"""
    return str(uuid.uuid4())


def create_initial_session(
    source_article: SourceArticle,
    user_input: str = "",
    session_id: Optional[str] = None
) -> DeepModeSession:
    """创建初始会话状态。"""
    now = datetime.now().isoformat()
    return DeepModeSession(
        session_id=session_id or create_session_id(),
        source_article=source_article,
        user_input=user_input,
        stage=STAGE_PLANNING,
        outline=None,
        outline_version=0,
        rag_context=None,
        current_draft=None,
        tuning_history=[],
        is_active=True,
        last_heartbeat=now,
        created_at=now,
        updated_at=now,
        finalized_at=None,
        final_draft=None,
    )