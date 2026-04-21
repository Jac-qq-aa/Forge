# forge/deep_mode/tools/outline_generator.py

"""大纲生成工具。"""

from langchain_core.tools import tool
import logging

logger = logging.getLogger(__name__)


@tool
def outline_generator(
    source_article: str,
    profile: str,
    rag_context: str
) -> str:
    """根据原文章、用户画像、知识库素材生成大纲。

    Args:
        source_article: 原知乎文章内容（标题 + 正文）
        profile: 用户画像 JSON 字符串
        rag_context: RAG 搜索结果

    Returns:
        大纲文本（带序号的结构化大纲）

    Example Output:
        一、开篇引入：职场新人的常见困境
            - 用一个真实场景切入
        二、核心观点：XX方法如何解决
            - 结合锐博集团实践案例
        三、实操建议
            - 三个可落地的技巧
        四、结尾
            - 引导读者思考
    """
    logger.info(f"[outline_generator] Generating outline...")

    # 构建大纲生成提示词
    prompt = f"""请根据以下信息生成文章大纲：

## 原文章内容
{source_article[:1500]}

## 用户画像
{profile}

## 知识库素材（可自然融入）
{rag_context if rag_context else "无"}

## 大纲生成要求
1. 保留原文核心观点和论证逻辑
2. 结构清晰，每个部分有明确的主题
3. 根据用户画像调整风格和侧重点
4. 如果有知识库素材，可在适当位置融入锐博集团案例
5. 大纲格式：一、二、三、四（带二级标题）
6. 篇幅控制在 {profile.get('length_preference', '中等')}

请直接输出大纲，格式示例：
一、开篇：[主题]
    - [要点]
二、核心观点：[主题]
    - [要点]
三、...
四、结尾：[主题]
"""

    return prompt


@tool
def outline_revision(
    current_outline: str,
    user_feedback: str,
    profile: str
) -> str:
    """根据用户反馈修改大纲。

    Args:
        current_outline: 当前大纲
        user_feedback: 用户修改意见
        profile: 用户画像

    Returns:
        修改后的大纲
    """
    logger.info(f"[outline_revision] Revising outline based on: {user_feedback[:50]}...")

    prompt = f"""请根据用户反馈修改大纲：

## 当前大纲
{current_outline}

## 用户反馈
{user_feedback}

## 用户画像
{profile}

## 修改要求
1. 针对用户反馈的具体问题进行修改
2. 保持大纲的整体结构和核心观点
3. 直接输出修改后的完整大纲

请直接输出修改后的大纲：
"""

    return prompt