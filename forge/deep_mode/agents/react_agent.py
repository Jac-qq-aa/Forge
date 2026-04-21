# forge/deep_mode/agents/react_agent.py

"""ReAct Agent - 微调对话阶段。"""

import logging
import json
import asyncio
from datetime import datetime
from typing import Optional

from langchain_core.tools import Tool

from forge.tools.llm_client import LLMClient
from forge.deep_mode.session_state import DeepModeSession
from forge.deep_mode.session_manager import SessionManager, get_session_manager
from forge.deep_mode.tools.section_rewriter import section_rewriter
from forge.deep_mode.tools.tone_adjuster import tone_adjuster
from forge.deep_mode.tools.wikipedia_check import wikipedia_check
from forge.deep_mode.tools.rag_search import rag_search

logger = logging.getLogger(__name__)


# ReAct Agent System Prompt
REACT_SYSTEM_PROMPT = """你是一个内容微调专家，负责根据用户反馈优化文章。

你的能力：
1. 重写指定段落（section_rewriter）
2. 调整整体语气（tone_adjuster）
3. 核查专有名词（wikipedia_check）
4. 搜索知识库补充素材（rag_search）

重要原则：
- 保持原文核心观点
- 只修改用户指定的部分
- 修改后确保全文风格一致
- 不要编造信息

当前文章状态：
{context}

可用工具：
{tools}

工具名称：{tool_names}

用户请求：{input}

{agent_scratchpad}
"""


class ReactAgent:
    """ReAct Agent 用于微调对话阶段。"""

    def __init__(self, session_manager: SessionManager = None):
        self.session_manager = session_manager or get_session_manager()
        self.llm = LLMClient()

        # 定义工具
        self.tools = [
            Tool(
                name="section_rewriter",
                description="重写指定段落。参数：current_draft（全文）、section_identifier（段落标识）、user_request（修改要求）",
                func=self._section_rewriter_wrapper,
            ),
            Tool(
                name="tone_adjuster",
                description="调整整体语气。参数：current_draft（全文）、target_tone（目标语气）",
                func=self._tone_adjuster_wrapper,
            ),
            Tool(
                name="wikipedia_check",
                description="核查专有名词定义。参数：term（术语）",
                func=self._wikipedia_check_wrapper,
            ),
            Tool(
                name="rag_search",
                description="搜索知识库补充素材。参数：query（关键词）",
                func=self._rag_search_wrapper,
            ),
        ]

    def _section_rewriter_wrapper(self, input_str: str) -> str:
        """包装 section_rewriter。"""
        try:
            data = json.loads(input_str)
            return section_rewriter.invoke({
                "current_draft": data.get("current_draft", ""),
                "section_identifier": data.get("section_identifier", ""),
                "user_request": data.get("user_request", ""),
            })
        except Exception as e:
            return f"重写失败：{e}"

    def _tone_adjuster_wrapper(self, input_str: str) -> str:
        """包装 tone_adjuster。"""
        try:
            data = json.loads(input_str)
            return tone_adjuster.invoke({
                "current_draft": data.get("current_draft", ""),
                "target_tone": data.get("target_tone", ""),
            })
        except Exception as e:
            return f"语气调整失败：{e}"

    def _wikipedia_check_wrapper(self, term: str) -> str:
        """包装 wikipedia_check。"""
        return wikipedia_check.invoke(term)

    def _rag_search_wrapper(self, query: str) -> str:
        """包装 rag_search。"""
        return rag_search.invoke(query)

    async def process_user_request(
        self,
        session: DeepModeSession,
        user_message: str
    ) -> str:
        """处理用户微调请求。

        Args:
            session: 会话状态
            user_message: 用户消息

        Returns:
            Agent 响应
        """
        logger.info(f"[ReactAgent] Processing user request: {user_message[:50]}...")

        current_draft = session.get("current_draft") or session.get("draft_v1", "")
        outline = session.get("outline", "")
        profile = session.get("profile", {})

        # 构建上下文
        context = f"""
当前草稿（前 500 字）：
{current_draft[:500]}...

大纲：
{outline}

用户画像：
{json.dumps(profile, ensure_ascii=False)}
"""

        # 判断用户意图，直接处理常见请求
        user_lower = user_message.lower()

        # 语气调整
        if any(kw in user_lower for kw in ["语气", "风格", "严肃", "轻松", "活泼", "专业", "幽默", "温和", "犀利"]):
            tone_keywords = ["轻松", "活泼", "专业", "幽默", "温和", "犀利", "严肃", "通俗", "正式"]
            target_tone = "轻松活泼"  # 默认
            for kw in tone_keywords:
                if kw in user_lower:
                    target_tone = kw
                    break

            logger.info(f"[ReactAgent] Detected tone adjustment request: {target_tone}")
            result = await self._run_tone_adjustment(current_draft, target_tone)
            return result

        # 事实核查
        if any(kw in user_lower for kw in ["查一下", "核查", "定义", "是什么", "百度百科", "wikipedia"]):
            # 提取术语
            term = user_message.replace("查一下", "").replace("核查", "").replace("的定义", "").replace("是什么", "").strip()
            if term:
                logger.info(f"[ReactAgent] Detected fact check request: {term}")
                result = wikipedia_check.invoke(term)
                return result

        # 段落重写（默认）
        section_identifier = "相关段落"
        if any(kw in user_message for kw in ["第一段", "第二段", "第三段", "开头", "结尾", "中间"]):
            section_identifier = user_message

        logger.info(f"[ReactAgent] Defaulting to section rewrite: {section_identifier}")
        result = await self._run_section_rewrite(current_draft, section_identifier, user_message)
        return result

    async def _run_tone_adjustment(self, current_draft: str, target_tone: str) -> str:
        """执行语气调整。"""
        prompt = f"""请调整文章的整体语气风格：

## 当前文章
{current_draft}

## 目标语气
{target_tone}

## 调整规则
1. 保持原文的核心观点和结构
2. 调整措辞和表达方式以匹配目标语气
3. 直接输出调整后的完整文章

请直接输出完整文章：
"""

        result = await self.llm.chat_with_retry(prompt)
        return f"已调整语气为【{target_tone}】，更新如下：\n\n{result}"

    async def _run_section_rewrite(self, current_draft: str, section_identifier: str, user_request: str) -> str:
        """执行段落重写。"""
        prompt = f"""请根据用户要求重写文章的指定部分：

## 当前全文
{current_draft}

## 用户要求
{user_request}

## 重写规则
1. 只修改需要修改的部分，其他内容保持不变
2. 确保修改后的内容与全文风格一致
3. 保持原文核心观点
4. 直接输出修改后的完整文章

请直接输出完整文章：
"""

        result = await self.llm.chat_with_retry(prompt)
        return f"已根据您的要求修改，更新如下：\n\n{result}"


async def run_react_agent(
    session_id: str,
    user_message: str
) -> dict:
    """运行 ReAct Agent 处理用户微调请求。

    Args:
        session_id: 会话 ID
        user_message: 用户消息

    Returns:
        更新后的状态和响应
    """
    session_manager = get_session_manager()
    agent = ReactAgent(session_manager)

    session = await session_manager.load_session(session_id)

    # 处理用户请求
    response = await agent.process_user_request(session, user_message)

    # 解析响应，提取更新后的草稿
    updated_draft = response
    if "更新如下：" in response:
        updated_draft = response.split("更新如下：")[-1].strip()

    # 更新会话状态
    new_history = session.get("tuning_history", [])
    new_history.append({
        "role": "user",
        "content": user_message,
        "timestamp": datetime.now().isoformat(),
    })
    new_history.append({
        "role": "agent",
        "content": response,
        "timestamp": datetime.now().isoformat(),
    })

    await session_manager.update_session(
        session_id,
        current_draft=updated_draft,
        tuning_history=new_history,
    )

    return {
        "response": response,
        "updated_draft": updated_draft,
    }