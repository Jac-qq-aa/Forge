# forge/deep_mode/agents/plan_execute_agent.py

"""Plan-Execute Agent - 大纲确认阶段。"""

import logging
import json
import asyncio

from langchain_core.tools import Tool

from forge.tools.llm_client import LLMClient
from forge.deep_mode.session_state import DeepModeSession
from forge.deep_mode.session_manager import SessionManager, get_session_manager
from forge.deep_mode.tools.rag_search import rag_search
from forge.deep_mode.tools.outline_generator import outline_generator, outline_revision
from forge.deep_mode.tools.content_generator import content_generator
from forge.knowledge import get_knowledge_base
from forge.config import AGENT_EXECUTION_TIMEOUT

logger = logging.getLogger(__name__)


class PlanExecuteAgent:
    """Plan-Execute Agent 用于大纲确认阶段。"""

    def __init__(self, session_manager: SessionManager = None):
        self.session_manager = session_manager or get_session_manager()
        self.llm = LLMClient()

    async def run_rag_search(self, session: DeepModeSession) -> str:
        """搜索知识库（带超时保护）。"""
        logger.info("[PlanExecute] Running RAG search...")

        title = session["source_article"].get("title", "")
        text = session["source_article"].get("text", "")[:200]
        query = f"{title} {text}"

        try:
            kb = get_knowledge_base()
            context = await asyncio.wait_for(
                asyncio.to_thread(kb.get_context_for_topic, query, 3),
                timeout=10.0
            )
            return context or ""
        except asyncio.TimeoutError:
            logger.warning("[PlanExecute] RAG search timeout (10s), skipping")
            return ""
        except Exception as e:
            logger.warning(f"[PlanExecute] RAG search failed: {e}, skipping")
            return ""

    async def run_outline_generation(self, session: DeepModeSession, user_input: str) -> str:
        """生成大纲（基于用户输入）。"""
        logger.info("[PlanExecute] Generating outline...")

        source_article = f"标题：{session['source_article'].get('title', '')}\n内容：{session['source_article'].get('text', '')}"
        rag_context = session.get("rag_context", "")

        prompt = f"""请根据以下信息生成文章大纲：

## 原文章内容
{source_article[:1500]}

## 用户改写需求
{user_input}

## 知识库素材（可自然融入）
{rag_context if rag_context else "无"}

## 大纲要求
1. 保留原文核心观点
2. 结构：一、二、三、四（带二级标题）
3. 根据用户需求调整风格和侧重点

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

## 知识库素材
{session.get('rag_context', '') if session.get('rag_context') else "无"}

## 生成要求
1. **保留核心观点**：原文论点不能丢弃
2. **按大纲结构**：每个部分对应段落
3. **知识库融入**：自然引用，不超过10%
4. **严禁编造**：没有具体信息用模糊表述

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
        stage: 要执行的阶段（outline_generation, content_generation）
        user_input: 用户输入（outline_generation 和 outline_revision 时需要）

    Returns:
        更新后的 Session
    """
    session_manager = get_session_manager()
    agent = PlanExecuteAgent(session_manager)

    session = await session_manager.load_session(session_id)

    if stage == "outline_generation":
        # 搜索知识库
        rag_context = await agent.run_rag_search(session)
        session = await session_manager.update_session(
            session_id,
            rag_context=rag_context
        )

        # 生成大纲
        outline = await agent.run_outline_generation(session, user_input)
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
            stage="tuning"
        )

    return session