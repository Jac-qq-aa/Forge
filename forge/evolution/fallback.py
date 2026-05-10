# forge/evolution/fallback.py

"""自进化系统降级策略。

当模板管理器或知识库不可用时，提供硬编码降级方案。
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# 硬编码降级模板（当数据库不可用时使用）
# ============================================================================

FALLBACK_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "deep_content_generator": {
        "id": "fallback_deep_content",
        "template_key": "deep_content_generator",
        "version": 0,  # 0 表示降级版本
        "system_prompt": """你是一位资深互联网职场人，擅长将专业内容转化为通俗易懂的文章。

你的写作风格：
1. **口语化但专业**：用日常语言表达，但保持专业性
2. **结构清晰**：用小标题分段，逻辑流畅
3. **避免AI味**：不用"首先/其次/最后"，不用"值得一提的是"

严禁：
- 编造数据或事实
- 使用过于正式的书面语""",
        "user_prompt_template": """请根据大纲生成完整文章：

## 大纲
{outline}

## 原文章内容
标题：{title}
内容：{raw_content}

## 知识库素材
{rag_context}

## 生成要求
1. **保留核心观点**：原文论点不能丢弃
2. **按大纲结构**：每个部分对应段落
3. **严禁编造**：没有具体信息用模糊表述

直接输出文章：""",
    },

    "outline_generator": {
        "id": "fallback_outline",
        "template_key": "outline_generator",
        "version": 0,
        "system_prompt": """你是一位文章结构规划专家，擅长将内容转化为清晰的大纲。""",
        "user_prompt_template": """请根据以下信息生成文章大纲：

## 原文章内容
标题：{title}
内容：{raw_content}

## 用户改写需求
{user_input}

## 大纲要求
1. 保留原文核心观点
2. 结构：一、二、三、四（带二级标题）

直接输出大纲：""",
    },
}


def get_fallback_template(template_key: str) -> Optional[Dict[str, Any]]:
    """获取降级模板。

    Args:
        template_key: 模板标识

    Returns:
        硬编码模板字典，如果不存在返回 None
    """
    template = FALLBACK_TEMPLATES.get(template_key)

    if template:
        logger.info(f"[Fallback] Using fallback template for: {template_key}")
        return template

    logger.warning(f"[Fallback] No fallback template for: {template_key}")
    return None


def skip_quality_context() -> str:
    """知识库不可用时跳过高质量案例参考。

    Returns:
        默认占位文本
    """
    logger.info("[Fallback] Skipping quality context (knowledge base unavailable)")
    return "无参考案例"


def is_fallback_template(template: Dict[str, Any]) -> bool:
    """判断是否是降级模板。

    Args:
        template: 模板字典

    Returns:
        True 如果是降级模板（version=0 或 id 包含 "fallback")
    """
    if template is None:
        return True

    return (
        template.get("version") == 0
        or "fallback" in str(template.get("id", ""))
    )