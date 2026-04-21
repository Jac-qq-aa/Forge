# forge/deep_mode/tools/rag_search.py

"""知识库搜索工具。"""

from langchain_core.tools import tool
import logging

from forge.knowledge import get_knowledge_base

logger = logging.getLogger(__name__)


@tool
def rag_search(query: str, max_docs: int = 3) -> str:
    """搜索锐博集团知识库，获取相关参考资料。

    Args:
        query: 搜索关键词（如文章标题、核心概念）
        max_docs: 返回文档数量，默认 3

    Returns:
        知识库相关内容摘要，用于注入到生成 prompt

    Note:
        如果知识库连接失败或无结果，返回空字符串（Agent 会继续生成，只是没有知识库素材）
    """
    logger.info(f"[rag_search] Searching: {query[:50]}...")

    try:
        kb = get_knowledge_base()
        context = kb.get_context_for_topic(query, max_docs=max_docs)

        if context:
            logger.info(f"[rag_search] Found context: {len(context)} chars")
            return context
        else:
            logger.info("[rag_search] No relevant documents found")
            return ""

    except Exception as e:
        logger.warning(f"[rag_search] Search failed: {e}")
        # Fallback：返回空字符串，Agent 继续生成
        return ""