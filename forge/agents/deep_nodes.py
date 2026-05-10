"""深度模式节点 - 将 deep_mode workflow 函数包装为 StateGraph 节点。

这些节点用于 Unified Workflow 的深度生成分支：
- deep_outline_generator: 大纲生成
- human_review: 用户决策占位节点（interrupt 点）
- deep_outline_reviser: 大纲修改
- deep_content_generator: 内容生成
- tuning_agent: 微调对话 Agent
- finalize_node: 定稿节点
"""

import logging
from langsmith import traceable
from typing import Dict, Any

from forge.graph.state import UnifiedState, STAGE_WAITING_OUTLINE, STAGE_TUNING, STAGE_COMPLETED
from forge.deep_mode.workflow import (
    rag_search,
    generate_outline,
    revise_outline,
    generate_content,
    run_tuning_agent,
)
from forge.config import OUTLINE_MAX_REVISIONS
from forge.evaluation.probe_decorator import with_probe

logger = logging.getLogger(__name__)


# ============================================================================
# 大纲生成节点
# ============================================================================

@traceable(name="Deep_Outline_Generator")
@with_probe("deep_outline_generator")
async def deep_outline_generator_node(state: UnifiedState) -> dict:
    """生成大纲节点。

    输入：
    - raw_content: 原始内容
    - user_input: 用户改写需求

    输出：
    - outline: 生成的大纲
    - outline_version: 1
    - rag_context: RAG 知识库素材
    - stage: waiting_outline
    """
    raw_content = state.get("raw_content", {})
    user_input = state.get("user_input", "")

    logger.info("[Deep_Outline_Generator] Starting outline generation")
    logger.info(f"[Deep_Outline_Generator] User input: {user_input[:50]}...")

    # RAG 搜索
    try:
        rag_context = await rag_search(raw_content)
        logger.info(f"[Deep_Outline_Generator] RAG context: {len(rag_context)} chars")
    except Exception as e:
        logger.warning(f"[Deep_Outline_Generator] RAG search failed: {e}")
        rag_context = ""

    # 生成大纲
    try:
        outline = await generate_outline(raw_content, user_input, rag_context)
        logger.info(f"[Deep_Outline_Generator] Outline generated: {len(outline)} chars")
    except Exception as e:
        logger.error(f"[Deep_Outline_Generator] Outline generation failed: {e}")
        outline = f"大纲生成失败: {e}"

    return {
        "outline": outline,
        "outline_version": 1,
        "rag_context": rag_context,
        "stage": STAGE_WAITING_OUTLINE,
    }


# ============================================================================
# 用户决策占位节点（interrupt 点）
# ============================================================================

async def human_review_node(state: UnifiedState) -> dict:
    """用户决策占位节点。

    此节点不执行实际操作，只是作为 interrupt_before 的暂停点。
    用户通过 API 传入 human_decision 参数继续执行。

    LangGraph 在 interrupt_before 指定的节点前暂停，
    等待用户通过 ainvoke 继续执行。

    输入：
    - human_decision: 用户决策（通过 API 传入）

    输出：
    - 无（状态不变）
    """
    # 此节点只是占位，不执行实际操作
    # 用户决策通过 API 传入，在路由函数中使用
    logger.info("[Human_Review] Waiting for user decision")
    return {}


# ============================================================================
# 大纲修改节点
# ============================================================================

@traceable(name="Deep_Outline_Reviser")
@with_probe("deep_outline_reviser")
async def deep_outline_reviser_node(state: UnifiedState) -> dict:
    """大纲修改节点。

    输入：
    - outline: 当前大纲
    - human_decision: 用户修改意见（可能带 "modify:" 前缀）

    输出：
    - outline: 修改后的大纲
    - outline_version: 版本号 +1
    """
    outline = state.get("outline", "")
    human_decision = state.get("human_decision", "")
    outline_version = state.get("outline_version", 1)

    # 处理 human_decision：去掉 "modify:" 前缀（如果存在）
    user_feedback = human_decision
    if human_decision.startswith("modify:"):
        user_feedback = human_decision[7:]  # 去掉 "modify:" 前缀（7个字符）

    logger.info(f"[Deep_Outline_Reviser] Revising outline (v{outline_version})")
    logger.info(f"[Deep_Outline_Reviser] User feedback: {user_feedback[:50]}...")

    # 检查 feedback 是否为空
    if not user_feedback or user_feedback.strip() == "":
        logger.warning("[Deep_Outline_Reviser] Empty user feedback, keeping original outline")
        return {
            "outline": outline,
            "outline_version": outline_version,
            "stage": STAGE_WAITING_OUTLINE,
        }

    try:
        revised_outline = await revise_outline(outline, user_feedback)
        logger.info(f"[Deep_Outline_Reviser] Outline revised: {len(revised_outline)} chars")
    except Exception as e:
        logger.error(f"[Deep_Outline_Reviser] Revision failed: {e}")
        revised_outline = outline  # 保持原大纲

    return {
        "outline": revised_outline,
        "outline_version": outline_version + 1,
        "stage": STAGE_WAITING_OUTLINE,  # 重新等待用户确认
    }


# ============================================================================
# 内容生成节点
# ============================================================================

@traceable(name="Deep_Content_Generator")
@with_probe("deep_content_generator")
async def deep_content_generator_node(state: UnifiedState) -> dict:
    """内容生成节点。

    输入：
    - outline: 大纲
    - raw_content: 原始内容
    - rag_context: RAG 知识库素材

    输出：
    - current_draft: 生成的草稿
    - rewritten_draft: 兼容字段（用于后续节点）
    - draft_v1: 初版草稿
    - stage: tuning
    """
    outline = state.get("outline", "")
    raw_content = state.get("raw_content", {})
    rag_context = state.get("rag_context", "")

    logger.info("[Deep_Content_Generator] Starting content generation")
    logger.info(f"[Deep_Content_Generator] Outline: {len(outline)} chars")

    try:
        draft = await generate_content(outline, raw_content, rag_context)
        logger.info(f"[Deep_Content_Generator] Draft generated: {len(draft)} chars")
    except Exception as e:
        logger.error(f"[Deep_Content_Generator] Content generation failed: {e}")
        draft = f"内容生成失败: {e}"

    return {
        "current_draft": draft,
        "rewritten_draft": draft,  # 兼容后续节点（Director 等）
        "draft_v1": draft,  # 保存初版
        "stage": STAGE_TUNING,
    }


# ============================================================================
# 微调对话 Agent 节点
# ============================================================================

@traceable(name="Tuning_Agent")
@with_probe("tuning_agent")
async def tuning_agent_node(state: UnifiedState) -> dict:
    """微调对话 Agent 节点。

    输入：
    - current_draft: 当前草稿
    - human_decision: 用户微调请求

    输出：
    - current_draft: 修改后的草稿（如有）
    - tuning_messages: 对话历史
    """
    current_draft = state.get("current_draft", "")
    human_decision = state.get("human_decision", "")

    logger.info("[Tuning_Agent] Processing user request")
    logger.info(f"[Tuning_Agent] User message: {human_decision[:50]}...")

    try:
        response = await run_tuning_agent(current_draft, human_decision)
        logger.info(f"[Tuning_Agent] Response: {response[:100]}...")

        # 根据响应判断是否修改了文章
        if response.startswith("【回答】"):
            # 只是问答，不修改草稿
            return {
                "stage": STAGE_TUNING,  # 继续微调
            }
        else:
            # 返回了修改后的文章
            return {
                "current_draft": response,
                "rewritten_draft": response,  # 同步更新兼容字段
                "stage": STAGE_TUNING,  # 继续等待用户决策
            }
    except Exception as e:
        logger.error(f"[Tuning_Agent] Failed: {e}")
        return {
            "stage": STAGE_TUNING,
        }


# ============================================================================
# 定稿节点
# ============================================================================

@traceable(name="Finalize_Node")
@with_probe("finalize")
async def finalize_node(state: UnifiedState) -> dict:
    """定稿节点。

    输入：
    - current_draft: 当前草稿

    输出：
    - final_script: 最终脚本
    - stage: completed
    """
    current_draft = state.get("current_draft", "")

    logger.info("[Finalize_Node] Finalizing draft")
    logger.info(f"[Finalize_Node] Draft: {len(current_draft)} chars")

    # 定稿：将 current_draft 作为最终脚本
    return {
        "final_script": f"【最终文案】\n\n{current_draft}",
        "stage": STAGE_COMPLETED,
    }


# ============================================================================
# 路由函数
# ============================================================================

def route_after_human_review(state: UnifiedState) -> str:
    """大纲确认后的路由函数。

    根据用户决策和大纲版本决定下一步：
    - accept: 进入内容生成
    - modify: 进入大纲修改（最多3次）
    - finalize: 直接定稿（跳过内容生成）

    Returns:
        节点名称: "accept" / "modify" / "finalize"
    """
    human_decision = state.get("human_decision", "accept")
    outline_version = state.get("outline_version", 0)

    logger.info(f"[Route] Human decision: {human_decision}, outline_version: {outline_version}")

    # 用户选择修改大纲
    if human_decision.startswith("modify:") and outline_version < OUTLINE_MAX_REVISIONS:
        # 提取修改意见（去掉 "modify:" 前缀）
        return "modify"

    # 用户选择定稿（直接结束）
    if human_decision == "finalize":
        return "finalize"

    # 用户选择接受，或超过最大修改次数
    return "accept"


def route_after_tuning(state: UnifiedState) -> str:
    """微调后的路由函数。

    根据用户决策决定下一步：
    - finalize: 定稿
    - continue: 继续微调

    Returns:
        节点名称: "finalize" / "director"
    """
    human_decision = state.get("human_decision", "")

    logger.info(f"[Route] Tuning decision: {human_decision}")

    # 用户选择定稿
    if human_decision == "finalize" or human_decision == "定稿":
        return "finalize"

    # 其他情况继续微调（等待下一轮用户输入）
    # 但实际上我们需要暂停等待用户，所以返回到 tuning_agent
    # tuning_agent 是 interrupt_before 节点，会暂停
    return "finalize"  # 暂时默认定稿，后续可根据实际需求调整


# ============================================================================
# 导出
# ============================================================================

__all__ = [
    "deep_outline_generator_node",
    "human_review_node",
    "deep_outline_reviser_node",
    "deep_content_generator_node",
    "tuning_agent_node",
    "finalize_node",
    "route_after_human_review",
    "route_after_tuning",
]