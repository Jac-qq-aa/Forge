# 深度生成模式 Phase 1 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现深度生成模式基础设施 + 核心生成逻辑（Session Manager、Plan-Execute Agent、工具、REST API），产出最小可用产品：用户可填写画像表单、生成大纲、确认大纲、生成全文。

**Architecture:** Session Manager 使用 SQLite 存储，Plan-Execute Agent 使用 LangChain Agent + Tools，API 使用 FastAPI REST 端点。后续 Phase 2 添加 WebSocket + ReAct Agent 实现微调对话。

**Tech Stack:** LangChain (Agent + Tools)、SQLite (aiosqlite)、FastAPI、Pydantic、复用现有 LLMClient 和 KnowledgeBase

---

## Phase 1 文件结构

```
forge/
├── deep_mode/                    # 新增模块
│   ├── __init__.py
│   ├── session_state.py          # Session State 定义
│   ├── session_manager.py        # Session Manager（SQLite CRUD）
│   ├── errors.py                 # 异常定义
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   └── plan_execute_agent.py # Plan-Execute Agent
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── profile_extractor.py
│   │   ├── rag_search.py
│   │   ├── outline_generator.py
│   │   └── content_generator.py
│
├── config.py                     # 修改：新增配置项
│
├── web/
│   ├── app.py                    # 修改：新增 API 端点
│
└── sessions.db                   # SQLite 数据库（运行时生成）
```

---

## Task 1: Session State 定义

**Files:**
- Create: `forge/deep_mode/__init__.py`
- Create: `forge/deep_mode/session_state.py`

- [ ] **Step 1: 创建模块入口文件**

```python
# forge/deep_mode/__init__.py

"""深度生成模式模块 - 多智能体协作内容生成系统。"""

from forge.deep_mode.session_state import ProfileInfo, DeepModeSession, SessionStage
from forge.deep_mode.errors import DeepModeError, SessionNotFoundError, InvalidStageError

__all__ = [
    "ProfileInfo",
    "DeepModeSession",
    "SessionStage",
    "DeepModeError",
    "SessionNotFoundError",
    "InvalidStageError",
]
```

- [ ] **Step 2: 创建 Session State 定义**

```python
# forge/deep_mode/session_state.py

"""深度生成会话状态定义。"""

from typing import TypedDict, Literal, Optional, List
from datetime import datetime
import uuid


# 阶段状态枚举
SessionStage = Literal[
    "waiting_profile",       # 等待用户填写画像表单
    "generating_outline",    # Agent 正在生成大纲
    "waiting_outline",       # 等待用户确认大纲
    "generating_content",    # Agent 正在生成全文
    "tuning",                # 微调对话阶段（Phase 2）
    "completed",             # 已定稿
    "cancelled",             # 用户取消
]


class ProfileInfo(TypedDict, total=False):
    """用户画像"""
    tone: str              # 语气风格：幽默、专业、轻松、犀利...
    target_audience: str   # 目标读者：职场新人、HR从业者、管理者...
    focus_point: str       # 侧重点：实用工具、理论分析、案例故事...
    length_preference: str # 篇幅偏好：简洁、中等、深度...
    special_request: str   # 用户特殊要求（自由文本）
    target_platform: str   # 目标平台：zhihu_article, xhs_video...


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

    # Plan-Execute Agent 输出（单向写入）
    profile: ProfileInfo
    outline: str                 # 大纲文本
    outline_version: int         # 大纲版本号
    draft_v1: str                # 初稿

    # ReAct Agent 输出（增量更新，Phase 2）
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
    profile: ProfileInfo = None
) -> DeepModeSession:
    """创建初始会话状态。"""
    now = datetime.now().isoformat()
    return DeepModeSession(
        session_id=create_session_id(),
        article_id=article_id,
        created_at=now,
        updated_at=now,
        stage="waiting_profile",
        profile=profile or ProfileInfo(),
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
```

- [ ] **Step 3: Commit**

```bash
git add forge/deep_mode/__init__.py forge/deep_mode/session_state.py
git commit -m "feat(deep_mode): add session state definitions"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Task 2: 异常定义

**Files:**
- Create: `forge/deep_mode/errors.py`

- [ ] **Step 1: 创建异常定义文件**

```python
# forge/deep_mode/errors.py

"""深度生成模式异常定义。"""


class DeepModeError(Exception):
    """深度生成模式基础异常。"""
    pass


class SessionNotFoundError(DeepModeError):
    """会话不存在。"""
    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class InvalidStageError(DeepModeError):
    """操作与当前阶段不匹配。"""
    def __init__(self, current_stage: str, expected_stage: str):
        self.current_stage = current_stage
        self.expected_stage = expected_stage
        super().__init__(f"Invalid stage: current={current_stage}, expected={expected_stage}")


class OutlineRevisionLimitError(DeepModeError):
    """大纲修改次数已达上限。"""
    def __init__(self, max_revisions: int):
        self.max_revisions = max_revisions
        super().__init__(f"Outline revision limit reached: {max_revisions}")


class AgentTimeoutError(DeepModeError):
    """Agent 执行超时。"""
    def __init__(self, timeout_seconds: int):
        self.timeout_seconds = timeout_seconds
        super().__init__(f"Agent execution timeout: {timeout_seconds}s")


class RAGSearchFailedError(DeepModeError):
    """知识库搜索失败。"""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"RAG search failed: {reason}")
```

- [ ] **Step 2: Commit**

```bash
git add forge/deep_mode/errors.py
git commit -m "feat(deep_mode): add error definitions"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Task 3: Session Manager (SQLite)

**Files:**
- Create: `forge/deep_mode/session_manager.py`

- [ ] **Step 1: 创建 Session Manager**

```python
# forge/deep_mode/session_manager.py

"""Session Manager - SQLite 存储和状态注入。"""

import json
import logging
import os
import asyncio
from datetime import datetime
from typing import Optional, List
import aiosqlite

from forge.deep_mode.session_state import (
    DeepModeSession,
    ProfileInfo,
    SessionStage,
    create_session_id,
    create_initial_session,
)
from forge.deep_mode.errors import SessionNotFoundError, OutlineRevisionLimitError
from forge.config import DEEP_MODE_SESSION_TTL, OUTLINE_MAX_REVISIONS

logger = logging.getLogger(__name__)

# SQLite 数据库路径
SESSION_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "sessions.db")


class SessionManager:
    """深度生成会话管理器。"""

    def __init__(self, db_path: str = SESSION_DB_PATH):
        self.db_path = db_path
        self._initialized = False

    async def _ensure_db(self):
        """确保数据库和表已创建。"""
        if self._initialized:
            return

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS deep_mode_sessions (
                    session_id TEXT PRIMARY KEY,
                    article_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    outline TEXT NOT NULL,
                    outline_version INTEGER NOT NULL,
                    draft_v1 TEXT NOT NULL,
                    current_draft TEXT NOT NULL,
                    tuning_history TEXT NOT NULL,
                    source_article TEXT NOT NULL,
                    rag_context TEXT NOT NULL,
                    final_draft TEXT NOT NULL,
                    finalized_at TEXT
                )
            """)
            await db.commit()

        self._initialized = True
        logger.info(f"[SessionManager] Database initialized: {self.db_path}")

    async def create_session(
        self,
        article_id: str,
        source_article: dict,
        profile: ProfileInfo = None
    ) -> DeepModeSession:
        """创建新会话。"""
        await self._ensure_db()

        session = create_initial_session(article_id, source_article, profile)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO deep_mode_sessions (
                    session_id, article_id, created_at, updated_at, stage,
                    profile, outline, outline_version, draft_v1, current_draft,
                    tuning_history, source_article, rag_context, final_draft, finalized_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session["session_id"],
                session["article_id"],
                session["created_at"],
                session["updated_at"],
                session["stage"],
                json.dumps(session["profile"]),
                session["outline"],
                session["outline_version"],
                session["draft_v1"],
                session["current_draft"],
                json.dumps(session["tuning_history"]),
                json.dumps(session["source_article"]),
                session["rag_context"],
                session["final_draft"],
                session["finalized_at"],
            ))
            await db.commit()

        logger.info(f"[SessionManager] Session created: {session['session_id']}")
        return session

    async def load_session(self, session_id: str) -> DeepModeSession:
        """加载会话。"""
        await self._ensure_db()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM deep_mode_sessions WHERE session_id = ?",
                (session_id,)
            )
            row = await cursor.fetchone()

            if row is None:
                raise SessionNotFoundError(session_id)

            return DeepModeSession(
                session_id=row["session_id"],
                article_id=row["article_id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                stage=row["stage"],
                profile=json.loads(row["profile"]),
                outline=row["outline"],
                outline_version=row["outline_version"],
                draft_v1=row["draft_v1"],
                current_draft=row["current_draft"],
                tuning_history=json.loads(row["tuning_history"]),
                source_article=json.loads(row["source_article"]),
                rag_context=row["rag_context"],
                final_draft=row["final_draft"],
                finalized_at=row["finalized_at"],
            )

    async def update_session(self, session_id: str, **updates) -> DeepModeSession:
        """更新会话字段。"""
        await self._ensure_db()

        session = await self.load_session(session_id)

        # 更新字段
        for key, value in updates.items():
            if key in session:
                session[key] = value

        session["updated_at"] = datetime.now().isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE deep_mode_sessions SET
                    updated_at = ?, stage = ?, profile = ?, outline = ?,
                    outline_version = ?, draft_v1 = ?, current_draft = ?,
                    tuning_history = ?, rag_context = ?, final_draft = ?, finalized_at = ?
                WHERE session_id = ?
            """, (
                session["updated_at"],
                session["stage"],
                json.dumps(session["profile"]),
                session["outline"],
                session["outline_version"],
                session["draft_v1"],
                session["current_draft"],
                json.dumps(session["tuning_history"]),
                session["rag_context"],
                session["final_draft"],
                session["finalized_at"],
                session_id,
            ))
            await db.commit()

        logger.info(f"[SessionManager] Session updated: {session_id}, stage={session['stage']}")
        return session

    async def update_stage(self, session_id: str, stage: SessionStage) -> DeepModeSession:
        """更新会话阶段。"""
        return await self.update_session(session_id, stage=stage)

    async def increment_outline_version(self, session_id: str) -> int:
        """增加大纲版本号，检查上限。"""
        session = await self.load_session(session_id)

        if session["outline_version"] >= OUTLINE_MAX_REVISIONS:
            raise OutlineRevisionLimitError(OUTLINE_MAX_REVISIONS)

        new_version = session["outline_version"] + 1
        await self.update_session(session_id, outline_version=new_version)
        return new_version

    async def finalize_session(self, session_id: str) -> DeepModeSession:
        """定稿会话。"""
        session = await self.load_session(session_id)
        now = datetime.now().isoformat()

        return await self.update_session(
            session_id,
            stage="completed",
            final_draft=session["current_draft"] or session["draft_v1"],
            finalized_at=now,
        )

    async def cancel_session(self, session_id: str) -> DeepModeSession:
        """取消会话。"""
        return await self.update_session(session_id, stage="cancelled")

    async def list_sessions(self, article_id: str = None, stage: str = None) -> List[DeepModeSession]:
        """列出会话。"""
        await self._ensure_db()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            if article_id and stage:
                cursor = await db.execute(
                    "SELECT * FROM deep_mode_sessions WHERE article_id = ? AND stage = ?",
                    (article_id, stage)
                )
            elif article_id:
                cursor = await db.execute(
                    "SELECT * FROM deep_mode_sessions WHERE article_id = ?",
                    (article_id,)
                )
            elif stage:
                cursor = await db.execute(
                    "SELECT * FROM deep_mode_sessions WHERE stage = ?",
                    (stage,)
                )
            else:
                cursor = await db.execute("SELECT * FROM deep_mode_sessions")

            rows = await cursor.fetchall()

            sessions = []
            for row in rows:
                sessions.append(DeepModeSession(
                    session_id=row["session_id"],
                    article_id=row["article_id"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    stage=row["stage"],
                    profile=json.loads(row["profile"]),
                    outline=row["outline"],
                    outline_version=row["outline_version"],
                    draft_v1=row["draft_v1"],
                    current_draft=row["current_draft"],
                    tuning_history=json.loads(row["tuning_history"]),
                    source_article=json.loads(row["source_article"]),
                    rag_context=row["rag_context"],
                    final_draft=row["final_draft"],
                    finalized_at=row["finalized_at"],
                ))

            return sessions

    async def cleanup_expired_sessions(self):
        """清理过期会话。"""
        await self._ensure_db()

        cutoff = datetime.now().timestamp() - DEEP_MODE_SESSION_TTL
        cutoff_dt = datetime.fromtimestamp(cutoff).isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM deep_mode_sessions WHERE created_at < ? AND stage NOT IN ('completed', 'cancelled')",
                (cutoff_dt,)
            )
            deleted = db.total_changes
            await db.commit()

        if deleted > 0:
            logger.info(f"[SessionManager] Cleaned up {deleted} expired sessions")


# 全局实例
_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """获取 Session Manager 单例。"""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager
```

- [ ] **Step 2: Commit**

```bash
git add forge/deep_mode/session_manager.py
git commit -m "feat(deep_mode): add session manager with SQLite storage"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Task 4: 配置项更新

**Files:**
- Modify: `forge/config.py`

- [ ] **Step 1: 添加深度生成模式配置项**

在 `forge/config.py` 文件末尾添加：

```python
# ========== 深度生成模式配置 ==========

# Session 管理
DEEP_MODE_SESSION_TTL = int(os.getenv("DEEP_MODE_SESSION_TTL", "86400"))  # 24小时
OUTLINE_MAX_REVISIONS = int(os.getenv("OUTLINE_MAX_REVISIONS", "3"))      # 大纲最多修改 3 次
AGENT_EXECUTION_TIMEOUT = int(os.getenv("AGENT_EXECUTION_TIMEOUT", "60"))  # Agent 超时 60s

# 目标平台选项
TARGET_PLATFORM_OPTIONS = ["zhihu_article", "xhs_video", "wechat_article"]
```

- [ ] **Step 2: Commit**

```bash
git add forge/config.py
git commit -m "feat(config): add deep mode configuration items"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Task 5: Agent 工具 - profile_extractor

**Files:**
- Create: `forge/deep_mode/tools/__init__.py`
- Create: `forge/deep_mode/tools/profile_extractor.py`

- [ ] **Step 1: 创建工具模块入口**

```python
# forge/deep_mode/tools/__init__.py

"""深度生成模式 Agent 工具集。"""

from forge.deep_mode.tools.profile_extractor import profile_extractor
from forge.deep_mode.tools.rag_search import rag_search
from forge.deep_mode.tools.outline_generator import outline_generator
from forge.deep_mode.tools.content_generator import content_generator

__all__ = [
    "profile_extractor",
    "rag_search",
    "outline_generator",
    "content_generator",
]
```

- [ ] **Step 2: 创建 profile_extractor 工具**

```python
# forge/deep_mode/tools/profile_extractor.py

"""用户画像提取工具。"""

from langchain_core.tools import tool
import json
import logging

logger = logging.getLogger(__name__)


@tool
def profile_extractor(user_input: str, article_context: str) -> str:
    """从用户自然语言输入中提取结构化画像。

    Args:
        user_input: 用户的需求描述（自由文本）
        article_context: 原文章标题和摘要（用于理解上下文）

    Returns:
        JSON 格式的 ProfileInfo 字符串

    Example:
        用户输入: "改成知乎回答风格，语气专业点，给HR从业者看，重点讲实操案例"
        输出: {"tone": "专业", "target_audience": "HR从业者", "focus_point": "实操案例", "length_preference": "中等"}
    """
    logger.info(f"[profile_extractor] Extracting profile from: {user_input[:50]}...")

    # 构建提示词
    prompt = f"""请从用户的改写需求中提取以下维度的信息，以 JSON 格式返回：

文章上下文：
{article_context[:200]}

用户需求：
{user_input}

需要提取的维度：
1. tone（语气风格）：幽默、专业、轻松、犀利、温和、活泼
2. target_audience（目标读者）：职场新人、HR从业者、管理者、大众读者、专业人士
3. focus_point（侧重点）：实用工具、理论分析、案例故事、行业洞察、情感共鸣
4. length_preference（篇幅偏好）：简洁(500字)、中等(800字)、深度(1500字+)
5. target_platform（目标平台）：zhihu_article、xhs_video、wechat_article

规则：
- 如果用户未明确提及某维度，根据文章上下文推断合理默认值
- 只返回 JSON，不要其他解释
- 确保 JSON 格式正确

示例输出：
{"tone": "专业", "target_audience": "HR从业者", "focus_point": "实操案例", "length_preference": "中等", "target_platform": "zhihu_article"}
"""

    # 这里需要在 Agent 中调用 LLM，工具本身返回提示词结构
    # 实际提取逻辑由 Agent 调用 LLM 完成
    # 工具返回结构化的请求格式

    return json.dumps({
        "prompt": prompt,
        "user_input": user_input,
        "article_context": article_context,
        "requires_llm": True,
    })
```

- [ ] **Step 3: Commit**

```bash
git add forge/deep_mode/tools/__init__.py forge/deep_mode/tools/profile_extractor.py
git commit -m "feat(deep_mode): add profile_extractor tool"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Task 6: Agent 工具 - rag_search

**Files:**
- Create: `forge/deep_mode/tools/rag_search.py`

- [ ] **Step 1: 创建 rag_search 工具**

```python
# forge/deep_mode/tools/rag_search.py

"""知识库搜索工具。"""

from langchain_core.tools import tool
import logging

from forge.knowledge import get_knowledge_base

logger = logging.getLogger(__name__)


@tool
def rag_search(query: str, max_docs: int = 3) -> str:
    """搜索锐博集团知识库，获取相关参考资料。

    Args:
        query: 搜索关键词（如文章标题、核心概念）
        max_docs: 返回文档数量，默认 3

    Returns:
        知识库相关内容摘要，用于注入到生成 prompt

    Note:
        如果知识库连接失败或无结果，返回空字符串（Agent 会继续生成，只是没有知识库素材）
    """
    logger.info(f"[rag_search] Searching: {query[:50]}...")

    try:
        kb = get_knowledge_base()
        context = kb.get_context_for_topic(query, max_docs=max_docs)

        if context:
            logger.info(f"[rag_search] Found context: {len(context)} chars")
            return context
        else:
            logger.info("[rag_search] No relevant documents found")
            return ""

    except Exception as e:
        logger.warning(f"[rag_search] Search failed: {e}")
        # Fallback：返回空字符串，Agent 继续生成
        return ""
```

- [ ] **Step 2: Commit**

```bash
git add forge/deep_mode/tools/rag_search.py
git commit -m "feat(deep_mode): add rag_search tool"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Task 7: Agent 工具 - outline_generator

**Files:**
- Create: `forge/deep_mode/tools/outline_generator.py`

- [ ] **Step 1: 创建 outline_generator 工具**

```python
# forge/deep_mode/tools/outline_generator.py

"""大纲生成工具。"""

from langchain_core.tools import tool
import logging

logger = logging.getLogger(__name__)


@tool
def outline_generator(
    source_article: str,
    profile: str,
    rag_context: str
) -> str:
    """根据原文章、用户画像、知识库素材生成大纲。

    Args:
        source_article: 原知乎文章内容（标题 + 正文）
        profile: 用户画像 JSON 字符串
        rag_context: RAG 搜索结果

    Returns:
        大纲文本（带序号的结构化大纲）

    Example Output:
        一、开篇引入：职场新人的常见困境
            - 用一个真实场景切入
        二、核心观点：XX方法如何解决
            - 结合锐博集团实践案例
        三、实操建议
            - 三个可落地的技巧
        四、结尾
            - 引导读者思考
    """
    logger.info(f"[outline_generator] Generating outline...")

    # 构建大纲生成提示词
    prompt = f"""请根据以下信息生成文章大纲：

## 原文章内容
{source_article[:1500]}

## 用户画像
{profile}

## 知识库素材（可自然融入）
{rag_context if rag_context else "无"}

## 大纲生成要求
1. 保留原文核心观点和论证逻辑
2. 结构清晰，每个部分有明确的主题
3. 根据用户画像调整风格和侧重点
4. 如果有知识库素材，可在适当位置融入锐博集团案例
5. 大纲格式：一、二、三、四（带二级标题）
6. 篇幅控制在 {profile.get('length_preference', '中等')}

请直接输出大纲，格式示例：
一、开篇：[主题]
    - [要点]
二、核心观点：[主题]
    - [要点]
三、...
四、结尾：[主题]
"""

    return prompt


@tool
def outline_revision(
    current_outline: str,
    user_feedback: str,
    profile: str
) -> str:
    """根据用户反馈修改大纲。

    Args:
        current_outline: 当前大纲
        user_feedback: 用户修改意见
        profile: 用户画像

    Returns:
        修改后的大纲
    """
    logger.info(f"[outline_revision] Revising outline based on: {user_feedback[:50]}...")

    prompt = f"""请根据用户反馈修改大纲：

## 当前大纲
{current_outline}

## 用户反馈
{user_feedback}

## 用户画像
{profile}

## 修改要求
1. 针对用户反馈的具体问题进行修改
2. 保持大纲的整体结构和核心观点
3. 直接输出修改后的完整大纲

请直接输出修改后的大纲：
"""

    return prompt
```

- [ ] **Step 2: Commit**

```bash
git add forge/deep_mode/tools/outline_generator.py
git commit -m "feat(deep_mode): add outline_generator and outline_revision tools"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Task 8: Agent 工具 - content_generator

**Files:**
- Create: `forge/deep_mode/tools/content_generator.py`

- [ ] **Step 1: 创建 content_generator 工具**

```python
# forge/deep_mode/tools/content_generator.py

"""全文生成工具。"""

from langchain_core.tools import tool
import logging

logger = logging.getLogger(__name__)


@tool
def content_generator(
    outline: str,
    source_article: str,
    profile: str,
    rag_context: str
) -> str:
    """根据大纲生成完整文章。

    Args:
        outline: 已确认的大纲
        source_article: 原知乎文章内容（保留核心观点）
        profile: 用户画像 JSON 字符串（决定语气风格）
        rag_context: RAG 知识库素材

    Returns:
        完整文章文本

    Note:
        必须保留原文核心观点，RAG 素材自然融入，不生硬堆砌
    """
    logger.info(f"[content_generator] Generating content based on outline...")

    # 构建全文生成提示词
    prompt = f"""请根据大纲生成完整文章：

## 已确认的大纲
{outline}

## 原文章内容（核心观点来源）
{source_article[:2000]}

## 用户画像
{profile}

## 知识库素材（自然融入，不超过10%篇幅）
{rag_context if rag_context else "无"}

## 生成要求
1. **核心观点必须保留**：原文的主要论点和论证逻辑是主体，不能丢弃
2. **风格匹配画像**：语气、受众、侧重点按 profile 执行
3. **大纲为骨架**：每个大纲部分对应文章段落
4. **知识库素材自然融入**：用"据锐博集团资料显示..."等表述，不生硬
5. **篇幅控制**：按 profile 中的 length_preference
6. **信息真实性**：严禁编造具体信息（课程名、时间、数字等）
   - 原文有的事实可以保留
   - 知识库信息引用时标注来源
   - 没有具体信息时用模糊表述

请直接输出完整文章：
"""

    return prompt
```

- [ ] **Step 2: Commit**

```bash
git add forge/deep_mode/tools/content_generator.py
git commit -m "feat(deep_mode): add content_generator tool"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Task 9: Plan-Execute Agent

**Files:**
- Create: `forge/deep_mode/agents/__init__.py`
- Create: `forge/deep_mode/agents/plan_execute_agent.py`

- [ ] **Step 1: 创建 Agent 模块入口**

```python
# forge/deep_mode/agents/__init__.py

"""深度生成模式 Agent 模块。"""

from forge.deep_mode.agents.plan_execute_agent import PlanExecuteAgent, run_plan_execute

__all__ = [
    "PlanExecuteAgent",
    "run_plan_execute",
]
```

- [ ] **Step 2: 创建 Plan-Execute Agent**

```python
# forge/deep_mode/agents/plan_execute_agent.py

"""Plan-Execute Agent - 大纲确认阶段。"""

import logging
import json
import asyncio
from typing import Optional

from langchain.agents import AgentExecutor, create_react_agent
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import Tool

from forge.tools.llm_client import LLMClient
from forge.deep_mode.session_state import DeepModeSession, ProfileInfo
from forge.deep_mode.session_manager import SessionManager, get_session_manager
from forge.deep_mode.tools.profile_extractor import profile_extractor
from forge.deep_mode.tools.rag_search import rag_search
from forge.deep_mode.tools.outline_generator import outline_generator, outline_revision
from forge.deep_mode.tools.content_generator import content_generator
from forge.config import AGENT_EXECUTION_TIMEOUT

logger = logging.getLogger(__name__)


# Agent System Prompt
PLAN_EXECUTE_SYSTEM_PROMPT = """你是一个专业的内容改写助手，负责生成文章大纲和全文。

你的工作流程：
1. 理解用户的改写需求（画像）
2. 搜索知识库获取相关素材
3. 生成文章大纲
4. 等待用户确认大纲
5. 根据确认的大纲生成全文

重要原则：
- 保留原文核心观点
- 知识库素材自然融入，不生硬
- 风格匹配用户画像
- 严禁编造具体信息

可用工具：
{tools}

使用工具名称：{tool_names}

当前任务：{input}

{agent_scratchpad}
"""


class PlanExecuteAgent:
    """Plan-Execute Agent 用于大纲确认阶段。"""

    def __init__(self, session_manager: SessionManager = None):
        self.session_manager = session_manager or get_session_manager()
        self.llm = LLMClient()

        # 定义工具（用于大纲生成阶段）
        self.tools = [
            Tool(
                name="rag_search",
                description="搜索知识库获取素材",
                func=self._rag_search_wrapper,
            ),
            Tool(
                name="outline_generator",
                description="生成文章大纲",
                func=self._outline_generator_wrapper,
            ),
            Tool(
                name="content_generator",
                description="根据大纲生成全文",
                func=self._content_generator_wrapper,
            ),
        ]

    def _rag_search_wrapper(self, query: str) -> str:
        """包装 rag_search 工具（同步调用）。"""
        kb = get_knowledge_base()
        try:
            context = kb.get_context_for_topic(query, max_docs=3)
            return context or "无相关素材"
        except Exception as e:
            logger.warning(f"[PlanExecute] RAG search failed: {e}")
            return "无相关素材"

    def _outline_generator_wrapper(self, input_str: str) -> str:
        """大纲生成包装器，实际调用 LLM。"""
        # input_str 应包含 source_article, profile, rag_context
        try:
            data = json.loads(input_str)
            source_article = data.get("source_article", "")
            profile = data.get("profile", {})
            rag_context = data.get("rag_context", "")

            prompt = f"""请根据以下信息生成文章大纲：

## 原文章内容
{source_article[:1500]}

## 用户画像
{json.dumps(profile, ensure_ascii=False)}

## 知识库素材
{rag_context if rag_context else "无"}

## 大纲生成要求
1. 保留原文核心观点
2. 结构清晰：一、二、三、四
3. 每个部分有二级标题
4. 根据画像调整风格

请直接输出大纲：
"""
            # 同步调用（AgentExecutor 是同步的）
            # 这里我们需要用 asyncio.run 包装
            result = asyncio.run(self.llm.chat_with_retry(prompt))
            return result
        except Exception as e:
            logger.error(f"[PlanExecute] Outline generation failed: {e}")
            return f"大纲生成失败：{e}"

    def _content_generator_wrapper(self, input_str: str) -> str:
        """全文生成包装器。"""
        try:
            data = json.loads(input_str)
            outline = data.get("outline", "")
            source_article = data.get("source_article", "")
            profile = data.get("profile", {})
            rag_context = data.get("rag_context", "")

            prompt = f"""请根据大纲生成完整文章：

## 大纲
{outline}

## 原文章内容
{source_article[:2000]}

## 用户画像
{json.dumps(profile, ensure_ascii=False)}

## 知识库素材
{rag_context if rag_context else "无"}

## 生成要求
1. 保留原文核心观点
2. 按大纲结构展开
3. 知识库素材自然融入
4. 严禁编造具体信息

请直接输出文章：
"""
            result = asyncio.run(self.llm.chat_with_retry(prompt))
            return result
        except Exception as e:
            logger.error(f"[PlanExecute] Content generation failed: {e}")
            return f"全文生成失败：{e}"

    async def run_profile_extraction(
        self,
        session: DeepModeSession,
        user_input: str
    ) -> ProfileInfo:
        """提取用户画像。"""
        logger.info(f"[PlanExecute] Extracting profile...")

        article_context = f"{session['source_article'].get('title', '')} {session['source_article'].get('text', '')[:200]}"

        prompt = f"""请从用户需求中提取画像：

文章：{article_context}
用户需求：{user_input}

提取维度：tone, target_audience, focus_point, length_preference, target_platform

返回 JSON：
"""

        result = await self.llm.chat_with_retry(prompt)

        # 解析 JSON
        try:
            # 清理可能的 markdown 格式
            if "```json" in result:
                result = result.split("```json")[1].split("```")[0]
            elif "```" in result:
                result = result.split("```")[1].split("```")[0]

            profile = json.loads(result.strip())
            logger.info(f"[PlanExecute] Profile extracted: {profile}")
            return ProfileInfo(**profile)
        except json.JSONDecodeError:
            logger.warning(f"[PlanExecute] JSON parse failed, using default profile")
            return ProfileInfo(
                tone="专业",
                target_audience="大众读者",
                focus_point="实用工具",
                length_preference="中等",
                target_platform="zhihu_article",
            )

    async def run_rag_search(self, session: DeepModeSession) -> str:
        """搜索知识库。"""
        logger.info("[PlanExecute] Running RAG search...")

        title = session["source_article"].get("title", "")
        text = session["source_article"].get("text", "")[:200]
        query = f"{title} {text}"

        kb = get_knowledge_base()
        try:
            context = kb.get_context_for_topic(query, max_docs=3)
            return context or ""
        except Exception as e:
            logger.warning(f"[PlanExecute] RAG search failed: {e}")
            return ""

    async def run_outline_generation(self, session: DeepModeSession) -> str:
        """生成大纲。"""
        logger.info("[PlanExecute] Generating outline...")

        source_article = f"标题：{session['source_article'].get('title', '')}\n内容：{session['source_article'].get('text', '')}"
        profile = session["profile"]
        rag_context = session["rag_context"]

        prompt = f"""请根据以下信息生成文章大纲：

## 原文章内容
{source_article[:1500]}

## 用户画像
{json.dumps(profile, ensure_ascii=False)}

## 知识库素材（可自然融入）
{rag_context if rag_context else "无"}

## 大纲要求
1. 保留原文核心观点
2. 结构：一、二、三、四（带二级标题）
3. 篇幅：{profile.get('length_preference', '中等')}
4. 侧重点：{profile.get('focus_point', '实用工具')}
5. 语气：{profile.get('tone', '专业')}

直接输出大纲：
"""

        outline = await self.llm.chat_with_retry(prompt)
        logger.info(f"[PlanExecute] Outline generated: {len(outline)} chars")
        return outline

    async def run_outline_revision(
        self,
        session: DeepModeSession,
        user_feedback: str
    ) -> str:
        """修改大纲。"""
        logger.info(f"[PlanExecute] Revising outline: {user_feedback[:50]}...")

        prompt = f"""请根据用户反馈修改大纲：

## 当前大纲
{session['outline']}

## 用户反馈
{user_feedback}

## 用户画像
{json.dumps(session['profile'], ensure_ascii=False)}

## 修改要求
1. 针对反馈修改
2. 保持整体结构

直接输出修改后的大纲：
"""

        revised_outline = await self.llm.chat_with_retry(prompt)
        logger.info(f"[PlanExecute] Outline revised: {len(revised_outline)} chars")
        return revised_outline

    async def run_content_generation(self, session: DeepModeSession) -> str:
        """生成全文。"""
        logger.info("[PlanExecute] Generating content...")

        prompt = f"""请根据大纲生成完整文章：

## 大纲
{session['outline']}

## 原文章内容
标题：{session['source_article'].get('title', '')}
内容：{session['source_article'].get('text', '')[:2000]}

## 用户画像
{json.dumps(session['profile'], ensure_ascii=False)}

## 知识库素材
{session['rag_context'] if session['rag_context'] else "无"}

## 生成要求
1. **保留核心观点**：原文论点不能丢弃
2. **按大纲结构**：每个部分对应段落
3. **风格匹配**：语气={session['profile'].get('tone', '专业')}
4. **知识库融入**：自然引用，不超过10%
5. **严禁编造**：没有具体信息用模糊表述

直接输出文章：
"""

        content = await self.llm.chat_with_retry(prompt)
        logger.info(f"[PlanExecute] Content generated: {len(content)} chars")
        return content


async def run_plan_execute(
    session_id: str,
    stage: str,
    user_input: str = None
) -> DeepModeSession:
    """运行 Plan-Execute Agent 指定阶段。

    Args:
        session_id: 会话 ID
        stage: 要执行的阶段（profile_extraction, outline_generation, content_generation）
        user_input: 用户输入（profile_extraction 和 outline_revision 时需要）

    Returns:
        更新后的 Session
    """
    session_manager = get_session_manager()
    agent = PlanExecuteAgent(session_manager)

    session = await session_manager.load_session(session_id)

    if stage == "profile_extraction":
        # 提取画像
        profile = await agent.run_profile_extraction(session, user_input)
        session = await session_manager.update_session(
            session_id,
            profile=profile,
            stage="generating_outline"
        )

        # 搜索知识库
        rag_context = await agent.run_rag_search(session)
        session = await session_manager.update_session(
            session_id,
            rag_context=rag_context
        )

        # 生成大纲
        outline = await agent.run_outline_generation(session)
        session = await session_manager.update_session(
            session_id,
            outline=outline,
            outline_version=1,
            stage="waiting_outline"
        )

    elif stage == "outline_revision":
        # 修改大纲
        revised_outline = await agent.run_outline_revision(session, user_input)
        new_version = await session_manager.increment_outline_version(session_id)
        session = await session_manager.update_session(
            session_id,
            outline=revised_outline,
            outline_version=new_version,
            stage="waiting_outline"
        )

    elif stage == "content_generation":
        # 生成全文
        content = await agent.run_content_generation(session)
        session = await session_manager.update_session(
            session_id,
            draft_v1=content,
            current_draft=content,
            stage="tuning"  # Phase 2 会处理 tuning，Phase 1 直接标记 tuning
        )

    return session
```

- [ ] **Step 3: Commit**

```bash
git add forge/deep_mode/agents/__init__.py forge/deep_mode/agents/plan_execute_agent.py
git commit -m "feat(deep_mode): add Plan-Execute Agent"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Task 10: REST API 端点

**Files:**
- Modify: `forge/web/app.py`

- [ ] **Step 1: 先读取现有 app.py 结构**

读取 `forge/web/app.py` 了解现有结构，然后添加新端点。

- [ ] **Step 2: 添加深度生成 API 端点**

在 `forge/web/app.py` 中添加：

```python
# ========== 深度生成模式 API ==========

from pydantic import BaseModel
from forge.deep_mode import ProfileInfo, DeepModeSession, SessionNotFoundError, InvalidStageError
from forge.deep_mode.session_manager import get_session_manager
from forge.deep_mode.agents.plan_execute_agent import run_plan_execute


class CreateSessionRequest(BaseModel):
    """创建会话请求。"""
    article_id: str
    source_article: dict
    profile: ProfileInfo = None
    user_input: str = None  # 用户自然语言需求（可选）


class OutlineActionRequest(BaseModel):
    """大纲操作请求。"""
    session_id: str
    action: str  # "accept" or "modify"
    modification: str = None  # 修改意见（modify 时需要）


class FinalizeRequest(BaseModel):
    """定稿请求。"""
    session_id: str


@app.post("/api/deep_mode/create_session")
async def api_create_deep_mode_session(request: CreateSessionRequest):
    """创建深度生成会话，启动 Plan-Execute Agent。

    如果提供 user_input，会自动提取画像并开始生成大纲。
    """
    session_manager = get_session_manager()

    # 创建会话
    session = await session_manager.create_session(
        article_id=request.article_id,
        source_article=request.source_article,
        profile=request.profile
    )

    # 如果有用户输入，立即开始画像提取和大纲生成
    if request.user_input:
        try:
            session = await run_plan_execute(
                session["session_id"],
                "profile_extraction",
                user_input=request.user_input
            )
            return {
                "session_id": session["session_id"],
                "stage": session["stage"],
                "profile": session["profile"],
                "outline": session["outline"],
                "outline_version": session["outline_version"],
            }
        except Exception as e:
            # 失败时返回会话 ID，用户可以重试
            return {
                "session_id": session["session_id"],
                "stage": "waiting_profile",
                "error": str(e),
            }

    return {
        "session_id": session["session_id"],
        "stage": session["stage"],
    }


@app.get("/api/deep_mode/session/{session_id}")
async def api_get_session_status(session_id: str):
    """获取会话状态。"""
    session_manager = get_session_manager()

    try:
        session = await session_manager.load_session(session_id)
        return {
            "session_id": session["session_id"],
            "article_id": session["article_id"],
            "stage": session["stage"],
            "profile": session["profile"],
            "outline": session["outline"],
            "outline_version": session["outline_version"],
            "draft_v1": session["draft_v1"],
            "current_draft": session["current_draft"],
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
        }
    except SessionNotFoundError:
        return {"error": "Session not found", "session_id": session_id}, 404


@app.post("/api/deep_mode/outline_action")
async def api_outline_action(request: OutlineActionRequest):
    """大纲确认或修改。"""
    session_manager = get_session_manager()

    try:
        session = await session_manager.load_session(request.session_id)

        # 检查阶段
        if session["stage"] != "waiting_outline":
            raise InvalidStageError(session["stage"], "waiting_outline")

        if request.action == "accept":
            # 确认大纲，开始生成全文
            session = await run_plan_execute(
                request.session_id,
                "content_generation"
            )
            return {
                "status": "accepted",
                "session_id": session["session_id"],
                "stage": session["stage"],
                "draft": session["current_draft"],
            }

        elif request.action == "modify":
            if not request.modification:
                return {"error": "modification required for modify action"}, 400

            # 修改大纲
            session = await run_plan_execute(
                request.session_id,
                "outline_revision",
                user_input=request.modification
            )
            return {
                "status": "modified",
                "session_id": session["session_id"],
                "stage": session["stage"],
                "outline": session["outline"],
                "outline_version": session["outline_version"],
            }

        else:
            return {"error": "Invalid action: must be 'accept' or 'modify'"}, 400

    except SessionNotFoundError:
        return {"error": "Session not found"}, 404
    except InvalidStageError as e:
        return {"error": str(e)}, 400
    except OutlineRevisionLimitError as e:
        return {"error": str(e), "max_revisions": e.max_revisions}, 400


@app.post("/api/deep_mode/finalize")
async def api_finalize_session(request: FinalizeRequest):
    """定稿会话（Phase 1 版本，直接返回定稿内容）。"""
    session_manager = get_session_manager()

    try:
        session = await session_manager.finalize_session(request.session_id)
        return {
            "status": "completed",
            "session_id": session["session_id"],
            "final_draft": session["final_draft"],
            "finalized_at": session["finalized_at"],
        }
    except SessionNotFoundError:
        return {"error": "Session not found"}, 404


@app.delete("/api/deep_mode/session/{session_id}")
async def api_cancel_session(session_id: str):
    """取消会话。"""
    session_manager = get_session_manager()

    try:
        session = await session_manager.cancel_session(session_id)
        return {
            "status": "cancelled",
            "session_id": session["session_id"],
        }
    except SessionNotFoundError:
        return {"error": "Session not found"}, 404


@app.get("/api/deep_mode/sessions")
async def api_list_sessions(article_id: str = None, stage: str = None):
    """列出会话。"""
    session_manager = get_session_manager()
    sessions = await session_manager.list_sessions(article_id=article_id, stage=stage)

    return {
        "sessions": [
            {
                "session_id": s["session_id"],
                "article_id": s["article_id"],
                "stage": s["stage"],
                "created_at": s["created_at"],
            }
            for s in sessions
        ],
        "count": len(sessions),
    }
```

- [ ] **Step 3: Commit**

```bash
git add forge/web/app.py
git commit -m "feat(api): add deep mode REST API endpoints"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Task 11: 添加 aiosqlite 依赖

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: 添加 aiosqlite 到 requirements.txt**

在 `requirements.txt` 中添加：

```
aiosqlite>=0.19.0
```

- [ ] **Step 2: 安装依赖**

```bash
pip install aiosqlite
```

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "feat: add aiosqlite dependency for session manager"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Phase 1 完成验证

完成以上所有任务后，Phase 1 应具备以下能力：

1. ✅ Session 创建、查询、取消
2. ✅ 画像提取（从用户自然语言）
3. ✅ RAG 知识库搜索
4. ✅ 大纲生成
5. ✅ 大纲修改（最多 3 次）
6. ✅ 全文生成
7. ✅ 定稿

**测试流程：**

```bash
# 1. 启动服务
python run_web.py

# 2. 创建会话
curl -X POST http://localhost:8000/api/deep_mode/create_session \
  -H "Content-Type: application/json" \
  -d '{
    "article_id": "test001",
    "source_article": {"title": "测试文章", "text": "这是一篇测试文章内容..."},
    "user_input": "改成知乎回答风格，语气专业，给HR从业者看"
  }'

# 3. 查看状态
curl http://localhost:8000/api/deep_mode/session/<session_id>

# 4. 修改大纲
curl -X POST http://localhost:8000/api/deep_mode/outline_action \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "<session_id>",
    "action": "modify",
    "modification": "把第二部分改成案例分析"
  }'

# 5. 确认大纲
curl -X POST http://localhost:8000/api/deep_mode/outline_action \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "<session_id>",
    "action": "accept"
  }'

# 6. 定稿
curl -X POST http://localhost:8000/api/deep_mode/finalize \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<session_id>"}'
```

---

## Self-Review Checklist

- [x] Spec coverage: 所有 Phase 1 功能都有对应任务
- [x] Placeholder scan: 无 TBD/TODO
- [x] Type consistency: ProfileInfo、DeepModeSession 在各模块中定义一致
- [x] 现有代码复用: LLMClient、KnowledgeBase 已复用
- [x] 依赖添加: aiosqlite 已添加到 requirements.txt

---

Phase 1 实现计划完成。