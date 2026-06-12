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
"""

import logging
from typing import Dict, Any, Optional, Literal, List

from langgraph.graph import StateGraph, END
from langgraph.constants import START
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import MemorySaver

from forge.tools.llm_client import LLMClient
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
    gateway_trace_headers: Optional[Dict[str, str]]

    # 原文章
    source_article: Optional[Dict[str, str]]

    # 用户输入
    user_input: Optional[str]

    # 大纲
    outline: Optional[str]
    outline_version: Optional[int]
    outline_thinking: Optional[str]  # 大纲修改的思考过程

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

    # DEBUG: 打印完整状态
    logger.info(f"[Node:revise_outline] DEBUG - Full state keys: {list(state.keys())}")
    logger.info(f"[Node:revise_outline] DEBUG - user_decision: {state.get('user_decision', 'NOT_FOUND')}")
    logger.info(f"[Node:revise_outline] DEBUG - outline_feedback: '{feedback}'")
    logger.info(f"[Node:revise_outline] DEBUG - feedback length: {len(feedback)}")
    logger.info(f"[Node:revise_outline] DEBUG - Current outline preview: {current_outline[:200]}...")

    # 检查 feedback 是否为空
    if not feedback or feedback.strip() == "":
        logger.warning("[Node:revise_outline] Empty feedback! Skipping revision, returning original outline")
        return {
            "outline": current_outline,
            "outline_version": state.get("outline_version", 0) + 1,
            "stage": "waiting_outline",
        }

    prompt = f"""请根据用户反馈修改大纲。

## 当前大纲
{current_outline}

## 用户反馈
{feedback}

## 输出格式要求

请按以下格式输出：

<thinking>
在此写出你的思考过程：
1. 分析用户的需求是什么操作（删除、增加、修改、调整顺序）
2. 确认操作会影响哪些内容
3. 检查是否会影响其他未提及的内容
4. 确定最终大纲的点数变化
</thinking>

<outline>
在此输出修改后的完整大纲
</outline>

## ⚠️ 关键规则

### 删除操作（最高优先级）
当用户要求删除某一点时：
1. **点数必须减少**：删除第N点后，大纲总点数 = 原点数 - 1
2. **禁止添加新内容**：删除操作不需要补充新点
3. **禁止修改其他点**：未提及的点保持原样不动
4. **保持编号格式**：使用一、二、三、四...格式

删除示例：
- 原大纲有4点：一、二、三、四
- 用户说"删除第三点"
- 结果必须变成3点：一、二、四（跳过三，不补新点）

### 其他操作
- 增加：用户明确要求"添加"或"增加"时才添加新内容
- 修改：只修改用户指定的内容，其他保持不变
- 调整顺序：用户明确要求调整顺序时才调整
"""

    try:
        llm = LLMClient()
        revised = await llm.chat_with_retry(prompt)
        outline_version = state.get("outline_version", 0) + 1

        # === 诊断日志：打印 LLM 原始输出 ===
        logger.info(f"[Node:revise_outline] ========== LLM RAW OUTPUT START ==========")
        logger.info(f"[Node:revise_outline] {revised}")
        logger.info(f"[Node:revise_outline] ========== LLM RAW OUTPUT END ==========")
        logger.info(f"[Node:revise_outline] Revised length: {len(revised)} chars")

        # 解析思考过程和最终大纲
        thinking = ""
        final_outline = revised

        # 提取 <thinking> 部分
        import re
        thinking_match = re.search(r'<thinking>(.*?)</thinking>', revised, re.DOTALL)
        if thinking_match:
            thinking = thinking_match.group(1).strip()
            logger.info(f"[Node:revise_outline] ✓ Found <thinking> tag, length: {len(thinking)} chars")
        else:
            logger.warning(f"[Node:revise_outline] ✗ NO <thinking> tag found!")

        # 提取 <outline> 部分
        outline_match = re.search(r'<outline>(.*?)</outline>', revised, re.DOTALL)
        if outline_match:
            final_outline = outline_match.group(1).strip()
            logger.info(f"[Node:revise_outline] ✓ Found <outline> tag, length: {len(final_outline)} chars")
        else:
            logger.warning(f"[Node:revise_outline] ✗ NO <outline> tag found! Using full output as outline")

        # === 诊断日志：打印原大纲和新大纲对比 ===
        original_points = len(re.findall(r'^[一二三四五六七八九十]、', current_outline, re.MULTILINE))
        new_points = len(re.findall(r'^[一二三四五六七八九十]、', final_outline, re.MULTILINE))
        logger.info(f"[Node:revise_outline] ========== POINT COUNT COMPARISON ==========")
        logger.info(f"[Node:revise_outline] Original outline points: {original_points}")
        logger.info(f"[Node:revise_outline] New outline points: {new_points}")
        logger.info(f"[Node:revise_outline] User feedback: '{feedback}'")
        logger.info(f"[Node:revise_outline] ========== END COMPARISON ==========")

        # === 诊断日志：打印解析后的最终大纲 ===
        logger.info(f"[Node:revise_outline] ========== FINAL OUTLINE START ==========")
        logger.info(f"[Node:revise_outline] {final_outline}")
        logger.info(f"[Node:revise_outline] ========== FINAL OUTLINE END ==========")

        return {
            "outline": final_outline,
            "outline_thinking": thinking,
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

    # 修改大纲后，直接等待用户确认（不再重新生成）
    graph.add_edge("revise_outline", "wait_outline_approval")

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
    2. 会话状态持久化，支持断线恢复

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

    result = await app.ainvoke(Command(resume=user_response), config)

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

    result = await app.ainvoke(Command(resume=user_response), config)

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
