# forge/deep_mode/workflow.py

"""LangGraph Workflow for Deep Mode - Plan-Execute + ReAct Architecture.

基于 LangGraph create_agent + AgentState 实现：
1. Plan-Execute 流程（大纲生成 → 内容生成）
2. ReAct 微调 Agent（对话微调）

参考：deer-flow create_agent + state_schema 模式
"""

import logging
import asyncio
from typing import Annotated, NotRequired, TypedDict, Literal, List, Dict, Any, Optional
from datetime import datetime
import operator

from langchain.agents import AgentState, create_agent
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_core.tools import tool

from forge.tools.llm_client import LLMClient, SyncLLMClient
from forge.deep_mode.session_state import DeepModeSession
from forge.knowledge import get_knowledge_base
from forge.config import AGENT_EXECUTION_TIMEOUT

logger = logging.getLogger(__name__)


# ============================================================================
# State Definition - Extend AgentState (deer-flow pattern)
# ============================================================================

class DeepModeState(AgentState):
    """深度生成模式状态 - 扩展 AgentState。

    继承 AgentState 的 messages 字段（使用 add_messages reducer），
    添加深度生成所需的额外状态字段。

    参考：deer-flow ThreadState 扩展 AgentState
    """

    # 会话信息
    session_id: NotRequired[str | None]
    article_id: NotRequired[str | None]

    # 原文章信息
    source_article: NotRequired[Dict[str, str] | None]  # {title, text, url}

    # 用户输入
    user_input: NotRequired[str | None]

    # 流程状态
    stage: NotRequired[
        Literal[
            "planning",        # 正在生成大纲
            "waiting_outline", # 等待用户确认大纲
            "executing",       # 正在生成全文
            "tuning",          # 微调对话
            "completed",       # 已定稿
        ]
    ]

    # 大纲和版本
    outline: NotRequired[str | None]
    outline_version: NotRequired[int | None]

    # RAG 知识库素材
    rag_context: NotRequired[str | None]

    # 当前草稿（工具可访问）
    current_draft: NotRequired[str | None]

    # 错误信息
    error: NotRequired[str | None]


# ============================================================================
# Plan-Execute Nodes (不使用 StateGraph，直接调用 LLM)
# ============================================================================

async def rag_search(source_article: Dict[str, str]) -> str:
    """搜索知识库获取素材。"""
    logger.info("[Workflow] Running RAG search...")

    title = source_article.get("title", "")
    text = source_article.get("text", "")[:200]
    query = f"{title} {text}"

    try:
        kb = get_knowledge_base()
        context = await asyncio.wait_for(
            asyncio.to_thread(kb.get_context_for_topic, query, 3),
            timeout=10.0
        )
        return context or ""
    except asyncio.TimeoutError:
        logger.warning("[Workflow] RAG search timeout (10s)")
        return ""
    except Exception as e:
        logger.warning(f"[Workflow] RAG search failed: {e}")
        return ""


async def generate_outline(
    source_article: Dict[str, str],
    user_input: str,
    rag_context: str = ""
) -> str:
    """生成大纲。"""
    logger.info("[Workflow] Generating outline...")

    prompt = f"""请根据以下信息生成文章大纲：

## 原文章内容
标题：{source_article.get('title', '')}
内容：{source_article.get('text', '')[:1500]}

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

    try:
        llm = LLMClient()
        outline = await llm.chat_with_retry(prompt)
        logger.info(f"[Workflow] Outline generated: {len(outline)} chars")
        return outline
    except Exception as e:
        logger.error(f"[Workflow] Outline generation failed: {e}")
        raise


async def revise_outline(current_outline: str, user_feedback: str) -> str:
    """修改大纲。"""
    logger.info(f"[Workflow] Revising outline: {user_feedback[:50]}...")

    prompt = f"""请根据用户反馈修改大纲：

## 当前大纲
{current_outline}

## 用户反馈
{user_feedback}

## 修改要求
1. 严格按照用户反馈修改
2. 用户要求删除就删除，要求增加就增加，不限制结构变化
3. 保持剩余内容的逻辑连贯性

直接输出修改后的大纲：
"""

    try:
        llm = LLMClient()
        revised = await llm.chat_with_retry(prompt)
        logger.info(f"[Workflow] Outline revised: {len(revised)} chars")
        return revised
    except Exception as e:
        logger.error(f"[Workflow] Outline revision failed: {e}")
        raise


async def generate_content(
    outline: str,
    source_article: Dict[str, str],
    rag_context: str = "",
    return_template_id: bool = False,
) -> str | tuple:
    """根据大纲生成全文。

    使用动态 Prompt 模板 + 高质量案例参考。

    Args:
        outline: 大纲内容
        source_article: 原文章数据 {title, text, url, platform}
        rag_context: RAG 知识库素材
        return_template_id: 是否返回模板ID（用于效果统计）

    Returns:
        生成的草稿内容，如果 return_template_id=True 则返回 (draft, template_id)
    """
    logger.info("[Workflow] Generating content with dynamic template...")

    # 初始化变量（降级时的默认值）
    template = None
    quality_context = "无参考案例"
    prompt_manager = None

    # 获取动态模板和高质量案例参考
    try:
        from forge.evolution import get_prompt_manager, get_quality_knowledge_manager, ensure_default_templates

        # 确保默认模板已初始化
        await ensure_default_templates()

        # 获取当前激活的模板
        prompt_manager = get_prompt_manager()
        template = await prompt_manager.get_active_template("deep_content_generator")

        # 获取高质量案例参考
        quality_kb = get_quality_knowledge_manager()
        platform = source_article.get("platform", "zhihu")
        quality_context = await quality_kb.get_context_for_generation(outline, platform)

    except Exception as e:
        # 降级：使用硬编码模板
        logger.warning(f"[Workflow] Evolution system unavailable, using fallback: {e}")
        from forge.evolution import get_fallback_template, skip_quality_context

        template = get_fallback_template("deep_content_generator")
        quality_context = skip_quality_context()

    # 确保 template 存在（双重保险）
    if template is None:
        from forge.evolution import get_fallback_template
        template = get_fallback_template("deep_content_generator")

    # 组装 prompt
    system_prompt = template.get("system_prompt", "")
    user_prompt_template = template.get("user_prompt_template", "")

    # 安全格式化（缺失变量使用默认值）
    variables = {
        "outline": outline,
        "title": source_article.get("title", ""),
        "raw_content": source_article.get("text", "")[:2000],
        "rag_context": rag_context or "无",
        "quality_context": quality_context,
    }

    # 格式化 user_prompt
    if prompt_manager:
        user_prompt = prompt_manager.safe_format_template(user_prompt_template, variables)
    else:
        # 直接格式化，缺失变量使用默认值
        safe_vars = {k: v or "无" for k, v in variables.items()}
        user_prompt = user_prompt_template.format(**safe_vars)

    # 调用 LLM
    try:
        llm = LLMClient()
        draft = await llm.chat_with_retry(user_prompt, system_prompt)
        logger.info(f"[Workflow] Content generated: {len(draft)} chars, template={template.get('id')}")

        if return_template_id:
            return draft, template.get("id")
        return draft

    except Exception as e:
        logger.error(f"[Workflow] Content generation failed: {e}")
        raise


# ============================================================================
# ReAct Tuning Agent Tools
# ============================================================================

@tool
def rewrite_section(section_identifier: str, user_request: str) -> str:
    """重写文章指定段落。

    Args:
        section_identifier: 段落标识（如"第二段"、"开头部分"）
        user_request: 用户修改要求

    Returns:
        重写后的完整草稿（通过 injected_state 获取当前草稿）
    """
    logger.info(f"[TuningTool] rewrite_section: {section_identifier}")
    # 注意：工具不直接传入 current_draft，由 Agent 通过 state 注入
    # 实际调用时 Agent 会将 current_draft 作为上下文传递给 LLM
    return "rewrite_section_called"


@tool
def adjust_tone(target_tone: str) -> str:
    """调整文章整体语气。

    Args:
        target_tone: 目标语气（如"轻松"、"专业"、"幽默")

    Returns:
        调整后的完整草稿
    """
    logger.info(f"[TuningTool] adjust_tone: {target_tone}")
    return "adjust_tone_called"


@tool
def check_fact(term: str) -> str:
    """核查专有名词定义。

    Args:
        term: 要核查的术语

    Returns:
        术语定义或核查结果
    """
    logger.info(f"[TuningTool] check_fact: {term}")

    prompt = f"""请解释以下术语的定义和背景：

术语：{term}

要求：
1. 给出准确的定义
2. 说明常见用法或背景
3. 如果不确定，明确说明"建议查阅专业资料"

请回答：
"""

    try:
        llm = SyncLLMClient()
        return llm.chat_with_retry(prompt)
    except Exception as e:
        return f"核查失败：{e}"


@tool
def search_knowledge(query: str) -> str:
    """搜索知识库补充素材。

    Args:
        query: 搜索关键词

    Returns:
        相关知识库内容
    """
    logger.info(f"[TuningTool] search_knowledge: {query}")

    try:
        kb = get_knowledge_base()
        context = kb.get_context_for_topic(query, 2)
        return context or "未找到相关内容"
    except Exception as e:
        return f"搜索失败：{e}"


# ============================================================================
# Tuning Agent System Prompt
# ============================================================================

TUNING_SYSTEM_PROMPT = """你是一个文章微调专家。

## ⚠️ 最重要规则：回复格式

你必须严格区分两种情况：

**情况1 - 用户要求修改文章**
回复格式：直接返回完整的修改后文章（不加任何前缀或说明）

示例：
用户："把第二段改得通俗点"
你的回复：
[完整的文章内容，第二段已修改，其他段落保持不变]

**情况2 - 用户只是提问或评论（不要求修改）**
回复格式：【回答】+ 你的回答内容

示例：
用户："这篇文章主要讲什么？"
你的回复：【回答】这篇文章主要讲述了...

用户："写得不错"
你的回复：【回答】谢谢您的认可！

用户："这个术语是什么意思？"
你的回复：【回答】这个术语指的是...

## 如何判断用户意图？

**需要修改文章（直接返回全文）：**
- "把...改..."
- "修改..."
- "删除..."
- "增加..."
- "精简..."
- "调整语气..."
- "重写..."

**只是提问或评论（用【回答】开头）：**
- "这篇文章讲什么？"
- "...是什么意思？"
- "...准确吗？"
- "写得不错"
- "有点长"
- "我觉得..."
- 任何不带修改意图的问题或评论

## 修改规则

1. 修改请求：返回完整文章，保持结构，只改指定部分
2. 提问请求：用【回答】开头，不修改文章

## 注意

- 模糊情况默认按提问处理（用【回答】开头）
- 修改请求必须返回完整文章，不能只返回片段
"""


# ============================================================================
# Create Tuning Agent (deer-flow pattern)
# ============================================================================

def create_tuning_agent():
    """创建 ReAct 微调 Agent。

    使用 langchain.agents.create_agent + state_schema。
    参考 deer-flow create_deerflow_agent 模式。
    """
    from langchain_openai import ChatOpenAI
    from forge.config import QWEN_API_URL, QWEN_API_KEY, QWEN_MODEL

    llm = ChatOpenAI(
        base_url=QWEN_API_URL,
        api_key=QWEN_API_KEY,
        model=QWEN_MODEL,
    )

    tools = [
        rewrite_section,
        adjust_tone,
        check_fact,
        search_knowledge,
    ]

    try:
        agent = create_agent(
            model=llm,
            tools=tools,
            system_prompt=TUNING_SYSTEM_PROMPT,
            state_schema=DeepModeState,  # 关键：传入自定义 state_schema
        )
        logger.info("[TuningAgent] Agent created with DeepModeState")
        return agent
    except Exception as e:
        logger.warning(f"[TuningAgent] create_agent failed: {e}, using fallback")
        return None


# ============================================================================
# Global instances
# ============================================================================

_tuning_agent = None


def get_tuning_agent():
    """获取 Tuning Agent 实例。"""
    global _tuning_agent
    if _tuning_agent is None:
        _tuning_agent = create_tuning_agent()
    return _tuning_agent


# ============================================================================
# Run Tuning Agent
# ============================================================================

async def run_tuning_agent(
    current_draft: str,
    user_message: str,
) -> str:
    """运行 Tuning Agent 处理用户微调请求。

    Args:
        current_draft: 当前草稿
        user_message: 用户修改请求

    Returns:
        Agent 响应（修改后的草稿或核查结果）
    """
    agent = get_tuning_agent()

    if agent is None:
        # Fallback: 直接调用 LLM
        return await _fallback_tuning(current_draft, user_message)

    # 构建包含当前草稿的消息
    full_message = f"""## 当前文章
{current_draft}

## 用户请求
{user_message}

## 回复格式要求（必须遵守）

1. 如果用户要求修改文章：
   - 直接返回完整的修改后文章
   - 不加任何前缀或说明
   - 保持结构，只改指定部分

2. 如果用户只是提问或评论（不要求修改）：
   - 以【回答】开头
   - 焊接回答内容，不返回文章

判断标准：
- 包含"改"、"修改"、"删除"、"增加"等词 → 修改请求
- 只是提问、评论、确认 → 提问请求

请处理上述请求："""

    try:
        result = await agent.ainvoke({
            "messages": [HumanMessage(content=full_message)],
            "current_draft": current_draft,  # state_schema 支持此字段
        })

        # 提取最终响应
        if "messages" in result:
            last_msg = result["messages"][-1]
            if isinstance(last_msg, AIMessage):
                response = last_msg.content
                logger.info(f"[TuningAgent] Response: {response[:100]}...")
                return response

        return current_draft
    except Exception as e:
        logger.error(f"[TuningAgent] Failed: {e}")
        return await _fallback_tuning(current_draft, user_message)


async def _fallback_tuning(current_draft: str, user_message: str) -> str:
    """Fallback: 直接调用 LLM 处理微调。"""
    logger.info("[TuningFallback] Using direct LLM call")

    full_message = f"""## 当前文章
{current_draft}

## 用户请求
{user_message}

## 回复格式要求（必须遵守）

1. 如果用户要求修改文章：
   - 直接返回完整的修改后文章
   - 不加任何前缀或说明
   - 保持结构，只改指定部分

2. 如果用户只是提问或评论（不要求修改）：
   - 以【回答】开头
   - 焊接回答内容，不返回文章

判断标准：
- 包含"改"、"修改"、"删除"、"增加"等词 → 修改请求
- 只是提问、评论、确认 → 提问请求

请处理上述请求："""

    try:
        llm = LLMClient()
        return await llm.chat_with_retry(full_message)
    except Exception as e:
        logger.error(f"[TuningFallback] Failed: {e}")
        return f"修改失败：{e}"


# ============================================================================
# API-compatible Functions
# ============================================================================

async def run_plan_execute(
    session_id: str,
    stage: str,
    user_input: str = None
) -> Dict[str, Any]:
    """运行 Plan-Execute Agent 指定阶段（兼容旧 API）。

    Args:
        session_id: 会话 ID
        stage: 要执行的阶段（outline_generation, outline_revision, content_generation）
        user_input: 用户输入（outline_generation 和 outline_revision 时需要）

    Returns:
        更新后的 Session (dict format)
    """
    from forge.deep_mode.session_manager import get_session_manager

    session_manager = get_session_manager()
    session = await session_manager.load_session(session_id)

    source_article = session.get("source_article", {})
    outline = session.get("outline", "")
    rag_context = session.get("rag_context", "")

    if stage == "outline_generation":
        # 搜索知识库
        rag_context = await rag_search(source_article)

        # 生成大纲
        outline = await generate_outline(source_article, user_input or "", rag_context)

        # 更新会话
        session = await session_manager.update_session(
            session_id,
            rag_context=rag_context,
            outline=outline,
            outline_version=1,
            stage="waiting_outline",
        )

    elif stage == "outline_revision":
        # 修改大纲
        revised_outline = await revise_outline(outline, user_input or "")

        new_version = await session_manager.increment_outline_version(session_id)
        session = await session_manager.update_session(
            session_id,
            outline=revised_outline,
            outline_version=new_version,
            stage="waiting_outline",
        )

    elif stage == "content_generation":
        # 生成全文
        draft = await generate_content(outline, source_article, rag_context)

        session = await session_manager.update_session(
            session_id,
            draft_v1=draft,
            current_draft=draft,
            stage="tuning",
        )

    return session


# ============================================================================
# Exports (keep for backward compatibility)
# ============================================================================

# 保留旧 API 兼容的函数名
WorkflowState = DeepModeState  # alias for compatibility

async def run_plan_execute_workflow(
    session_id: str,
    article_id: str,
    source_article: Dict[str, str],
    user_input: str,
) -> Dict[str, Any]:
    """运行完整的 Plan-Execute Workflow（兼容旧 API）。"""
    rag_context = await rag_search(source_article)
    outline = await generate_outline(source_article, user_input, rag_context)

    return {
        "session_id": session_id,
        "article_id": article_id,
        "source_article": source_article,
        "user_input": user_input,
        "outline": outline,
        "outline_version": 1,
        "rag_context": rag_context,
        "stage": "waiting_outline",
        "messages": [],
        "error": None,
    }


async def run_executor_only(
    session_id: str,
    outline: str,
    source_article: Dict[str, str],
    rag_context: str = "",
) -> Dict[str, Any]:
    """只运行 Executor（兼容旧 API）。"""
    draft = await generate_content(outline, source_article, rag_context)

    return {
        "session_id": session_id,
        "outline": outline,
        "source_article": source_article,
        "rag_context": rag_context,
        "draft": draft,
        "stage": "tuning",
        "messages": [AIMessage(content=draft)],
        "error": None,
    }


def get_workflow():
    """兼容旧 API - 已弃用，返回 None。"""
    logger.warning("[Workflow] get_workflow() is deprecated, use run_plan_execute() instead")
    return None


class TuningAgentFallback:
    """兼容旧 API - 已弃用。"""
    async def process_request(self, current_draft: str, user_message: str) -> str:
        logger.warning("[Workflow] TuningAgentFallback is deprecated")
        return await _fallback_tuning(current_draft, user_message)


# ============================================================================
# StateGraph Integration (LangSmith 显示节点名称)
# ============================================================================

# 导出 StateGraph 版本的函数
from forge.deep_mode.graph import (
    get_deep_mode_app,
    get_deep_mode_graph,
    run_outline_generation,
    run_outline_revision,
    run_content_generation,
    run_tuning,
    run_finalize,
    DeepModeGraphState,
)

# StateGraph 版本的 run_plan_execute（可选使用）
async def run_plan_execute_graph(
    session_id: str,
    stage: str,
    user_input: str = None,
    user_feedback: str = None,
) -> Dict[str, Any]:
    """使用 StateGraph 运行 Plan-Execute（LangSmith 显示节点名称）。

    Args:
        session_id: 会话 ID
        stage: 阶段（outline_generation, outline_revision, content_generation）
        user_input: 用户输入（outline_generation 时需要）
        user_feedback: 大纲修改反馈（outline_revision 时需要）

    Returns:
        更新后的状态字典

    LangSmith 显示节点名称：
        - rag_search
        - generate_outline
        - revise_outline
        - generate_content
        - tuning
        - finalize
    """
    from forge.deep_mode.session_manager import get_session_manager

    session_manager = get_session_manager()
    session = await session_manager.load_session(session_id)

    source_article = session.get("source_article", {})

    if stage == "outline_generation":
        # 使用 StateGraph 运行
        result = await run_outline_generation(
            session_id=session_id,
            source_article=source_article,
            user_input=user_input or "",
        )

        # 更新 session
        session = await session_manager.update_session(
            session_id,
            rag_context=result.get("rag_context", ""),
            outline=result.get("outline", ""),
            outline_version=result.get("outline_version", 1),
            stage=result.get("stage", "waiting_outline"),
        )

    elif stage == "outline_revision":
        # 获取当前状态
        current_state = {
            "session_id": session_id,
            "source_article": source_article,
            "outline": session.get("outline", ""),
            "outline_version": session.get("outline_version", 1),
            "rag_context": session.get("rag_context", ""),
        }

        # 使用 StateGraph 运行
        result = await run_outline_revision(
            session_id=session_id,
            current_state=current_state,
            user_feedback=user_feedback or user_input or "",
        )

        # 更新 session
        session = await session_manager.update_session(
            session_id,
            outline=result.get("outline", ""),
            outline_version=result.get("outline_version", session.get("outline_version", 1) + 1),
            stage=result.get("stage", "waiting_outline"),
        )

    elif stage == "content_generation":
        # 获取当前状态
        current_state = {
            "session_id": session_id,
            "source_article": source_article,
            "outline": session.get("outline", ""),
            "outline_version": session.get("outline_version", 1),
            "rag_context": session.get("rag_context", ""),
        }

        # 使用 StateGraph 运行
        result = await run_content_generation(
            session_id=session_id,
            current_state=current_state,
        )

        # 更新 session
        session = await session_manager.update_session(
            session_id,
            draft_v1=result.get("draft_v1", ""),
            current_draft=result.get("current_draft", ""),
            template_id=result.get("template_id"),  # 用于效果统计
            stage=result.get("stage", "tuning"),
        )

    return session