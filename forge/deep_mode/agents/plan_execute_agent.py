# forge/deep_mode/agents/plan_execute_agent.py

"""Plan-Execute Agent - 大纲确认阶段。"""

import logging
import json
import asyncio
from typing import Optional

from langchain_core.tools import Tool

from forge.tools.llm_client import LLMClient
from forge.deep_mode.session_state import DeepModeSession, ProfileInfo
from forge.deep_mode.session_manager import SessionManager, get_session_manager
from forge.deep_mode.tools.profile_extractor import profile_extractor
from forge.deep_mode.tools.rag_search import rag_search
from forge.deep_mode.tools.outline_generator import outline_generator, outline_revision
from forge.deep_mode.tools.content_generator import content_generator
from forge.knowledge import get_knowledge_base
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