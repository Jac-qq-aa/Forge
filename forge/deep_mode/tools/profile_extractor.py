"""用户画像提取工具。"""

from langchain_core.tools import tool
import json
import logging

logger = logging.getLogger(__name__)


@tool
def profile_extractor(user_input: str, article_context: str) -> str:
    """从用户自然语言输入中提取结构化画像。

    Args:
        user_input: 用户的需求描述（自由文本）
        article_context: 原文章标题和摘要（用于理解上下文）

    Returns:
        JSON 格式的 ProfileInfo 字符串

    Example:
        用户输入: "改成知乎回答风格，语气专业点，给HR从业者看，重点讲实操案例"
        输出: {"tone": "专业", "target_audience": "HR从业者", "focus_point": "实操案例", "length_preference": "中等"}
    """
    logger.info(f"[profile_extractor] Extracting profile from: {user_input[:50]}...")

    # 构建提示词
    prompt = f"""请从用户的改写需求中提取以下维度的信息，以 JSON 格式返回：

文章上下文：
{article_context[:200]}

用户需求：
{user_input}

需要提取的维度：
1. tone（语气风格）：幽默、专业、轻松、犀利、温和、活泼
2. target_audience（目标读者）：职场新人、HR从业者、管理者、大众读者、专业人士
3. focus_point（侧重点）：实用工具、理论分析、案例故事、行业洞察、情感共鸣
4. length_preference（篇幅偏好）：简洁(500字)、中等(800字)、深度(1500字+)
5. target_platform（目标平台）：zhihu_article、xhs_video、wechat_article

规则：
- 如果用户未明确提及某维度，根据文章上下文推断合理默认值
- 只返回 JSON，不要其他解释
- 确保 JSON 格式正确

示例输出：
{"tone": "专业", "target_audience": "HR从业者", "focus_point": "实操案例", "length_preference": "中等", "target_platform": "zhihu_article"}
"""

    # 这里需要在 Agent 中调用 LLM，工具本身返回提示词结构
    # 实际提取逻辑由 Agent 调用 LLM 完成
    # 工具返回结构化的请求格式

    return json.dumps({
        "prompt": prompt,
        "user_input": user_input,
        "article_context": article_context,
        "requires_llm": True,
    })