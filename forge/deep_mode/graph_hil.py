# forge/deep_mode/graph_hil.py

"""LangGraph StateGraph with Human-in-the-Loop.

使用 interrupt 功能在关键节点暂停，等待用户确认后继续。

流程：
1. START → rag_search → generate_outline → interrupt（等待确认大纲）
2. 用户确认后 → generate_content → interrupt（等待确认定稿）
3. 用户确认后 → tuning（可选循环） → finalize → END

Human-in-the-Loop 暂停点：
- generate_outline 后：等待用户确认/修改大纲
- generate_content 后：等待用户确认定稿或继续微调

LangSmith Tracing:
- 使用 langsmith.tracing_context(parent=...) 合合 traces
- 在第一次调用时获取 run_tree headers，存储到 checkpointer
- resume 时从 state 获取 headers，使用 tracing_context 合合
"""

import logging
import uuid
from typing import Dict, Any, Optional, Literal, List

import langsmith as ls
from langsmith.run_helpers import get_current_run_tree
from langgraph.graph import StateGraph, END
from langgraph.constants import START
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage, AIMessage

from forge.tools.llm_client import LLMClient, SyncLLMClient
from forge.knowledge import get_knowledge_base
from forge.graph.checkpointer import get_checkpointer

logger = logging.getLogger(__name__)


# ============================================================================
# State Definition
# ============================================================================

class DeepModeHILState(Dict):
    """深度模式 Human-in-the-Loop 状态。

    所有字段都是可选的。
    """

    # 会话信息
    session_id: Optional[str]

    # LangSmith trace headers（用于合并 traces，持久化到 checkpointer）
    # 格式: {"langsmith-trace": "...", "langsmith-project": "..."}
    trace_headers: Optional[Dict[str, str]]

    # 原文章
    source_article: Optional[Dict[str, str]]

    # 用户输入
    user_input: Optional[str]

    # 大纲
    outline: Optional[str]
    outline_version: Optional[int]

    # 用户反馈（大纲修改）
    outline_feedback: Optional[str]

    # RAG 素材
    rag_context: Optional[str]

    # 草稿
    draft_v1: Optional[str]
    current_draft: Optional[str]
    final_draft: Optional[str]

    # 微调
    tuning_messages: Optional[List[Dict]]
    tuning_request: Optional[str]  # 用户微调请求

    # 用户决策
    user_decision: Optional[Literal["approve", "reject", "tuning", "finalize"]]

    # 阶段标记
    stage: Optional[str]

    # 错误
    error: Optional[str]


# ============================================================================
# Node Functions with interrupt
# ============================================================================

async def rag_search_node(state: DeepModeHILState) -> Dict[str, Any]:
    """节点: 搜索知识库."""
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
        logger.info(f"[Node:rag_search] Found: {len(rag_context)} chars")
    except Exception as e:
        logger.warning(f"[Node:rag_search] Failed: {e}")
        rag_context = ""

    return {"rag_context": rag_context}


async def generate_outline_node(state: DeepModeHILState) -> Dict[str, Any]:
    """节点: 生成大纲."""
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

## 知识库素材
{rag_context or "无"}

## 大纲要求
1. 保留原文核心观点
2. 结构：一、二、三、四（带二级标题）

直接输出大纲：
"""

    try:
        llm = LLMClient()
        outline = await llm.chat_with_retry(prompt)
        outline_version = state.get("outline_version", 0) + 1

        logger.info(f"[Node:generate_outline] Generated: {len(outline)} chars")

        return {
            "outline": outline,
            "outline_version": outline_version,
            "stage": "waiting_outline",
        }
    except Exception as e:
        logger.error(f"[Node:generate_outline] Failed: {e}")
        return {"error": str(e)}


async def wait_outline_approval_node(state: DeepModeHILState) -> Dict[str, Any]:
    """节点: 等待大纲确认（Human-in-the-Loop 暂停点）.

    使用 interrupt 暂停，等待用户决策：
    - approve: 确认大纲，继续生成内容
    - reject: 拒绝大纲，需要修改
    - outline_feedback: 用户修改反馈
    """
    logger.info("[Node:wait_outline_approval] Waiting for user approval...")

    # interrupt 暂停，返回大纲给用户
    user_response = interrupt({
        "type": "outline_approval",
        "outline": state.get("outline", ""),
        "outline_version": state.get("outline_version", 1),
        "message": "请确认大纲是否满意？您可以：1. 确认 2. 提出修改意见",
    })

    # 用户恢复后，处理响应
    logger.info(f"[Node:wait_outline_approval] User response: {user_response}")

    # user_response 可能是：
    # {"decision": "approve"} - 确认
    # {"decision": "reject", "feedback": "修改意见"} - 需要修改

    decision = user_response.get("decision", "approve")
    feedback = user_response.get("feedback", "")

    return {
        "user_decision": decision,
        "outline_feedback": feedback,
        "stage": "outline_approved" if decision == "approve" else "outline_rejected",
    }


async def revise_outline_node(state: DeepModeHILState) -> Dict[str, Any]:
    """节点: 修改大纲."""
    logger.info("[Node:revise_outline] Revising outline...")

    current_outline = state.get("outline", "")
    feedback = state.get("outline_feedback", "")

    prompt = f"""请根据用户反馈修改大纲：

## 当前大纲
{current_outline}

## 用户反馈
{feedback}

## 修改要求
1. 严格按照用户反馈修改
2. 用户要求删除就删除，要求增加就增加，不限制结构变化
3. 保持剩余内容的逻辑连贯性

直接输出修改后的大纲：
"""

    try:
        llm = LLMClient()
        revised = await llm.chat_with_retry(prompt)
        outline_version = state.get("outline_version", 0) + 1

        logger.info(f"[Node:revise_outline] Revised: {len(revised)} chars")

        return {
            "outline": revised,
            "outline_version": outline_version,
            "stage": "waiting_outline",  # 再次等待确认
        }
    except Exception as e:
        logger.error(f"[Node:revise_outline] Failed: {e}")
        return {"error": str(e)}


async def generate_content_node(state: DeepModeHILState) -> Dict[str, Any]:
    """节点: 生成全文."""
    logger.info("[Node:generate_content] Generating content...")

    outline = state.get("outline", "")
    source_article = state.get("source_article", {})
    rag_context = state.get("rag_context", "")

    # 获取动态模板（可选）
    template_id = None
    try:
        from forge.evolution import get_prompt_manager, ensure_default_templates
        await ensure_default_templates()
        prompt_manager = get_prompt_manager()
        template = await prompt_manager.get_active_template("deep_content_generator")
        system_prompt = template.get("system_prompt", "")
        user_prompt_template = template.get("user_prompt_template", "")
        template_id = template.get("id")

        # 格式化
        variables = {
            "outline": outline,
            "title": source_article.get("title", ""),
            "raw_content": source_article.get("text", "")[:2000],
            "rag_context": rag_context or "无",
            "quality_context": "无参考案例",
        }
        user_prompt = prompt_manager.safe_format_template(user_prompt_template, variables)
    except Exception as e:
        logger.warning(f"[Node:generate_content] Evolution unavailable: {e}")
        system_prompt = ""
        user_prompt = f"""请根据大纲生成完整文章：

## 大纲
{outline}

## 原文章内容
标题：{source_article.get('title', '')}
内容：{source_article.get('text', '')[:2000]}

直接输出文章：
"""

    try:
        llm = LLMClient()
        draft = await llm.chat_with_retry(user_prompt, system_prompt)

        logger.info(f"[Node:generate_content] Generated: {len(draft)} chars")

        return {
            "draft_v1": draft,
            "current_draft": draft,
            "template_id": template_id,
            "stage": "waiting_content",
        }
    except Exception as e:
        logger.error(f"[Node:generate_content] Failed: {e}")
        return {"error": str(e)}


async def wait_content_approval_node(state: DeepModeHILState) -> Dict[str, Any]:
    """节点: 等待内容确认（Human-in-the-Loop 暂停点）.

    使用 interrupt 暂停，等待用户决策：
    - approve: 确认定稿
    - tuning: 继续微调
    """
    logger.info("[Node:wait_content_approval] Waiting for user approval...")

    # interrupt 暂停，返回草稿给用户
    user_response = interrupt({
        "type": "content_approval",
        "draft": state.get("current_draft", ""),
        "message": "请确认文章是否满意？您可以：1. 定稿 2. 提出修改意见",
    })

    logger.info(f"[Node:wait_content_approval] User response: {user_response}")

    decision = user_response.get("decision", "finalize")
    tuning_request = user_response.get("tuning_request", "")

    return {
        "user_decision": decision,
        "tuning_request": tuning_request,
        "stage": "tuning" if decision == "tuning" else "finalizing",
    }


async def tuning_node(state: DeepModeHILState) -> Dict[str, Any]:
    """节点: 微调文章."""
    logger.info("[Node:tuning] Processing tuning request...")

    current_draft = state.get("current_draft", "")
    tuning_request = state.get("tuning_request", "")

    if not tuning_request:
        return {}

    full_message = f"""## 当前文章
{current_draft}

## 用户请求
{tuning_request}

## 回复格式
- 如果用户要求修改：直接返回完整修改后文章
- 如果用户只是提问：用【回答】开头

请处理：
"""

    try:
        llm = LLMClient()
        response = await llm.chat_with_retry(full_message)

        # 判断是否是修改
        is_question = response.startswith("【回答】")

        # 更新历史
        tuning_messages = state.get("tuning_messages", [])
        tuning_messages.append({
            "request": tuning_request,
            "response": response,
            "is_question": is_question,
        })

        # 如果是修改，更新草稿
        updated_draft = current_draft
        if not is_question and len(response) > len(current_draft) * 0.3:
            updated_draft = response

        logger.info(f"[Node:tuning] Response: {len(response)} chars, is_question={is_question}")

        return {
            "current_draft": updated_draft,
            "tuning_messages": tuning_messages,
            "tuning_request": None,  # 清空当前请求
            "stage": "waiting_content",  # 再次等待确认
        }
    except Exception as e:
        logger.error(f"[Node:tuning] Failed: {e}")
        return {"error": str(e)}


async def finalize_node(state: DeepModeHILState) -> Dict[str, Any]:
    """节点: 定稿."""
    logger.info("[Node:finalize] Finalizing...")

    current_draft = state.get("current_draft", "")

    return {
        "final_draft": current_draft,
        "stage": "completed",
    }


# ============================================================================
# Conditional Edges
# ============================================================================

def route_after_outline(state: DeepModeHILState) -> str:
    """大纲确认后的路由."""
    decision = state.get("user_decision", "")

    if decision == "approve":
        return "generate_content"
    elif decision == "reject":
        return "revise_outline"
    else:
        return "generate_content"  # 默认继续


def route_after_content(state: DeepModeHILState) -> str:
    """内容确认后的路由."""
    decision = state.get("user_decision", "")

    if decision == "finalize" or decision == "approve":
        return "finalize"
    elif decision == "tuning":
        return "tuning"
    else:
        return "finalize"  # 默认定稿


def route_after_tuning(state: DeepModeHILState) -> str:
    """微调后的路由."""
    # 如果还有 tuning_request，继续微调
    if state.get("tuning_request"):
        return "tuning"
    # 否则等待用户决策
    return "wait_content_approval"


# ============================================================================
# Build StateGraph with Human-in-the-Loop
# ============================================================================

def build_hil_graph() -> StateGraph:
    """构建 Human-in-the-Loop StateGraph."""
    graph = StateGraph(DeepModeHILState)

    # 添加节点
    graph.add_node("rag_search", rag_search_node)
    graph.add_node("generate_outline", generate_outline_node)
    graph.add_node("wait_outline_approval", wait_outline_approval_node)
    graph.add_node("revise_outline", revise_outline_node)
    graph.add_node("generate_content", generate_content_node)
    graph.add_node("wait_content_approval", wait_content_approval_node)
    graph.add_node("tuning", tuning_node)
    graph.add_node("finalize", finalize_node)

    # 添加边
    graph.add_edge(START, "rag_search")
    graph.add_edge("rag_search", "generate_outline")
    graph.add_edge("generate_outline", "wait_outline_approval")

    # 大纲确认后的条件路由
    graph.add_conditional_edges(
        "wait_outline_approval",
        route_after_outline,
        {
            "generate_content": "generate_content",
            "revise_outline": "revise_outline",
        }
    )

    # 修改大纲后，重新生成并等待确认
    graph.add_edge("revise_outline", "generate_outline")

    # 内容生成后等待确认
    graph.add_edge("generate_content", "wait_content_approval")

    # 内容确认后的条件路由
    graph.add_conditional_edges(
        "wait_content_approval",
        route_after_content,
        {
            "finalize": "finalize",
            "tuning": "tuning",
        }
    )

    # 微调后等待确认
    graph.add_conditional_edges(
        "tuning",
        route_after_tuning,
        {
            "tuning": "tuning",
            "wait_content_approval": "wait_content_approval",
        }
    )

    # 定稿后结束
    graph.add_edge("finalize", END)

    logger.info("[Graph] HIL StateGraph built")

    return graph


# ============================================================================
# Compiled App with Checkpointer (持久化)
# ============================================================================

_hil_graph = None
_hil_app = None
_checkpointer = None


def get_hil_graph() -> StateGraph:
    """获取 HIL StateGraph."""
    global _hil_graph
    if _hil_graph is None:
        _hil_graph = build_hil_graph()
    return _hil_graph


async def get_hil_app():
    """获取编译后的 HIL App（带 AsyncPostgresSaver 持久化）.

    使用 AsyncPostgresSaver 替代 MemorySaver，支持：
    1. HTTP API 和 WebSocket 共享同一个 thread_id
    2. LangSmith 显示完整流程 trace
    3. 会话状态持久化，支持断线恢复

    Checkpointer 用于保存状态，支持 interrupt 暂停和恢复。
    """
    global _hil_app, _checkpointer
    if _hil_app is None:
        graph = get_hil_graph()

        # 使用 AsyncPostgresSaver（持久化）
        try:
            _checkpointer = await get_checkpointer()
            logger.info("[Graph] HIL graph compiled with AsyncPostgresSaver")
        except Exception as e:
            logger.warning(f"[Graph] AsyncPostgresSaver unavailable, fallback to MemorySaver: {e}")
            _checkpointer = MemorySaver()

        _hil_app = graph.compile(checkpointer=_checkpointer)
        logger.info("[Graph] HIL graph compiled with checkpointer")
    return _hil_app


# ============================================================================
# Human-in-the-Loop API
# ============================================================================


async def _get_trace_headers(thread_id: str) -> Dict[str, str]:
    """从 checkpointer 获取 trace headers.

    headers 存储在 state 中，持久化到 checkpointer，确保跨请求共享。
    用于 ls.tracing_context(parent=headers) 合合 traces。
    """
    app = await get_hil_app()
    config = {"configurable": {"thread_id": thread_id}}

    snapshot = await app.aget_state(config)

    # LangGraph snapshot.values 是 dict，直接用 dict 访问方式
    values = dict(snapshot.values) if snapshot.values else {}
    trace_headers = values.get("trace_headers", {})

    logger.info(f"[HIL] _get_trace_headers: thread={thread_id}, headers keys={list(trace_headers.keys())}")

    return trace_headers


@ls.traceable(name="start_generation")
async def start_generation(
    thread_id: str,
    source_article: Dict[str, str],
    user_input: str,
) -> Dict[str, Any]:
    """启动生成流程（运行到第一个 interrupt）.

    Args:
        thread_id: 线程ID（用于 checkpointer 保存状态）
        source_article: 原文章
        user_input: 用户改写需求

    Returns:
        interrupt 返回值（大纲内容）
    """
    app = await get_hil_app()

    config = {
        "configurable": {"thread_id": thread_id},
    }

    initial_state = {
        "session_id": thread_id,
        "source_article": source_article,
        "user_input": user_input,
    }

    # 运行到第一个 interrupt
    result = await app.ainvoke(initial_state, config)

    # 获取当前 run_tree 的 headers（用于后续 resume 合合）
    run_tree = get_current_run_tree()
    if run_tree:
        trace_headers = run_tree.to_headers()
        # 存储 headers 到 checkpointer
        await app.aupdate_state(config, {"trace_headers": trace_headers})
        logger.info(f"[HIL] Saved trace_headers: {list(trace_headers.keys())}")

    # 如果遇到 interrupt，获取 interrupt 值（使用异步方法）
    snapshot = await app.aget_state(config)
    if snapshot.next:  # 有待执行的节点 = 遇到了 interrupt
        interrupt_value = snapshot.values
        logger.info(f"[HIL] Interrupted at outline approval: {interrupt_value.get('outline', '')[:100]}...")
        return {
            "status": "interrupted",
            "interrupt_type": "outline_approval",
            "outline": interrupt_value.get("outline", ""),
            "outline_version": interrupt_value.get("outline_version", 1),
        }

    return {"status": "completed", "result": result}


@ls.traceable(name="approve_outline")
async def approve_outline(
    thread_id: str,
    feedback: str = None,
) -> Dict[str, Any]:
    """用户确认大纲.

    Args:
        thread_id: 线程ID
        feedback: 修改意见（可选，如果提供则 reject）

    Returns:
        下一步状态（继续生成内容或重新修改大纲）
    """
    app = await get_hil_app()

    # 从 checkpointer state 获取已有的 trace_headers
    trace_headers = await _get_trace_headers(thread_id)

    config = {
        "configurable": {"thread_id": thread_id},
    }

    # 构建用户响应
    if feedback:
        user_response = {"decision": "reject", "feedback": feedback}
    else:
        user_response = {"decision": "approve"}

    # 提供用户响应，继续执行（使用异步方法）
    await app.aupdate_state(
        config,
        {"user_decision": user_response["decision"], "outline_feedback": feedback or ""},
    )

    # 使用 tracing_context 合合到原有 trace
    with ls.tracing_context(parent=trace_headers):
        result = await app.ainvoke(Command(resume=user_response), config)

    # 更新 trace_headers（新的 run_tree headers）
    run_tree = get_current_run_tree()
    if run_tree:
        new_headers = run_tree.to_headers()
        await app.aupdate_state(config, {"trace_headers": new_headers})
        logger.info(f"[HIL] Updated trace_headers after approve_outline")

    # 检查是否又遇到 interrupt（使用异步方法）
    snapshot = await app.aget_state(config)
    if snapshot.next:
        interrupt_value = snapshot.values
        stage = interrupt_value.get("stage", "")

        if stage == "waiting_content":
            return {
                "status": "interrupted",
                "interrupt_type": "content_approval",
                "draft": interrupt_value.get("current_draft", ""),
            }
        elif stage == "waiting_outline":
            return {
                "status": "interrupted",
                "interrupt_type": "outline_approval",
                "outline": interrupt_value.get("outline", ""),
                "outline_version": interrupt_value.get("outline_version", 1),
            }

    return {"status": "completed", "result": result}


@ls.traceable(name="approve_content")
async def approve_content(
    thread_id: str,
    tuning_request: str = None,
) -> Dict[str, Any]:
    """用户确认内容.

    Args:
        thread_id: 线程ID
        tuning_request: 微调请求（可选，如果提供则进入微调）

    Returns:
        下一步状态（定稿或继续微调）
    """
    app = await get_hil_app()

    # 从 checkpointer state 获取已有的 trace_headers
    trace_headers = await _get_trace_headers(thread_id)

    config = {
        "configurable": {"thread_id": thread_id},
    }

    # 构建用户响应
    if tuning_request:
        user_response = {"decision": "tuning", "tuning_request": tuning_request}
    else:
        user_response = {"decision": "finalize"}

    # 更新状态并继续（使用异步方法）
    await app.aupdate_state(
        config,
        {"user_decision": user_response["decision"], "tuning_request": tuning_request or ""},
    )

    # 使用 tracing_context 合合到原有 trace
    with ls.tracing_context(parent=trace_headers):
        result = await app.ainvoke(Command(resume=user_response), config)

    # 更新 trace_headers
    run_tree = get_current_run_tree()
    if run_tree:
        new_headers = run_tree.to_headers()
        await app.aupdate_state(config, {"trace_headers": new_headers})
        logger.info(f"[HIL] Updated trace_headers after approve_content")

    # 检查是否又遇到 interrupt（使用异步方法）
    snapshot = await app.aget_state(config)
    if snapshot.next:
        interrupt_value = snapshot.values
        return {
            "status": "interrupted",
            "interrupt_type": "content_approval",
            "draft": interrupt_value.get("current_draft", ""),
        }

    # 定稿完成
    logger.info(f"[HIL] Session completed: {thread_id}")

    return {
        "status": "completed",
        "final_draft": result.get("final_draft", ""),
    }


async def get_current_state(thread_id: str) -> Dict[str, Any]:
    """获取当前状态（用于查看 interrupt 点）。"""
    app = await get_hil_app()
    config = {"configurable": {"thread_id": thread_id}}

    snapshot = await app.aget_state(config)
    return snapshot.values


async def reset_thread(thread_id: str) -> None:
    """重置线程（清除状态）。"""
    app = await get_hil_app()
    config = {"configurable": {"thread_id": thread_id}}

    # AsyncPostgresSaver 支持删除 checkpoint
    try:
        await app.checkpointer.adelete(config)
        logger.info(f"[HIL] Thread checkpoint deleted: {thread_id}")
    except Exception as e:
        logger.warning(f"[HIL] Thread delete failed: {e}")