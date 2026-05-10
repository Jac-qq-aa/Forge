# forge/evolution/init_templates.py

"""默认Prompt模板初始化。

首次运行时检查并初始化默认模板到数据库。
"""

import logging
from typing import Dict, Any

from .prompt_manager import get_prompt_manager
from .storage import get_evolution_storage

logger = logging.getLogger(__name__)


# ============================================================================
# 默认模板定义
# ============================================================================

DEFAULT_DEEP_CONTENT_TEMPLATE: Dict[str, Any] = {
    "template_key": "deep_content_generator",
    "version": 1,
    "system_prompt": """你是一位资深互联网职场人，擅长将专业内容转化为通俗易懂的文章。

你的写作风格：
1. **口语化但专业**：用日常语言表达，但保持专业性
2. **结构清晰**：用小标题分段，逻辑流畅
3. **避免AI味**：不用"首先/其次/最后"，不用"值得一提的是"
4. **保留核心观点**：改写不等于删除原意
5. **自然融入素材**：知识库内容不超过10%

严禁：
- 编造数据或事实
- 使用过于正式的书面语
- 简单复制粘贴原文""",

    "user_prompt_template": """请根据大纲生成完整文章：

## 大纲
{outline}

## 原文章内容
标题：{title}
内容：{raw_content}

## 知识库素材
{rag_context}

## 高质量案例参考
{quality_context}

## 生成要求
1. **保留核心观点**：原文论点不能丢弃
2. **按大纲结构**：每个部分对应段落
3. **知识库融入**：自然引用，不超过10%
4. **严禁编造**：没有具体信息用模糊表述

直接输出文章（不要加任何标题或说明）：""",
    "is_active": True,
}

DEFAULT_OUTLINE_TEMPLATE: Dict[str, Any] = {
    "template_key": "outline_generator",
    "version": 1,
    "system_prompt": """你是一位文章结构规划专家，擅长将内容转化为清晰的大纲。

你的规划原则：
1. **保留核心观点**：大纲必须包含原文要点
2. **结构化呈现**：使用一、二、三、四格式
3. **二级标题细化**：每个一级标题下有2-3个二级标题
4. **根据需求调整**：用户需求优先""",

    "user_prompt_template": """请根据以下信息生成文章大纲：

## 原文章内容
标题：{title}
内容：{raw_content}

## 用户改写需求
{user_input}

## 知识库素材（可自然融入）
{rag_context}

## 大纲要求
1. 保留原文核心观点
2. 结构：一、二、三、四（带二级标题）
3. 根据用户需求调整风格和侧重点

直接输出大纲：""",
    "is_active": True,
}

# 所有默认模板
DEFAULT_TEMPLATES = {
    "deep_content_generator": DEFAULT_DEEP_CONTENT_TEMPLATE,
    "outline_generator": DEFAULT_OUTLINE_TEMPLATE,
}


# ============================================================================
# 初始化函数
# ============================================================================

async def init_default_templates() -> Dict[str, str]:
    """初始化所有默认模板到数据库。

    Returns:
        创建的模板ID字典 {template_key: template_id}
    """
    storage = get_evolution_storage()
    created_ids = {}

    for template_key, template_data in DEFAULT_TEMPLATES.items():
        # 检查是否已存在
        existing = await storage.get_active_template(template_key)

        if existing:
            logger.info(f"[InitTemplates] Template already exists: {template_key}")
            created_ids[template_key] = existing.get("id")
            continue

        # 创建新模板
        template_id = await storage.create_template(
            template_key=template_key,
            system_prompt=template_data["system_prompt"],
            user_prompt_template=template_data["user_prompt_template"],
            is_active=template_data.get("is_active", True),
        )

        if template_data.get("is_active"):
            await storage.activate_template(template_id)

        created_ids[template_key] = template_id
        logger.info(f"[InitTemplates] Template initialized: {template_key} -> {template_id}")

    return created_ids


async def ensure_default_templates() -> None:
    """确保默认模板存在（启动时调用）。

    如果数据库中没有激活模板，则初始化默认模板。
    """
    storage = get_evolution_storage()

    # 检查核心模板
    deep_template = await storage.get_active_template("deep_content_generator")

    if not deep_template:
        logger.info("[InitTemplates] No active templates found, initializing defaults...")
        await init_default_templates()
    else:
        logger.info(f"[InitTemplates] Active template found: deep_content_generator v{deep_template.get('version')}")