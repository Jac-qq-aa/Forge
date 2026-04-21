# forge/deep_mode/tools/wikipedia_check.py

"""Wikipedia 事实核查工具。"""

from langchain_core.tools import tool
import logging

logger = logging.getLogger(__name__)


@tool
def wikipedia_check(term: str) -> str:
    """使用 Wikipedia API 核查专有名词/事实。

    Args:
        term: 需核查的术语或事实陈述

    Returns:
        Wikipedia 定义摘要，或"未找到相关条目"

    Example:
        用户: "查一下'360度评估'的定义"
        输出: Wikipedia 定义摘要
    """
    logger.info(f"[wikipedia_check] Checking term: {term}")

    try:
        import wikipedia

        # 设置中文 Wikipedia
        wikipedia.set_lang("zh")

        # 搜索条目
        results = wikipedia.search(term, results=3)

        if not results:
            # 尝试英文 Wikipedia
            wikipedia.set_lang("en")
            results = wikipedia.search(term, results=3)

        if not results:
            return "未找到相关 Wikipedia 条目"

        # 获取最相关条目的摘要
        try:
            page = wikipedia.page(results[0], auto_suggest=False)
            summary = page.summary[:500]  # 截取前 500 字
            return f"【Wikipedia 定义】\n条目：{page.title}\n摘要：{summary}"
        except wikipedia.exceptions.PageError:
            return "未找到相关 Wikipedia 条目"
        except wikipedia.exceptions.DisambiguationError as e:
            # 多义项，返回选项列表
            return f"存在多个相关条目：{', '.join(e.options[:5])}"

    except ImportError:
        logger.warning("[wikipedia_check] wikipedia library not installed")
        return "Wikipedia 库未安装，无法核查"
    except Exception as e:
        logger.error(f"[wikipedia_check] Check failed: {e}")
        return f"核查失败：{e}"