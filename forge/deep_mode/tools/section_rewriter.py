# forge/deep_mode/tools/section_rewriter.py

"""局部重写工具。"""

from langchain_core.tools import tool
import logging
import asyncio

from forge.tools.llm_client import LLMClient

logger = logging.getLogger(__name__)


@tool
def section_rewriter(current_draft: str, section_identifier: str, user_request: str) -> str:
    """根据用户要求重写指定段落。

    Args:
        current_draft: 当前完整草稿
        section_identifier: 段落标识（如"第二段"、"大纲第三节"、"开头部分"）
        user_request: 用户修改要求

    Returns:
        重写后的完整草稿

    Example:
        用户: "把第二段改得更通俗一点"
        输出: 更新后的全文
    """
    logger.info(f"[section_rewriter] Rewriting section: {section_identifier}")

    # 构建重写提示词
    prompt = f"""请根据用户要求重写文章的指定部分：

## 当前全文
{current_draft}

## 要修改的部分
{section_identifier}

## 用户修改要求
{user_request}

## 重写规则
1. 只修改指定部分，其他内容保持不变
2. 确保修改后的内容与全文风格一致
3. 保持原文核心观点
4. 直接输出修改后的完整文章（包含未修改的部分）

请直接输出完整文章：
"""

    # 调用 LLM（同步包装）
    llm = LLMClient()
    try:
        result = asyncio.run(llm.chat_with_retry(prompt))
        logger.info(f"[section_rewriter] Rewrite completed: {len(result)} chars")
        return result
    except Exception as e:
        logger.error(f"[section_rewriter] Rewrite failed: {e}")
        return f"重写失败：{e}"