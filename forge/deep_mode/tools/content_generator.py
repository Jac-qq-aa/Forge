# forge/deep_mode/tools/content_generator.py

"""全文生成工具。"""

from langchain_core.tools import tool
import logging

logger = logging.getLogger(__name__)


@tool
def content_generator(
    outline: str,
    source_article: str,
    profile: str,
    rag_context: str
) -> str:
    """根据大纲生成完整文章。

    Args:
        outline: 已确认的大纲
        source_article: 原知乎文章内容（保留核心观点）
        profile: 用户画像 JSON 字符串（决定语气风格）
        rag_context: RAG 知识库素材

    Returns:
        完整文章文本

    Note:
        必须保留原文核心观点，RAG 素材自然融入，不生硬堆砌
    """
    logger.info(f"[content_generator] Generating content based on outline...")

    # 构建全文生成提示词
    prompt = f"""请根据大纲生成完整文章：

## 已确认的大纲
{outline}

## 原文章内容（核心观点来源）
{source_article[:2000]}

## 用户画像
{profile}

## 知识库素材（自然融入，不超过10%篇幅）
{rag_context if rag_context else "无"}

## 生成要求
1. **核心观点必须保留**：原文的主要论点和论证逻辑是主体，不能丢弃
2. **风格匹配画像**：语气、受众、侧重点按 profile 执行
3. **大纲为骨架**：每个大纲部分对应文章段落
4. **知识库素材自然融入**：用"据锐博集团资料显示..."等表述，不生硬
5. **篇幅控制**：按 profile 中的 length_preference
6. **信息真实性**：严禁编造具体信息（课程名、时间、数字等）
   - 原文有的事实可以保留
   - 知识库信息引用时标注来源
   - 没有具体信息时用模糊表述

请直接输出完整文章：
"""

    return prompt