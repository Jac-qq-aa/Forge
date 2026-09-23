# forge/deep_mode/graph_hil.py

"""LangGraph StateGraph with Human-in-the-Loop.

使用 interrupt 功能在关键节点暂停，等待用户确认后继续。

流程：
1. START → rag_search → research → interrupt（等待审核事实）
2. 用户确认事实后 → generate_outline → interrupt（等待确认大纲）
3. 用户确认大纲后 → reflection → interrupt（等待确认定稿）
4. 用户确认后 → tuning（可选循环） → finalize → END

Human-in-the-Loop 暂停点：
- research 后：等待用户确认、排除、质疑事实或要求补充研究
- generate_outline 后：等待用户确认/修改大纲
- reflection 后：展示事实来源与质量报告，等待用户确认定稿或继续微调
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
from forge.agents.research_agent import run_research_agent
from forge.agents.research_types import (
    apply_fact_reviews,
    format_verified_facts,
    is_fact_approved,
)
from forge.agents.reflection_writer import (
    critic_node,
    make_word_count_report,
    run_reflection_writer,
)
from forge.agents.revision_agent import (
    answer_tuning_question,
    parse_tuning_intent,
    reconcile_issue_resolutions,
    run_revision_agent,
)
from forge.memory import get_preference_memory_service
from forge.memory import build_experience_query, get_experience_memory_manager
from forge.memory.service import DEFAULT_MEMORY_OWNER_ID
from forge.reference_articles import ReferencePack, ReferenceRequest, get_reference_article_library

logger = logging.getLogger(__name__)


def _memory_scope(state: Dict[str, Any]) -> Dict[str, str]:
    source_article = state.get("source_article", {})
    return {
        "memory_owner_id": state.get("memory_owner_id") or DEFAULT_MEMORY_OWNER_ID,
        "target_platform": str(state.get("target_platform") or "zhihu_article"),
        "content_type": str(source_article.get("content_type") or "general"),
    }


async def _get_memory_context(state: Dict[str, Any], stage: str) -> str:
    try:
        preference_context = await get_preference_memory_service().get_preference_context(
            stage=stage,
            **_memory_scope(state),
        )
        experience_context = await get_experience_memory_manager().get_context(
            query=build_experience_query(state, stage),
            stage=stage,
            **_memory_scope(state),
        )
        return "\n\n".join(
            part for part in (preference_context, experience_context) if part
        )
    except Exception as exc:
        logger.warning("[PreferenceMemory] Context lookup failed: %s", exc)
        return ""


async def _record_memory_event(
    state: Dict[str, Any],
    *,
    stage: str,
    instruction: str,
    before_text: str,
    after_text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        await get_preference_memory_service().record_edit_event(
            session_id=str(state.get("session_id") or ""),
            stage=stage,
            instruction=instruction,
            before_text=before_text,
            after_text=after_text,
            metadata=metadata,
            **_memory_scope(state),
        )
    except Exception as exc:
        logger.warning("[PreferenceMemory] Event recording failed: %s", exc)


# ============================================================================
# State Definition
# ============================================================================

class DeepModeHILState(Dict):
    """深度模式 Human-in-the-Loop 状态。

    所有字段都是可选的。
    """

    # 会话信息
    session_id: Optional[str]
    memory_owner_id: Optional[str]
    gateway_trace_headers: Optional[Dict[str, str]]

    # 原文章
    source_article: Optional[Dict[str, str]]

    # 用户输入
    user_input: Optional[str]
    target_platform: Optional[str]
    reference_pack: Optional[Dict[str, Any]]

    # 大纲
    outline: Optional[str]
    outline_version: Optional[int]
    outline_thinking: Optional[str]  # 大纲修改的思考过程

    # 用户反馈（大纲修改）
    outline_feedback: Optional[str]

    # RAG 素材
    rag_context: Optional[str]

    # 结构化研究与质量审查
    research_result: Optional[Dict[str, Any]]
    fact_decision: Optional[Literal["approve", "update", "research_more"]]
    fact_reviews: Optional[List[Dict[str, Any]]]
    fact_research_request: Optional[str]
    quality_report: Optional[Dict[str, Any]]

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
    approved_facts = format_verified_facts(state.get("research_result", {}))
    preference_context = await _get_memory_context(state, "outline")
    source_text = str(source_article.get("text") or source_article.get("content") or "")
    try:
        reference_pack = await get_reference_article_library().select_reference_pack(
            ReferenceRequest(
                topic=user_input or str(source_article.get("title") or ""),
                target_platform=str(state.get("target_platform") or "zhihu_article"),
                content_type=str(source_article.get("content_type") or "general"),
                source_title=str(source_article.get("title") or ""),
                source_excerpt=source_text[:800],
            )
        )
    except Exception as exc:
        logger.warning("[ReferenceArticles] Reference selection failed: %s", exc)
        reference_pack = ReferencePack.empty()
    effective_user_input = "\n\n".join(
        part for part in (user_input, preference_context) if part
    )

    prompt = f"""请根据以下信息生成文章大纲：

## 原文章内容
标题：{source_article.get('title', '')}
内容：{source_article.get('text', '')[:1500]}

## 用户改写需求
{effective_user_input}

## 知识库素材
{rag_context or "无"}

## 用户已确认的事实清单
{approved_facts}

## 参考文章库给出的结构与风格约束
{reference_pack.prompt_context()}

## 大纲要求
1. 保留原文核心观点
2. 结构：一、二、三、四（带二级标题）
3. 只能依据用户已确认的事实设计事实性章节
4. 每个一级标题末尾标注该章节使用的 Fact ID，例如：一、项目背景【事实：F001,F002】
5. 不得使用 excluded、challenged、unsupported 或未确认的事实
6. 参考文章只用于结构角色和表达节奏，不得复制其句子、事实、数字或案例

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
            "reference_pack": reference_pack.to_dict(),
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
        "research_result": state.get("research_result", {}),
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

        await _record_memory_event(
            state,
            stage="outline",
            instruction=feedback,
            before_text=current_outline,
            after_text=final_outline,
            metadata={"outline_version": outline_version},
        )

        return {
            "outline": final_outline,
            "outline_thinking": thinking,
            "outline_version": outline_version,
            "stage": "waiting_outline",  # 再次等待确认
        }
    except Exception as e:
        logger.error(f"[Node:revise_outline] Failed: {e}")
        return {"error": str(e)}


def _carry_fact_reviews(
    previous: Dict[str, Any],
    current: Dict[str, Any],
) -> Dict[str, Any]:
    """Carry explicit user decisions when supplemental research returns the same claim."""
    review_by_claim = {
        str(fact.get("claim", "")).strip(): fact.get("review")
        for fact in previous.get("facts", [])
        if str(fact.get("claim", "")).strip() and isinstance(fact.get("review"), dict)
    }
    for fact in current.get("facts", []):
        previous_review = review_by_claim.get(str(fact.get("claim", "")).strip())
        if previous_review:
            fact["review"] = dict(previous_review)
    return current


async def research_node(state: DeepModeHILState) -> Dict[str, Any]:
    """节点: 在大纲生成前建立带来源的结构化事实清单。"""
    logger.info("[Node:research] Planning and gathering evidence...")
    source_article = state.get("source_article", {})
    supplemental_request = str(state.get("fact_research_request") or "").strip()
    research_brief = "\n".join(
        part
        for part in (
            f"文章主题：{source_article.get('title', '')}",
            f"用户写作要求：{state.get('user_input', '')}",
            f"补充研究要求：{supplemental_request}" if supplemental_request else "",
        )
        if part
    )
    result = await run_research_agent(
        outline=research_brief,
        raw_content=source_article,
        user_input=state.get("user_input", ""),
        require_user_review=True,
        rag_context=state.get("rag_context", ""),
    )
    previous_result = state.get("research_result", {})
    if supplemental_request and previous_result:
        result = _carry_fact_reviews(previous_result, result)
    return {
        "research_result": result,
        "fact_research_request": None,
        "stage": "waiting_facts",
    }


async def wait_fact_approval_node(state: DeepModeHILState) -> Dict[str, Any]:
    """节点: 展示事实与证据并等待用户审核。"""
    logger.info("[Node:wait_fact_approval] Waiting for user fact review...")
    research_result = state.get("research_result", {})
    user_response = interrupt({
        "type": "fact_approval",
        "research_result": research_result,
        "message": "请审核事实清单。您可以确认、排除、质疑或要求补充研究。",
    })

    decision = str(user_response.get("decision") or "update")
    reviews = user_response.get("reviews") or []
    supplemental_request = str(user_response.get("research_request") or "").strip()
    updated_result, review_errors = apply_fact_reviews(
        research_result,
        reviews,
        accept_all=decision == "approve" and not reviews,
    )
    accepted_count = sum(
        1 for fact in updated_result.get("facts", []) if is_fact_approved(fact)
    )
    unresolved = [
        fact["fact_id"]
        for fact in updated_result.get("facts", [])
        if fact["review"]["decision"] in {"challenged", "needs_more_research"}
    ]

    effective_decision = decision
    if decision == "approve" and (review_errors or unresolved or accepted_count == 0):
        effective_decision = "update"
    if decision == "research_more" and not supplemental_request:
        review_comments = [
            str(review.get("comment") or "").strip()
            for review in reviews
            if review.get("action") in {"challenged", "needs_more_research"}
        ]
        supplemental_request = "；".join(comment for comment in review_comments if comment)
    if review_errors:
        updated_result["warnings"].extend(review_errors)

    logger.info(
        "[Node:wait_fact_approval] decision=%s accepted=%s unresolved=%s errors=%s",
        effective_decision,
        accepted_count,
        len(unresolved),
        len(review_errors),
    )
    return {
        "research_result": updated_result,
        "fact_decision": effective_decision,
        "fact_reviews": reviews,
        "fact_research_request": supplemental_request or None,
        "stage": "facts_approved" if effective_decision == "approve" else "waiting_facts",
    }


async def reflection_node(state: DeepModeHILState) -> Dict[str, Any]:
    """节点: 基于事实注册表生成草稿并执行结构化质量门。"""
    logger.info("[Node:reflection] Generating and evaluating draft...")
    preference_context = await _get_memory_context(state, "article")
    effective_user_input = "\n\n".join(
        part
        for part in (state.get("user_input", ""), preference_context)
        if part
    )
    result = await run_reflection_writer(
        research_result=state.get("research_result", {}),
        outline=state.get("outline", ""),
        raw_content=state.get("source_article", {}),
        target_platform=str(state.get("target_platform") or "zhihu_article"),
        user_input=effective_user_input,
        reference_pack=ReferencePack.from_dict(state.get("reference_pack")),
    )
    return {
        "draft_v1": result["draft"],
        "current_draft": result["draft"],
        "quality_report": result,
        "stage": "waiting_content",
    }


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
        "research_result": state.get("research_result", {}),
        "quality_report": state.get("quality_report", {}),
        "tuning_messages": state.get("tuning_messages", []),
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
    """Parse user intent and route edits through the shared Revision Agent."""
    logger.info("[Node:tuning] Parsing tuning intent...")

    current_draft = state.get("current_draft", "")
    tuning_request = state.get("tuning_request", "")

    if not tuning_request:
        return {}

    try:
        quality_report = state.get("quality_report", {})
        generation_plan = quality_report.get("generation_plan", {})
        research_result = state.get("research_result", {})
        intent = await parse_tuning_intent(
            tuning_request,
            generation_plan,
            research_result,
        )
        tuning_messages = state.get("tuning_messages", [])
        if intent["intent"] == "question":
            response = await answer_tuning_question(intent["question"], research_result)
            tuning_messages.append({
                "request": tuning_request,
                "response": response,
                "is_question": True,
                "intent": intent,
            })
            return {
                "current_draft": current_draft,
                "quality_report": {**quality_report, "last_tuning_intent": intent},
                "tuning_messages": tuning_messages,
                "tuning_request": None,
                "stage": "waiting_content",
            }

        if intent["intent"] == "clarify":
            response = intent["clarification_question"]
            tuning_messages.append({
                "request": tuning_request,
                "response": response,
                "is_question": True,
                "intent": intent,
            })
            return {
                "current_draft": current_draft,
                "quality_report": {**quality_report, "last_tuning_intent": intent},
                "tuning_messages": tuning_messages,
                "tuning_request": None,
                "stage": "waiting_content",
            }

        revision_cycle = int(quality_report.get("revision_cycles", 0)) + 1
        agent_result = await run_revision_agent(
            draft=current_draft,
            research_result=research_result,
            raw_content=state.get("source_article", {}),
            generation_plan=generation_plan,
            section_reports=quality_report.get("section_reports", []),
            writing_memory=quality_report.get("writing_memory", {}),
            source="user",
            revision_cycle=revision_cycle,
            tasks=intent["tasks"],
        )
        updated_draft = agent_result["draft"]
        word_count = make_word_count_report(updated_draft, agent_result["generation_plan"])
        agent_issues = []
        if agent_result["status"] in {"blocked", "needs_review"}:
            agent_issues.append({
                "issue_id": "I001",
                "issue_type": "revision_agent_error",
                "severity": "high",
                "span": "",
                "fact_id": "",
                "instruction": "Revision Agent 未能安全完成用户修改："
                + "；".join(agent_result["errors"] or agent_result["unresolved_issues"]),
            })
        review = await critic_node({
            "current_draft": updated_draft,
            "research_result": research_result,
            "outline": state.get("outline", ""),
            "raw_content": state.get("source_article", {}),
            "target_platform": str(state.get("target_platform") or "zhihu_article"),
            "user_input": state.get("user_input", ""),
            "generation_plan": agent_result["generation_plan"],
            "section_reports": agent_result["section_reports"],
            "writing_memory": agent_result["writing_memory"],
            "word_count": word_count,
            "issues": agent_issues,
        })
        response = (
            f"Revision Agent 执行了 {agent_result['tool_action_count']} 个动作；"
            f"Critic 状态：{review.get('status', 'needs_review')}。"
        )
        tuning_messages.append({
            "request": tuning_request,
            "response": response,
            "is_question": False,
            "intent": intent,
            "revision_plan": agent_result["revision_plan"],
        })
        resolutions, unresolved = reconcile_issue_resolutions(
            agent_result["issue_resolution_log"],
            review.get("issues", []),
            bool(review.get("approved", False)),
        )
        quality_report = {
            **quality_report,
            "status": review.get("status", "needs_review"),
            "approved": review.get("approved", False),
            "scores": review.get("scores", {}),
            "issues": review.get("issues", []),
            "draft_facts": review.get("draft_facts", []),
            "fact_match_summary": review.get("fact_match_summary", {}),
            "verification_log": review.get("verification_log", []),
            "generation_plan": agent_result["generation_plan"],
            "word_count": word_count,
            "section_reports": agent_result["section_reports"],
            "writing_memory": agent_result["writing_memory"],
            "revision_plan": agent_result["revision_plan"],
            "revision_action_log": quality_report.get("revision_action_log", [])
            + agent_result["revision_action_log"],
            "issue_resolution_log": quality_report.get("issue_resolution_log", [])
            + resolutions,
            "revision_cycles": revision_cycle,
            "tool_action_count": int(quality_report.get("tool_action_count", 0))
            + agent_result["tool_action_count"],
            "unresolved_issues": sorted(
                set(agent_result["unresolved_issues"]) | set(unresolved)
            ),
            "last_tuning_intent": intent,
            "checked_after_tuning": True,
        }

        logger.info(
            "[Node:tuning] intent=edit actions=%s critic=%s",
            agent_result["tool_action_count"],
            review.get("status"),
        )

        await _record_memory_event(
            state,
            stage="article",
            instruction=tuning_request,
            before_text=current_draft,
            after_text=updated_draft,
            metadata={
                "intent": intent.get("intent"),
                "revision_cycle": revision_cycle,
                "critic_status": review.get("status"),
            },
        )

        return {
            "current_draft": updated_draft,
            "quality_report": quality_report,
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
    session_id = str(state.get("session_id") or "")
    try:
        learned_count = await get_preference_memory_service().learn_from_finalized_session(
            session_id
        )
        logger.info(
            "[PreferenceMemory] Learned %s edit events from session %s",
            learned_count,
            session_id,
        )
    except Exception as exc:
        logger.warning("[PreferenceMemory] Finalization learning failed: %s", exc)

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
        return "reflection"
    elif decision == "reject":
        return "revise_outline"
    else:
        return "reflection"  # 默认继续


def route_after_facts(state: DeepModeHILState) -> str:
    """事实审核后的路由。"""
    decision = state.get("fact_decision", "")
    if decision == "approve":
        return "generate_outline"
    if decision == "research_more":
        return "research"
    return "wait_fact_approval"


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
    graph.add_node("research", research_node)
    graph.add_node("wait_fact_approval", wait_fact_approval_node)
    graph.add_node("generate_outline", generate_outline_node)
    graph.add_node("wait_outline_approval", wait_outline_approval_node)
    graph.add_node("revise_outline", revise_outline_node)
    graph.add_node("reflection", reflection_node)
    graph.add_node("wait_content_approval", wait_content_approval_node)
    graph.add_node("tuning", tuning_node)
    graph.add_node("finalize", finalize_node)

    # 添加边
    graph.add_edge(START, "rag_search")
    graph.add_edge("rag_search", "research")
    graph.add_edge("research", "wait_fact_approval")
    graph.add_conditional_edges(
        "wait_fact_approval",
        route_after_facts,
        {
            "generate_outline": "generate_outline",
            "research": "research",
            "wait_fact_approval": "wait_fact_approval",
        }
    )
    graph.add_edge("generate_outline", "wait_outline_approval")

    # 大纲确认后的条件路由
    graph.add_conditional_edges(
        "wait_outline_approval",
        route_after_outline,
        {
            "reflection": "reflection",
            "revise_outline": "revise_outline",
        }
    )

    # 修改大纲后，直接等待用户确认（不再重新生成）
    graph.add_edge("revise_outline", "wait_outline_approval")

    # 大纲确认后通过 Reflection 生成并审查草稿
    graph.add_edge("reflection", "wait_content_approval")

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
    target_platform: str = "zhihu_article",
    memory_owner_id: str = DEFAULT_MEMORY_OWNER_ID,
) -> Dict[str, Any]:
    """启动生成流程（运行到第一个 interrupt）.

    Args:
        thread_id: 线程ID（用于 checkpointer 保存状态）
        source_article: 原文章
        user_input: 用户改写需求

    Returns:
        interrupt 返回值（事实清单）
    """
    app = await get_hil_app()

    config = {
        "configurable": {"thread_id": thread_id},
    }

    initial_state = {
        "session_id": thread_id,
        "memory_owner_id": memory_owner_id,
        "source_article": source_article,
        "user_input": user_input,
        "target_platform": target_platform,
    }

    # 运行到第一个 interrupt
    result = await app.ainvoke(initial_state, config)

    # 如果遇到 interrupt，获取 interrupt 值（使用异步方法）
    snapshot = await app.aget_state(config)
    if snapshot.next:  # 有待执行的节点 = 遇到了 interrupt
        interrupt_value = snapshot.values
        interrupt_type = interrupt_value.get("type", "fact_approval")
        logger.info("[HIL] Interrupted at %s", interrupt_type)
        if interrupt_type == "fact_approval":
            return {
                "status": "interrupted",
                "interrupt_type": "fact_approval",
                "research_result": interrupt_value.get("research_result", {}),
            }
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
                "research_result": interrupt_value.get("research_result", {}),
                "quality_report": interrupt_value.get("quality_report", {}),
                "tuning_messages": interrupt_value.get("tuning_messages", []),
            }
        elif stage == "waiting_outline":
            return {
                "status": "interrupted",
                "interrupt_type": "outline_approval",
                "outline": interrupt_value.get("outline", ""),
                "outline_version": interrupt_value.get("outline_version", 1),
            }

    return {"status": "completed", "result": result}


async def approve_facts(
    thread_id: str,
    decision: str = "approve",
    reviews: Optional[List[Dict[str, Any]]] = None,
    research_request: str = "",
) -> Dict[str, Any]:
    """提交事实审核结果并返回下一个 HIL 暂停点。"""
    app = await get_hil_app()
    config = {"configurable": {"thread_id": thread_id}}
    resume_value = {
        "decision": decision,
        "reviews": reviews or [],
        "research_request": research_request,
    }
    result = await app.ainvoke(Command(resume=resume_value), config)
    snapshot = await app.aget_state(config)
    if snapshot.next:
        interrupt_value = snapshot.values
        stage = interrupt_value.get("stage", "")
        if stage != "waiting_outline":
            return {
                "status": "interrupted",
                "interrupt_type": "fact_approval",
                "research_result": interrupt_value.get("research_result", {}),
            }
        if stage == "waiting_outline":
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
            "research_result": interrupt_value.get("research_result", {}),
            "quality_report": interrupt_value.get("quality_report", {}),
            "tuning_messages": interrupt_value.get("tuning_messages", []),
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
