"""Editor node - LLM-based content rewriting."""

import logging
from forge.graph.state import GraphState
from forge.tools.llm_client import LLMClient

logger = logging.getLogger(__name__)


async def editor_node(state: GraphState) -> dict:
    """Rewrite content using Qwen LLM."""
    raw_content = state.get("raw_content", {})
    reflection_feedback = state.get("reflection_feedback", "")
    revision_count = state.get("revision_count", 0)

    logger.info(f"[Editor] Starting rewrite, revision count: {revision_count}")

    llm = LLMClient()

    original_text = raw_content.get("text", "")
    title = raw_content.get("title", "")

    if reflection_feedback:
        prompt = f"""请根据以下反馈意见优化内容：

反馈意见：
{reflection_feedback}

原始内容：
{original_text}

请改进内容，解决反馈中指出的问题。"""
        system_prompt = "你是一个专业的内容编辑，擅长根据反馈改进文章。"
    else:
        prompt = f"""请原创重写以下内容，保持吸引力和实用性，但要确保原创性：

标题：{title}
内容：{original_text}

要求：
1. 保持核心信息价值
2. 使用新的表达方式
3. 增加吸引人的开头
4. 控制篇幅在300-500字"""
        system_prompt = "你是一个短视频脚本创作专家，擅长创作原创、吸引人的内容。"

    rewritten_draft = await llm.chat_with_retry(prompt, system_prompt)

    new_revision_count = revision_count + 1
    logger.info(f"[Editor] Generated draft ({len(rewritten_draft)} chars)")
    logger.info(f"[Editor] New revision count: {new_revision_count}")
    logger.info("[Editor] Node completed")

    return {
        "rewritten_draft": rewritten_draft,
        "revision_count": new_revision_count,
    }