# forge/deep_mode/tools/tone_adjuster.py

"""语气调整工具。"""

from langchain_core.tools import tool
import logging
import asyncio

from forge.tools.llm_client import LLMClient

logger = logging.getLogger(__name__)


@tool
def tone_adjuster(current_draft: str, target_tone: str) -> str:
    """调整整体语气风格。

    Args:
        current_draft: 当前草稿
        target_tone: 目标语气（幽默、专业、犀利、温和、活泼、严肃...）

    Returns:
        调整语气后的完整文章

    Example:
        用户: "整体语气太严肃了，改轻松点"
        输出: 调整后的全文
    """
    logger.info(f"[tone_adjuster] Adjusting tone to: {target_tone}")

    prompt = f"""请调整文章的整体语气风格：

## 当前文章
{current_draft}

## 目标语气
{target_tone}

## 调整规则
1. 保持原文的核心观点和结构
2. 调整措辞和表达方式以匹配目标语气
3. 如果是"幽默"，适当加入轻松的表达
4. 如果是"专业"，使用更严谨的术语
5. 如果是"活泼"，使用口语化表达
6. 直接输出调整后的完整文章

请直接输出完整文章：
"""

    llm = LLMClient()
    try:
        result = asyncio.run(llm.chat_with_retry(prompt))
        logger.info(f"[tone_adjuster] Tone adjustment completed: {len(result)} chars")
        return result
    except Exception as e:
        logger.error(f"[tone_adjuster] Adjustment failed: {e}")
        return f"语气调整失败：{e}"