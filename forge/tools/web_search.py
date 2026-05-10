"""网页搜索工具 - Tavily API。

提供两种搜索模式：
1. web_search: 快速搜索，返回摘要结果
2. web_search_with_content: 深度搜索，包含完整网页内容

配置：
- TAVILY_API_KEY: Tavily API 密钥（已配置）
"""

import logging
import os
from typing import Optional

from langchain_core.tools import tool
from tavily import TavilyClient

logger = logging.getLogger(__name__)

# Tavily API Key
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "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")


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """网页搜索，返回结构化结果摘要。

    用于快速了解某个主题的相关信息，获取多个来源的摘要。

    Args:
        query: 搜索关键词或问题
        max_results: 最大结果数（默认5）

    Returns:
        搜索结果摘要（标题 + 内容 + URL）
    """
    logger.info(f"[WebSearch] Searching: {query}")

    try:
        client = TavilyClient(api_key=TAVILY_API_KEY)

        response = client.search(
            query=query,
            max_results=max_results,
            include_raw_content=False,  # 只返回摘要
        )

        results = []
        for r in response.get("results", []):
            title = r.get("title", "")
            content = r.get("content", "")
            url = r.get("url", "")
            results.append(f"【{title}】\n{content}\nURL: {url}")

        if not results:
            return "搜索无结果"

        output = "\n\n".join(results)
        logger.info(f"[WebSearch] Found {len(results)} results")
        return output

    except Exception as e:
        logger.error(f"[WebSearch] Failed: {e}")
        return f"搜索失败: {e}"


@tool
def web_search_with_content(query: str, max_results: int = 3) -> str:
    """深度搜索，包含完整网页内容。

    用于需要提取详细信息的场景，获取网页的完整文本内容。

    Args:
        query: 搜索关键词或问题
        max_results: 最大结果数（默认3，减少内容量）

    Returns:
        搜索结果（标题 + URL + 完整内容）
    """
    logger.info(f"[WebSearch] Deep searching: {query}")

    try:
        client = TavilyClient(api_key=TAVILY_API_KEY)

        response = client.search(
            query=query,
            max_results=max_results,
            include_raw_content=True,  # 包含完整内容
        )

        results = []
        for r in response.get("results", []):
            title = r.get("title", "")
            url = r.get("url", "")
            # 优先使用 raw_content，否则使用 content
            content = r.get("raw_content", r.get("content", ""))
            # 限制长度，避免过长
            content = content[:3000] if len(content) > 3000 else content
            results.append(f"【{title}】\nURL: {url}\n\n{content}")

        if not results:
            return "搜索无结果"

        output = "\n\n---\n\n".join(results)
        logger.info(f"[WebSearch] Deep found {len(results)} results")
        return output

    except Exception as e:
        logger.error(f"[WebSearch] Deep failed: {e}")
        return f"深度搜索失败: {e}"


@tool
def search_and_extract(query: str, extract_keywords: Optional[str] = None) -> str:
    """搜索并提取特定信息。

    先搜索，然后从结果中提取关键事实。

    Args:
        query: 搜索关键词
        extract_keywords: 要提取的信息类型（如"数据"、"案例"、"观点"）

    Returns:
        提取的事实清单
    """
    logger.info(f"[WebSearch] Search and extract: {query}, keywords: {extract_keywords}")

    # 先进行深度搜索获取完整内容
    search_result = web_search_with_content.invoke({"query": query, "max_results": 3})

    if "搜索无结果" in search_result or "搜索失败" in search_result:
        return search_result

    # 如果指定了提取关键词，提示用户关注特定信息
    if extract_keywords:
        return f"""搜索结果：
{search_result}

请从上述内容中提取与「{extract_keywords}」相关的关键事实。"""

    return search_result


# ============================================================================
# 导出
# ============================================================================

__all__ = [
    "web_search",
    "web_search_with_content",
    "search_and_extract",
    "TAVILY_API_KEY",
]