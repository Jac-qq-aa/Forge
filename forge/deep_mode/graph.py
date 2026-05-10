# forge/deep_mode/graph.py

"""LangGraph StateGraph for Deep Mode.

使用 StateGraph 组织节点，LangSmith 可以正确追踪节点名称。

流程：
START → rag_search → generate_outline → [条件: outline_approved]
    → (approved) generate_content → tuning → END
    → (rejected) revise_outline → generate_outline (循环)

节点名称会显示在 LangSmith 中：
- rag_search
- generate_outline
- revise_outline
- generate_content
- tuning
"""

import logging
from typing import Dict, Any, Optional, Literal

from langgraph.graph import StateGraph, END
from langgraph.constants import START
from langchain_core.messages import HumanMessage, AIMessage

from forge.tools.llm_client import LLMClient, SyncLLMClient
from forge.knowledge import get_knowledge_base
from forge.config import AGENT_EXECUTION_TIMEOUT

logger = logging.getLogger(__name__)


# ============================================================================
# State Definition
# ============================================================================

class DeepModeGraphState(Dict):
    """深度模式 StateGraph 状态。

    所有字段都是可选的，节点函数返回需要更新的字段。
    """

    # 会话信息
    session_id: Optional[str]
    article_id: Optional[str]

    # 原文章信息
    source_article: Optional[Dict[str, str]]  # {title, text, url, platform}

    # 用户输入
    user_input: Optional[str]
    user_feedback: Optional[str]  # 大纲修改反馈

    # 流程状态
    stage: Optional[Literal[
        "planning",        # 正在生成大纲
        "waiting_outline", # 等待用户确认大纲
        "executing",       # 正在生成全文
        "tuning",          # 微调对话
        "completed",       # 已定稿
    ]]

    # 大纲和版本
    outline: Optional[str]
    outline_version: Optional[int]

    # RAG 知识库素材
    rag_context: Optional[str]

    # 草稿
    draft_v1: Optional[str]
    current_draft: Optional[str]
    final_draft: Optional[str]

    # 微调
    tuning_history: Optional[list]
    user_tuning_message: Optional[str]  # 用户微调请求
    tuning_response: Optional[str]

    # 模板追踪（用于效果统计）
    template_id: Optional[str]

    # 错误信息
    error: Optional[str]


# ============================================================================
# Node Functions (每个节点名称会显示在 LangSmith)
# ============================================================================

async def rag_search_node(state: DeepModeGraphState) -> Dict[str, Any]:
    """节点: 搜索知识库获取素材。

    LangSmith 显示名称: rag_search
    """
    logger.info("[Node:rag_search] Running RAG search...")

    import asyncio

    source_article = state.get("source_article", {})
    title = source_article.get("title", "")
    text = source_article.get("text", "")[:200]
    query = f"{title} {text}"

    try:
        kb = get_knowledge_base()
        context = await asyncio.wait_for(
            asyncio.to_thread(kb.get_context_for_topic, query, 3),
            timeout=10.0
        )
        rag_context = context or ""
        logger.info(f"[Node:rag_search] Found context: {len(rag_context)} chars")
    except asyncio.TimeoutError:
        logger.warning("[Node:rag_search] Timeout (10s)")
        rag_context = ""
    except Exception as e:
        logger.warning(f"[Node:rag_search] Failed: {e}")
        rag_context = ""

    return {
        "rag_context": rag_context,
        "stage": "planning",
    }


async def generate_outline_node(state: DeepModeGraphState) -> Dict[str, Any]:
    """节点: 生成大纲。

    LangSmith 显示名称: generate_outline
    """
    logger.info("[Node:generate_outline] Generating outline...")

    source_article = state.get("source_article", {})
    user_input = state.get("user_input", "")
    rag_context = state.get("rag_context", "")

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
        outline_version = state.get("outline_version", 0) + 1

        logger.info(f"[Node:generate_outline] Generated: {len(outline)} chars, version={outline_version}")

        return {
            "outline": outline,
            "outline_version": outline_version,
            "stage": "waiting_outline",
        }
    except Exception as e:
        logger.error(f"[Node:generate_outline] Failed: {e}")
        return {
            "error": str(e),
            "stage": "error",
        }


async def revise_outline_node(state: DeepModeGraphState) -> Dict[str, Any]:
    """节点: 修改大纲。

    LangSmith 显示名称: revise_outline
    """
    logger.info("[Node:revise_outline] Revising outline...")

    current_outline = state.get("outline", "")
    user_feedback = state.get("user_feedback", "")

    prompt = f"""请根据用户反馈修改大纲：

## 当前大纲
{current_outline}

## 用户反馈
{user_feedback}

## 修改要求
1. 针对反馈修改
2. 保持整体结构
3. 直接输出修改后的大纲

直接输出修改后的大纲：
"""

    try:
        llm = LLMClient()
        revised = await llm.chat_with_retry(prompt)
        outline_version = state.get("outline_version", 0) + 1

        logger.info(f"[Node:revise_outline] Revised: {len(revised)} chars, version={outline_version}")

        return {
            "outline": revised,
            "outline_version": outline_version,
            "stage": "waiting_outline",
        }
    except Exception as e:
        logger.error(f"[Node:revise_outline] Failed: {e}")
        return {
            "error": str(e),
        }


async def generate_content_node(state: DeepModeGraphState) -> Dict[str, Any]:
    """节点: 根据大纲生成全文。

    LangSmith 显示名称: generate_content

    使用动态 Prompt 模板 + 高质量案例参考。
    """
    logger.info("[Node:generate_content] Generating content with dynamic template...")

    outline = state.get("outline", "")
    source_article = state.get("source_article", {})
    rag_context = state.get("rag_context", "")

    # 初始化变量（降级时的默认值）
    template = None
    quality_context = "无参考案例"
    prompt_manager = None

    # 获取动态模板和高质量案例参考
    try:
        from forge.evolution import (
            get_prompt_manager,
            get_quality_knowledge_manager,
            ensure_default_templates,
        )

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
        logger.warning(f"[Node:generate_content] Evolution system unavailable: {e}")
        from forge.evolution import get_fallback_template, skip_quality_context

        template = get_fallback_template("deep_content_generator")
        quality_context = skip_quality_context()

    # 确保 template 存在
    if template is None:
        from forge.evolution import get_fallback_template
        template = get_fallback_template("deep_content_generator")

    # 组装 prompt
    system_prompt = template.get("system_prompt", "")
    user_prompt_template = template.get("user_prompt_template", "")

    # 安全格式化
    variables = {
        "outline": outline,
        "title": source_article.get("title", ""),
        "raw_content": source_article.get("text", "")[:2000],
        "rag_context": rag_context or "无",
        "quality_context": quality_context,
    }

    if prompt_manager:
        user_prompt = prompt_manager.safe_format_template(user_prompt_template, variables)
    else:
        safe_vars = {k: v or "无" for k, v in variables.items()}
        user_prompt = user_prompt_template.format(**safe_vars)

    # 调用 LLM
    try:
        llm = LLMClient()
        draft = await llm.chat_with_retry(user_prompt, system_prompt)
        template_id = template.get("id")

        logger.info(f"[Node:generate_content] Generated: {len(draft)} chars, template={template_id}")

        return {
            "draft_v1": draft,
            "current_draft": draft,
            "template_id": template_id,
            "stage": "tuning",
            "tuning_history": [],  # 初始化微调历史
        }
    except Exception as e:
        logger.error(f"[Node:generate_content] Failed: {e}")
        return {
            "error": str(e),
            "stage": "error",
        }


async def tuning_node(state: DeepModeGraphState) -> Dict[str, Any]:
    """节点: 微调文章。

    LangSmith 显示名称: tuning

    处理用户微调请求，返回修改后的草稿或回答。
    """
    logger.info("[Node:tuning] Processing tuning request...")

    current_draft = state.get("current_draft", "")
    user_tuning_message = state.get("user_tuning_message", "")
    tuning_history = state.get("tuning_history", [])

    if not user_tuning_message:
        logger.info("[Node:tuning] No tuning message, skipping")
        return {}

    # 构建微调 prompt
    full_message = f"""## 当前文章
{current_draft}

## 用户请求
{user_tuning_message}

## 回复格式要求

1. 如果用户要求修改文章：
   - 直接返回完整的修改后文章
   - 不加任何前缀或说明
   - 保持结构，只改指定部分

2. 如果用户只是提问或评论：
   - 以【回答】开头
   - 只回答内容，不返回文章

请处理上述请求："""

    try:
        llm = LLMClient()
        response = await llm.chat_with_retry(full_message)

        # 判断响应类型
        is_question_response = response.startswith("【回答】")

        # 更新历史
        new_history = tuning_history.copy()
        new_history.append({
            "role": "user",
            "content": user_tuning_message,
        })
        new_history.append({
            "role": "agent",
            "content": response,
            "is_question": is_question_response,
        })

        # 如果是修改请求，更新草稿
        updated_draft = current_draft
        if not is_question_response:
            # 检查响应长度（避免片段）
            if len(response) > len(current_draft) * 0.3:
                updated_draft = response

        logger.info(f"[Node:tuning] Response: {len(response)} chars, is_question={is_question_response}")

        return {
            "tuning_response": response,
            "current_draft": updated_draft,
            "tuning_history": new_history,
            "user_tuning_message": None,  # 清空当前请求
        }
    except Exception as e:
        logger.error(f"[Node:tuning] Failed: {e}")
        return {
            "error": str(e),
        }


async def finalize_node(state: DeepModeGraphState) -> Dict[str, Any]:
    """节点: 定稿文章。

    LangSmith 显示名称: finalize
    """
    logger.info("[Node:finalize] Finalizing article...")

    current_draft = state.get("current_draft", "")

    return {
        "final_draft": current_draft,
        "stage": "completed",
    }


# ============================================================================
# Conditional Edge Functions
# ============================================================================

def should_revise_outline(state: DeepModeGraphState) -> str:
    """判断是否需要修改大纲。

    Returns:
        "revise" - 需要修改
        "generate" - 大纲已确认，生成内容
    """
    stage = state.get("stage", "")

    # 如果用户提供了 feedback，说明需要修改
    if state.get("user_feedback"):
        return "revise"

    # 如果大纲已确认（stage 从外部设置为 executing）
    if stage == "executing":
        return "generate"

    # 默认等待确认
    return "wait"


def should_continue_tuning(state: DeepModeGraphState) -> str:
    """判断是否继续微调。

    Returns:
        "continue" - 继续微调
        "finalize" - 定稿
    """
    stage = state.get("stage", "")

    # 如果用户请求定稿
    if stage == "completed":
        return "finalize"

    # 如果有新的微调请求
    if state.get("user_tuning_message"):
        return "continue"

    # 默认等待
    return "wait"


def check_error(state: DeepModeGraphState) -> str:
    """检查是否有错误。

    Returns:
        "error" - 有错误，终止
        "continue" - 无错误，继续
    """
    if state.get("error"):
        return "error"
    return "continue"


# ============================================================================
# Build StateGraph
# ============================================================================

def build_deep_mode_graph() -> StateGraph:
    """构建深度模式 StateGraph。

    节点名称会显示在 LangSmith 中。
    """
    # 创建 StateGraph
    graph = StateGraph(DeepModeGraphState)

    # 添加节点（LangSmith 显示这些名称）
    graph.add_node("rag_search", rag_search_node)
    graph.add_node("generate_outline", generate_outline_node)
    graph.add_node("revise_outline", revise_outline_node)
    graph.add_node("generate_content", generate_content_node)
    graph.add_node("tuning", tuning_node)
    graph.add_node("finalize", finalize_node)

    # 添加边
    # START → rag_search
    graph.add_edge(START, "rag_search")

    # rag_search → generate_outline
    graph.add_edge("rag_search", "generate_outline")

    # generate_outline → 条件判断
    graph.add_conditional_edges(
        "generate_outline",
        should_revise_outline,
        {
            "revise": "revise_outline",
            "generate": "generate_content",
            "wait": END,  # 等待用户确认
        }
    )

    # revise_outline → generate_outline（循环）
    graph.add_edge("revise_outline", "generate_outline")

    # generate_content → tuning
    graph.add_edge("generate_content", "tuning")

    # tuning → 条件判断
    graph.add_conditional_edges(
        "tuning",
        should_continue_tuning,
        {
            "continue": "tuning",  # 继续微调（循环）
            "finalize": "finalize",
            "wait": END,  # 等待用户输入
        }
    )

    # finalize → END
    graph.add_edge("finalize", END)

    logger.info("[Graph] Deep mode StateGraph built")

    return graph


# ============================================================================
# Compiled Graph Instance
# ============================================================================

_deep_mode_graph = None
_deep_mode_app = None


def get_deep_mode_graph() -> StateGraph:
    """获取 StateGraph 实例。"""
    global _deep_mode_graph
    if _deep_mode_graph is None:
        _deep_mode_graph = build_deep_mode_graph()
    return _deep_mode_graph


def get_deep_mode_app():
    """获取编译后的 Graph App。"""
    global _deep_mode_app
    if _deep_mode_app is None:
        graph = get_deep_mode_graph()
        _deep_mode_app = graph.compile()
        logger.info("[Graph] Deep mode graph compiled")
    return _deep_mode_app


# ============================================================================
# Helper Functions for Integration
# ============================================================================

async def run_outline_generation(
    session_id: str,
    source_article: Dict[str, str],
    user_input: str,
) -> Dict[str, Any]:
    """运行大纲生成阶段。

    使用 StateGraph，LangSmith 显示节点名称。
    """
    app = get_deep_mode_app()

    initial_state = {
        "session_id": session_id,
        "source_article": source_article,
        "user_input": user_input,
        "stage": "planning",
    }

    # 运行到 generate_outline 后暂停（等待用户确认）
    result = await app.ainvoke(initial_state)

    logger.info(f"[Graph] Outline generation completed: stage={result.get('stage')}")
    return result


async def run_outline_revision(
    session_id: str,
    current_state: Dict[str, Any],
    user_feedback: str,
) -> Dict[str, Any]:
    """运行大纲修改阶段。"""
    # 直接调用 revise_outline_node（LangSmith 显示 revise_outline）
    state = DeepModeGraphState({**current_state, "user_feedback": user_feedback})
    result = await revise_outline_node(state)

    logger.info(f"[Graph] Outline revision completed: version={result.get('outline_version')}")
    return {**current_state, **result}


async def run_content_generation(
    session_id: str,
    current_state: Dict[str, Any],
) -> Dict[str, Any]:
    """运行内容生成阶段。

    直接调用 generate_content_node，LangSmith 显示 generate_content。
    """
    # 直接调用节点（跳过前面的节点）
    state = DeepModeGraphState(current_state)
    result = await generate_content_node(state)

    logger.info(f"[Graph] Content generation completed: {len(result.get('current_draft', ''))} chars")
    return {**current_state, **result}


async def run_tuning(
    session_id: str,
    current_state: Dict[str, Any],
    user_message: str,
) -> Dict[str, Any]:
    """运行微调阶段。

    直接调用 tuning_node，LangSmith 显示 tuning。
    """
    state = DeepModeGraphState({**current_state, "user_tuning_message": user_message})
    result = await tuning_node(state)

    logger.info(f"[Graph] Tuning completed: is_question={result.get('tuning_response', '').startswith('【回答】')}")
    return {**current_state, **result}


async def run_finalize(
    session_id: str,
    current_state: Dict[str, Any],
) -> Dict[str, Any]:
    """运行定稿阶段。

    直接调用 finalize_node，LangSmith 显示 finalize。
    """
    state = DeepModeGraphState(current_state)
    result = await finalize_node(state)

    logger.info(f"[Graph] Finalize completed")
    return {**current_state, **result}