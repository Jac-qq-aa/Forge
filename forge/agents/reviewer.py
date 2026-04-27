"""Reviewer node - LLM-based quality review."""

import logging
from langsmith import traceable
from forge.graph.state import GraphState
from forge.tools.llm_client import LLMClient
from forge.config import MAX_REVISIONS
from forge.evaluation.probe_decorator import with_probe

logger = logging.getLogger(__name__)


@traceable(name="Reviewer")
@with_probe("reviewer")
async def reviewer_node(state: GraphState) -> dict:
    """Review rewritten content for quality."""
    rewritten_draft = state.get("rewritten_draft", "")
    revision_count = state.get("revision_count", 0)

    logger.info(f"[Reviewer] Starting review, revision count: {revision_count}")

    llm = LLMClient()

    prompt = f"""请审核以下短视频脚本内容：

{rewritten_draft}

审核标准：
1. 原创度：内容是否原创，无明显抄袭痕迹
2. 内容质量：逻辑清晰，信息有价值
3. 吸引力：开头吸引人，整体有吸引力

请回复：
- 如果通过审核：回复"通过"并给出简要评价
- 如果不通过：回复具体改进建议（不超过100字）"""

    system_prompt = "你是一个严格但专业的内容审核专家，确保内容质量和原创性。"

    response = await llm.chat_with_retry(prompt, system_prompt)

    approved = "通过" in response
    force_approve = revision_count >= MAX_REVISIONS

    if approved or force_approve:
        if force_approve and not approved:
            logger.info(f"[Reviewer] Force approving after {MAX_REVISIONS} revisions")
        final_script = f"【最终脚本】\n\n{rewritten_draft}\n\n[审核评价：{response}]"
        logger.info("[Reviewer] Content APPROVED")
        logger.info("[Reviewer] Node completed")
        return {"final_script": final_script, "reflection_feedback": ""}
    else:
        logger.info(f"[Reviewer] Content REJECTED - feedback: {response[:100]}")
        logger.info("[Reviewer] Node completed")
        return {"reflection_feedback": response, "final_script": ""}