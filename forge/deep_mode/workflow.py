# forge/deep_mode/workflow.py

"""LangGraph Workflow for Deep Mode - Plan-Execute + ReAct Architecture.

基于 LangGraph create_agent + AgentState 实现：
1. Plan-Execute 流程（大纲生成 → 内容生成）
2. ReAct 微调 Agent（对话微调）

参考：deer-flow create_agent + state_schema 模式
"""

import logging
import asyncio
from typing import Annotated, NotRequired, TypedDict, Literal, List, Dict, Any, Optional
from datetime import datetime
import operator

from langchain.agents import AgentState, create_agent
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_core.tools import tool

from forge.tools.llm_client import LLMClient, SyncLLMClient
from forge.deep_mode.session_state import DeepModeSession
from forge.deep_mode.paragraph_generator import (
    get_paragraph_generator,
    generate_article_with_paragraphs,
    estimate_target_length,
    parse_paragraph_outline,
    ParagraphTask,
)
from forge.deep_mode.paragraph_cache import get_paragraph_cache
from forge.knowledge import get_knowledge_base
from forge.config import AGENT_EXECUTION_TIMEOUT

logger = logging.getLogger(__name__)


# ============================================================================
# State Definition - Extend AgentState (deer-flow pattern)
# ============================================================================

class DeepModeState(AgentState):
    """深度生成模式状态 - 扩展 AgentState。

    继承 AgentState 的 messages 字段（使用 add_messages reducer），
    添加深度生成所需的额外状态字段。

    参考：deer-flow ThreadState 扩展 AgentState
    """

    # 会话信息
    session_id: NotRequired[str | None]
    article_id: NotRequired[str | None]

    # 原文章信息
    source_article: NotRequired[Dict[str, str] | None]  # {title, text, url}

    # 用户输入
    user_input: NotRequired[str | None]

    # 流程状态
    stage: NotRequired[
        Literal[
            "planning",        # 正在生成大纲
            "waiting_outline", # 等待用户确认大纲
            "executing",       # 正在生成全文
            "tuning",          # 微调对话
            "completed",       # 已定稿
        ]
    ]

    # 大纲和版本
    outline: NotRequired[str | None]
    outline_version: NotRequired[int | None]

    # RAG 知识库素材
    rag_context: NotRequired[str | None]

    # 当前草稿（工具可访问）
    current_draft: NotRequired[str | None]

    # 错误信息
    error: NotRequired[str | None]


# ============================================================================
# Plan-Execute Nodes (不使用 StateGraph，直接调用 LLM)
# ============================================================================

async def rag_search(source_article: Dict[str, str]) -> str:
    """搜索知识库获取素材。"""
    logger.info("[Workflow] Running RAG search...")

    title = source_article.get("title", "")
    text = source_article.get("text", "")[:200]
    query = f"{title} {text}"

    try:
        kb = get_knowledge_base()
        context = await asyncio.wait_for(
            asyncio.to_thread(kb.get_context_for_topic, query, 3),
            timeout=10.0
        )
        return context or ""
    except asyncio.TimeoutError:
        logger.warning("[Workflow] RAG search timeout (10s)")
        return ""
    except Exception as e:
        logger.warning(f"[Workflow] RAG search failed: {e}")
        return ""


async def generate_outline(
    source_article: Dict[str, str],
    user_input: str,
    rag_context: str = "",
    target_platform: str = "zhihu",
    use_paragraph_outline: bool = True,
) -> str:
    """生成大纲（默认使用段落级大纲）。

    Args:
        source_article: 原文章数据
        user_input: 用户改写需求
        rag_context: RAG知识库素材
        target_platform: 目标平台
        use_paragraph_outline: 是否使用段落级大纲（默认True）

    Returns:
        大纲文本
    """
    logger.info("[Workflow] Generating outline (paragraph-level)...")

    # 使用分段生成器的段落大纲
    generator = get_paragraph_generator()

    try:
        paragraphs = await generator.generate_paragraph_outline(
            source_article=source_article,
            user_input=user_input,
            rag_context=rag_context,
            target_platform=target_platform,
        )

        # 转换为文本大纲格式
        outline_text = "\n".join([
            f"段落{p.id} | 主点：{p.main_point} | 字数：{p.word_count} | 素材：{p.rag_reference}"
            for p in paragraphs
        ])

        logger.info(f"[Workflow] Paragraph outline generated: {len(paragraphs)} paragraphs, ~{sum(p.word_count for p in paragraphs)} words")
        return outline_text

    except Exception as e:
        logger.error(f"[Workflow] Paragraph outline generation failed: {e}")
        # 降级到传统大纲
        logger.info("[Workflow] Falling back to traditional outline...")
        return await _generate_outline_fallback(source_article, user_input, rag_context)


async def _generate_outline_fallback(
    source_article: Dict[str, str],
    user_input: str,
    rag_context: str = ""
) -> str:
    """降级：生成传统大纲格式。"""
    prompt = f"""请根据以下信息生成文章大纲：

## 原文章内容
标题：{source_article.get('title', '')}
内容：{source_article.get('text', '')[:1500]}

## 用户改写需求
{user_input}

## 知识库素材（可自然融入）
{rag_context if rag_context else "无"}

## 大纲要求
1. 保留原文核心观点
2. 结构：一、二、三、四（带二级标题）
3. 根据用户需求调整风格和侧重点

直接输出大纲：
"""

    try:
        llm = LLMClient()
        outline = await llm.chat_with_retry(prompt)
        logger.info(f"[Workflow] Fallback outline generated: {len(outline)} chars")
        return outline
    except Exception as e:
        logger.error(f"[Workflow] Fallback outline generation failed: {e}")
        raise


async def revise_outline(current_outline: str, user_feedback: str) -> str:
    """修改大纲。"""
    logger.info(f"[Workflow] Revising outline: {user_feedback[:50]}...")

    prompt = f"""请根据用户反馈修改大纲：

## 当前大纲
{current_outline}

## 用户反馈
{user_feedback}

## 修改要求

### 删除操作（最重要）
当用户要求"删除第X点"时：
- **彻底删除**该点的标题和所有内容
- **不要**将内容移动到其他位置
- **不要**重新排列剩余内容（保持原有编号）
- 示例：删除第三点后，大纲变成：一、二、四、五...（不重新编号为一、二、三、四）

### 其他操作
1. 增加：在指定位置插入新内容
2. 修改：只改指定内容，其他不变
3. 调整顺序：按用户要求移动位置

### 输出格式
直接输出修改后的完整大纲，不要添加任何解释。

直接输出修改后的大纲：
"""

    try:
        llm = LLMClient()
        revised = await llm.chat_with_retry(prompt)
        logger.info(f"[Workflow] Outline revised: {len(revised)} chars")
        return revised
    except Exception as e:
        logger.error(f"[Workflow] Outline revision failed: {e}")
        raise


async def generate_content(
    outline: str,
    source_article: Dict[str, str],
    rag_context: str = "",
    return_template_id: bool = False,
    user_input: str = "",
    target_platform: str = "zhihu",
    use_paragraph_generation: bool = True,
    session_id: str = "",
) -> str | tuple:
    """根据大纲生成全文（默认使用分段生成）。

    Args:
        outline: 大纲内容（可能是段落级大纲或传统大纲）
        source_article: 原文章数据 {title, text, url, platform}
        rag_context: RAG 知识库素材
        return_template_id: 是否返回模板ID（用于效果统计）
        user_input: 用户改写需求
        target_platform: 目标平台
        use_paragraph_generation: 是否使用分段生成（默认True）
        session_id: 会话ID（用于缓存）

    Returns:
        生成的草稿内容，如果 return_template_id=True 则返回 (draft, template_id)
    """
    logger.info("[Workflow] Generating content...")

    # 判断大纲类型：段落级大纲 vs 传统大纲
    is_paragraph_outline = "|" in outline and "段落" in outline or "主点" in outline

    if use_paragraph_generation and is_paragraph_outline:
        # 使用分段生成（段落级大纲）
        logger.info("[Workflow] Using paragraph-by-paragraph generation...")
        return await _generate_content_by_paragraphs(
            outline=outline,
            source_article=source_article,
            user_input=user_input,
            rag_context=rag_context,
            return_template_id=return_template_id,
            session_id=session_id,
        )
    else:
        # 使用一次性生成（传统大纲或短文）
        logger.info("[Workflow] Using one-shot generation...")
        return await _generate_content_once(
            outline=outline,
            source_article=source_article,
            rag_context=rag_context,
            return_template_id=return_template_id,
        )


async def _generate_content_by_paragraphs(
    outline: str,
    source_article: Dict[str, str],
    user_input: str,
    rag_context: str,
    return_template_id: bool,
    session_id: str,
) -> str | tuple:
    """分段生成全文（段落级大纲）。"""
    generator = get_paragraph_generator()
    cache = get_paragraph_cache()

    # 解析段落大纲
    paragraphs = parse_paragraph_outline(outline)

    if not paragraphs:
        logger.warning("[Workflow] Failed to parse paragraph outline, using one-shot")
        return await _generate_content_once(outline, source_article, rag_context, return_template_id)

    logger.info(f"[Workflow] Paragraph-by-paragraph: {len(paragraphs)} paragraphs")

    # 逐段生成
    full_text = ""
    total_words = sum(p.word_count for p in paragraphs)

    for i, paragraph in enumerate(paragraphs):
        paragraph_id = str(paragraph.id)

        # 检查缓存
        cached_content = cache.get(session_id, paragraph_id)
        if cached_content:
            logger.info(f"[Workflow] Using cached content for paragraph {paragraph_id}")
            full_text += cached_content + "\n\n"
            continue

        # 生成单个段落
        logger.info(f"[Workflow] Generating paragraph {paragraph_id}/{len(paragraphs)}...")

        content = await generator.generate_paragraph(
            paragraph=paragraph,
            all_paragraphs=paragraphs,
            written_text=full_text,
            source_article=source_article,
            user_input=user_input,
        )

        # 缓存结果
        if session_id:
            cache.set(session_id, paragraph_id, content)

        full_text += content + "\n\n"

        # 每3段输出进度
        if (i + 1) % 3 == 0:
            logger.info(f"[Workflow] Progress: {i + 1}/{len(paragraphs)} paragraphs, ~{len(full_text)} chars")

    draft = full_text.strip()
    logger.info(f"[Workflow] Paragraph generation complete: {len(draft)} chars, {len(paragraphs)} paragraphs")

    if return_template_id:
        return draft, "paragraph_generator"
    return draft


async def _generate_content_once(
    outline: str,
    source_article: Dict[str, str],
    rag_context: str,
    return_template_id: bool,
) -> str | tuple:
    """一次性生成全文（传统方式，用于短文或降级场景）。"""
    logger.info("[Workflow] One-shot content generation...")

    # 初始化变量（降级时的默认值）
    template = None
    quality_context = "无参考案例"
    prompt_manager = None

    # 获取动态模板和高质量案例参考
    try:
        from forge.evolution import get_prompt_manager, get_quality_knowledge_manager, ensure_default_templates

        await ensure_default_templates()

        prompt_manager = get_prompt_manager()
        template = await prompt_manager.get_active_template("deep_content_generator")

        quality_kb = get_quality_knowledge_manager()
        platform = source_article.get("platform", "zhihu")
        quality_context = await quality_kb.get_context_for_generation(outline, platform)

    except Exception as e:
        logger.warning(f"[Workflow] Evolution system unavailable, using fallback: {e}")
        from forge.evolution import get_fallback_template, skip_quality_context

        template = get_fallback_template("deep_content_generator")
        quality_context = skip_quality_context()

    if template is None:
        from forge.evolution import get_fallback_template
        template = get_fallback_template("deep_content_generator")

    system_prompt = template.get("system_prompt", "")
    user_prompt_template = template.get("user_prompt_template", "")

    variables = {
        "outline": outline,
        "title": source_article.get("title", ""),
        "raw_content": source_article.get("text", "")[:2000],
        "rag_context": rag_context or "无",
        "quality_context": quality_context,
    }

    if prompt_manager:
        user_prompt = prompt_manager.safe_format_template(user_prompt_template, variables)
    else:
        safe_vars = {k: v or "无" for k, v in variables.items()}
        user_prompt = user_prompt_template.format(**safe_vars)

    try:
        llm = LLMClient()
        draft = await llm.chat_with_retry(user_prompt, system_prompt)
        logger.info(f"[Workflow] One-shot content generated: {len(draft)} chars, template={template.get('id')}")

        if return_template_id:
            return draft, template.get("id")
        return draft

    except Exception as e:
        logger.error(f"[Workflow] One-shot content generation failed: {e}")
        raise


# ============================================================================
# ReAct Tuning Agent Tools
# ============================================================================

@tool
def rewrite_section(section_identifier: str, user_request: str) -> str:
    """重写文章指定段落。

    Args:
        section_identifier: 段落标识（如"第二段"、"开头部分"）
        user_request: 用户修改要求

    Returns:
        重写后的完整草稿（通过 injected_state 获取当前草稿）
    """
    logger.info(f"[TuningTool] rewrite_section: {section_identifier}")
    # 注意：工具不直接传入 current_draft，由 Agent 通过 state 注入
    # 实际调用时 Agent 会将 current_draft 作为上下文传递给 LLM
    return "rewrite_section_called"


@tool
def adjust_tone(target_tone: str) -> str:
    """调整文章整体语气。

    Args:
        target_tone: 目标语气（如"轻松"、"专业"、"幽默")

    Returns:
        调整后的完整草稿
    """
    logger.info(f"[TuningTool] adjust_tone: {target_tone}")
    return "adjust_tone_called"


@tool
def check_fact(term: str) -> str:
    """核查专有名词定义。

    Args:
        term: 要核查的术语

    Returns:
        术语定义或核查结果
    """
    logger.info(f"[TuningTool] check_fact: {term}")

    prompt = f"""请解释以下术语的定义和背景：

术语：{term}

要求：
1. 给出准确的定义
2. 说明常见用法或背景
3. 如果不确定，明确说明"建议查阅专业资料"

请回答：
"""

    try:
        llm = SyncLLMClient()
        return llm.chat_with_retry(prompt)
    except Exception as e:
        return f"核查失败：{e}"


@tool
def search_knowledge(query: str) -> str:
    """搜索知识库补充素材。

    Args:
        query: 搜索关键词

    Returns:
        相关知识库内容
    """
    logger.info(f"[TuningTool] search_knowledge: {query}")

    try:
        kb = get_knowledge_base()
        context = kb.get_context_for_topic(query, 2)
        return context or "未找到相关内容"
    except Exception as e:
        return f"搜索失败：{e}"


# ============================================================================
# Tuning Agent Tools - 增强版（段落级操作）
# ============================================================================

@tool
def expand_paragraph(paragraph_id: str, target_words: str) -> str:
    """扩展指定段落的字数。

    Args:
        paragraph_id: 段落编号（如"1"、"2"、"第三段"）
        target_words: 目标字数（如"800"、"增加200字"）

    Returns:
        扩展后的完整文章（Agent会注入当前草稿）
    """
    logger.info(f"[TuningTool] expand_paragraph: {paragraph_id} → {target_words}")
    return f"expand_paragraph_called: {paragraph_id}, {target_words}"


@tool
def shrink_paragraph(paragraph_id: str, target_words: str) -> str:
    """缩减指定段落的字数。

    Args:
        paragraph_id: 段落编号（如"1"、"2"、"第三段"）
        target_words: 目标字数（如"300"、"缩减到500字"）

    Returns:
        缩减后的完整文章
    """
    logger.info(f"[TuningTool] shrink_paragraph: {paragraph_id} → {target_words}")
    return f"shrink_paragraph_called: {paragraph_id}, {target_words}"


@tool
def merge_paragraphs(start_id: str, end_id: str) -> str:
    """合并相邻段落。

    Args:
        start_id: 起始段落编号
        end_id: 结束段落编号

    Returns:
        合并后的完整文章
    """
    logger.info(f"[TuningTool] merge_paragraphs: {start_id}-{end_id}")
    return f"merge_paragraphs_called: {start_id}, {end_id}"


@tool
def split_paragraph(paragraph_id: str) -> str:
    """拆分过长的段落。

    Args:
        paragraph_id: 要拆分的段落编号

    Returns:
        拆分后的完整文章
    """
    logger.info(f"[TuningTool] split_paragraph: {paragraph_id}")
    return f"split_paragraph_called: {paragraph_id}"


@tool
def check_coherence() -> str:
    """检查全文段落连贯性。

    Returns:
        连贯性分析结果，指出需要改进的过渡点
    """
    logger.info(f"[TuningTool] check_coherence")
    return "check_coherence_called"


@tool
def get_word_count() -> str:
    """统计文章各段落字数。

    Returns:
        各段落字数统计（Agent会注入当前草稿并计算）
    """
    logger.info(f"[TuningTool] get_word_count")
    return "get_word_count_called"


# ============================================================================
# Tuning Agent System Prompt
# ============================================================================

TUNING_SYSTEM_PROMPT = """你是一个文章微调专家，擅长段落级精细化调整。

## ⚠️ 最重要规则：回复格式

你必须严格区分两种情况：

**情况1 - 用户要求修改文章**
回复格式：直接返回完整的修改后文章（不加任何前缀或说明）

示例：
用户："把第二段改得通俗点"
你的回复：
[完整的文章内容，第二段已修改，其他段落保持不变]

**情况2 - 用户只是提问或评论（不要求修改）**
回复格式：【回答】+ 你的回答内容

示例：
用户："这篇文章主要讲什么？"
你的回复：【回答】这篇文章主要讲述了...

用户："写得不错"
你的回复：【回答】谢谢您的认可！

用户："这个术语是什么意思？"
你的回复：【回答】这个术语指的是...

## 如何判断用户意图？

**需要修改文章（直接返回全文）：**
- "把...改..."
- "修改..."
- "删除..."
- "增加..."
- "精简..."
- "调整语气..."
- "重写..."
- "扩展第X段"
- "缩减第X段"
- "合并段落"
- "拆分段落"

**只是提问或评论（用【回答】开头）：**
- "这篇文章讲什么？"
- "...是什么意思？"
- "...准确吗？"
- "写得不错"
- "有点长"
- "我觉得..."
- "字数统计"
- "检查连贯性"
- 任何不带修改意图的问题或评论

## 段落级操作指南

当用户提到段落操作时，按以下规则处理：

### 扩展段落
用户："第二段太短了，扩展到800字"
操作：
1. 识别段落位置（第二段）
2. 保持原段落核心观点
3. 扩展内容：增加案例、细节、论述
4. 字数控制在目标值±10%
5. 返回完整文章

### 缩减段落
用户："第三段太长了，缩减到500字"
操作：
1. 识别段落位置
2. 保留核心观点和关键论据
3. 删除冗余表述、重复内容
4. 字数控制在目标值±10%
5. 返回完整文章

### 合并段落
用户："把第三段和第四段合并"
操作：
1. 合并相邻两段内容
2. 添加过渡句保持连贯
3. 删除重复表述
4. 返回完整文章（段落数减少）

### 拆分段落
用户："第二段太长了，拆分成两段"
操作：
1. 找到段落中的逻辑分割点
2. 拆分为两段
3. 每段有独立主题句
4. 添加过渡保持连贯
5. 返回完整文章（段落数增加）

### 字数统计（提问）
用户："帮我统计各段落字数"
回复：【回答】各段落字数统计：
段落1: xxx字
段落2: xxx字
...
全文总计: xxx字

### 连贯性检查（提问）
用户："检查全文段落连贯性"
回复：【回答】连贯性分析：
- 段落1→2过渡: 良好/需改进
- 段落2→3过渡: 良好/需改进
...
建议改进点：[具体说明]

## 修改规则

1. 修改请求：返回完整文章，保持结构，只改指定部分
2. 提问请求：用【回答】开头，不修改文章
3. 段落操作：精准定位，字数控制±10%

## 注意

- 模糊情况默认按提问处理（用【回答】开头）
- 修改请求必须返回完整文章，不能只返回片段
- 段落编号：用户说"第一段"指文章开头段落，"第二段"指第二个段落
"""


# ============================================================================
# Create Tuning Agent (deer-flow pattern)
# ============================================================================

def create_tuning_agent():
    """创建 ReAct 微调 Agent。

    使用 langchain.agents.create_agent + state_schema。
    参考 deer-flow create_deerflow_agent 模式。

    工具列表：
    - 基础工具：rewrite_section, adjust_tone, check_fact, search_knowledge
    - 增强工具：expand_paragraph, shrink_paragraph, merge_paragraphs, split_paragraph
    - 分析工具：check_coherence, get_word_count
    """
    from langchain_openai import ChatOpenAI
    from forge.config import QWEN_API_URL, QWEN_API_KEY, QWEN_MODEL

    llm = ChatOpenAI(
        base_url=QWEN_API_URL,
        api_key=QWEN_API_KEY,
        model=QWEN_MODEL,
    )

    tools = [
        # 基础工具
        rewrite_section,
        adjust_tone,
        check_fact,
        search_knowledge,
        # 增强工具 - 段落级操作
        expand_paragraph,
        shrink_paragraph,
        merge_paragraphs,
        split_paragraph,
        # 分析工具
        check_coherence,
        get_word_count,
    ]

    try:
        agent = create_agent(
            model=llm,
            tools=tools,
            system_prompt=TUNING_SYSTEM_PROMPT,
            state_schema=DeepModeState,  # 关键：传入自定义 state_schema
        )
        logger.info("[TuningAgent] Agent created with DeepModeState + enhanced paragraph tools")
        return agent
    except Exception as e:
        logger.warning(f"[TuningAgent] create_agent failed: {e}, using fallback")
        return None


# ============================================================================
# Global instances
# ============================================================================

_tuning_agent = None


def get_tuning_agent():
    """获取 Tuning Agent 实例。"""
    global _tuning_agent
    if _tuning_agent is None:
        _tuning_agent = create_tuning_agent()
    return _tuning_agent


# ============================================================================
# Run Tuning Agent
# ============================================================================

async def run_tuning_agent(
    current_draft: str,
    user_message: str,
) -> str:
    """运行 Tuning Agent 处理用户微调请求。

    Args:
        current_draft: 当前草稿
        user_message: 用户修改请求

    Returns:
        Agent 响应（修改后的草稿或核查结果）
    """
    agent = get_tuning_agent()

    if agent is None:
        # Fallback: 直接调用 LLM
        return await _fallback_tuning(current_draft, user_message)

    # 构建包含当前草稿的消息
    full_message = f"""## 当前文章
{current_draft}

## 用户请求
{user_message}

## 回复格式要求（必须遵守）

1. 如果用户要求修改文章：
   - 直接返回完整的修改后文章
   - 不加任何前缀或说明
   - 保持结构，只改指定部分

2. 如果用户只是提问或评论（不要求修改）：
   - 以【回答】开头
   - 焊接回答内容，不返回文章

判断标准：
- 包含"改"、"修改"、"删除"、"增加"等词 → 修改请求
- 只是提问、评论、确认 → 提问请求

请处理上述请求："""

    try:
        result = await agent.ainvoke({
            "messages": [HumanMessage(content=full_message)],
            "current_draft": current_draft,  # state_schema 支持此字段
        })

        # 提取最终响应
        if "messages" in result:
            last_msg = result["messages"][-1]
            if isinstance(last_msg, AIMessage):
                response = last_msg.content
                logger.info(f"[TuningAgent] Response: {response[:100]}...")
                return response

        return current_draft
    except Exception as e:
        logger.error(f"[TuningAgent] Failed: {e}")
        return await _fallback_tuning(current_draft, user_message)


async def _fallback_tuning(current_draft: str, user_message: str) -> str:
    """Fallback: 直接调用 LLM 处理微调。"""
    logger.info("[TuningFallback] Using direct LLM call")

    full_message = f"""## 当前文章
{current_draft}

## 用户请求
{user_message}

## 回复格式要求（必须遵守）

1. 如果用户要求修改文章：
   - 直接返回完整的修改后文章
   - 不加任何前缀或说明
   - 保持结构，只改指定部分

2. 如果用户只是提问或评论（不要求修改）：
   - 以【回答】开头
   - 焊接回答内容，不返回文章

判断标准：
- 包含"改"、"修改"、"删除"、"增加"等词 → 修改请求
- 只是提问、评论、确认 → 提问请求

请处理上述请求："""

    try:
        llm = LLMClient()
        return await llm.chat_with_retry(full_message)
    except Exception as e:
        logger.error(f"[TuningFallback] Failed: {e}")
        return f"修改失败：{e}"


# ============================================================================
# API-compatible Functions
# ============================================================================

async def run_plan_execute(
    session_id: str,
    stage: str,
    user_input: str = None
) -> Dict[str, Any]:
    """运行 Plan-Execute Agent 指定阶段（兼容旧 API）。

    Args:
        session_id: 会话 ID
        stage: 要执行的阶段（outline_generation, outline_revision, content_generation）
        user_input: 用户输入（outline_generation 和 outline_revision 时需要）

    Returns:
        更新后的 Session (dict format)
    """
    from forge.deep_mode.session_manager import get_session_manager

    session_manager = get_session_manager()
    session = await session_manager.load_session(session_id)

    source_article = session.get("source_article", {})
    outline = session.get("outline", "")
    rag_context = session.get("rag_context", "")

    if stage == "outline_generation":
        # 搜索知识库
        rag_context = await rag_search(source_article)

        # 生成大纲
        outline = await generate_outline(source_article, user_input or "", rag_context)

        # 更新会话
        session = await session_manager.update_session(
            session_id,
            rag_context=rag_context,
            outline=outline,
            outline_version=1,
            stage="waiting_outline",
        )

    elif stage == "outline_revision":
        # 修改大纲
        revised_outline = await revise_outline(outline, user_input or "")

        new_version = await session_manager.increment_outline_version(session_id)
        session = await session_manager.update_session(
            session_id,
            outline=revised_outline,
            outline_version=new_version,
            stage="waiting_outline",
        )

    elif stage == "content_generation":
        # 生成全文
        draft = await generate_content(outline, source_article, rag_context)

        session = await session_manager.update_session(
            session_id,
            draft_v1=draft,
            current_draft=draft,
            stage="tuning",
        )

    return session


# ============================================================================
# Exports (keep for backward compatibility)
# ============================================================================

# 保留旧 API 兼容的函数名
WorkflowState = DeepModeState  # alias for compatibility

async def run_plan_execute_workflow(
    session_id: str,
    article_id: str,
    source_article: Dict[str, str],
    user_input: str,
) -> Dict[str, Any]:
    """运行完整的 Plan-Execute Workflow（兼容旧 API）。"""
    rag_context = await rag_search(source_article)
    outline = await generate_outline(source_article, user_input, rag_context)

    return {
        "session_id": session_id,
        "article_id": article_id,
        "source_article": source_article,
        "user_input": user_input,
        "outline": outline,
        "outline_version": 1,
        "rag_context": rag_context,
        "stage": "waiting_outline",
        "messages": [],
        "error": None,
    }


async def run_executor_only(
    session_id: str,
    outline: str,
    source_article: Dict[str, str],
    rag_context: str = "",
) -> Dict[str, Any]:
    """只运行 Executor（兼容旧 API）。"""
    draft = await generate_content(outline, source_article, rag_context)

    return {
        "session_id": session_id,
        "outline": outline,
        "source_article": source_article,
        "rag_context": rag_context,
        "draft": draft,
        "stage": "tuning",
        "messages": [AIMessage(content=draft)],
        "error": None,
    }


def get_workflow():
    """兼容旧 API - 已弃用，返回 None。"""
    logger.warning("[Workflow] get_workflow() is deprecated, use run_plan_execute() instead")
    return None


class TuningAgentFallback:
    """兼容旧 API - 已弃用。"""
    async def process_request(self, current_draft: str, user_message: str) -> str:
        logger.warning("[Workflow] TuningAgentFallback is deprecated")
        return await _fallback_tuning(current_draft, user_message)


# ============================================================================
# StateGraph Integration (LangSmith 显示节点名称)
# ============================================================================

# 导出 StateGraph 版本的函数
from forge.deep_mode.graph import (
    get_deep_mode_app,
    get_deep_mode_graph,
    run_outline_generation,
    run_outline_revision,
    run_content_generation,
    run_tuning,
    run_finalize,
    DeepModeGraphState,
)

# StateGraph 版本的 run_plan_execute（可选使用）
async def run_plan_execute_graph(
    session_id: str,
    stage: str,
    user_input: str = None,
    user_feedback: str = None,
) -> Dict[str, Any]:
    """使用 StateGraph 运行 Plan-Execute（LangSmith 显示节点名称）。

    Args:
        session_id: 会话 ID
        stage: 阶段（outline_generation, outline_revision, content_generation）
        user_input: 用户输入（outline_generation 时需要）
        user_feedback: 大纲修改反馈（outline_revision 时需要）

    Returns:
        更新后的状态字典

    LangSmith 显示节点名称：
        - rag_search
        - generate_outline
        - revise_outline
        - generate_content
        - tuning
        - finalize
    """
    from forge.deep_mode.session_manager import get_session_manager

    session_manager = get_session_manager()
    session = await session_manager.load_session(session_id)

    source_article = session.get("source_article", {})

    if stage == "outline_generation":
        # 使用 StateGraph 运行
        result = await run_outline_generation(
            session_id=session_id,
            source_article=source_article,
            user_input=user_input or "",
        )

        # 更新 session
        session = await session_manager.update_session(
            session_id,
            rag_context=result.get("rag_context", ""),
            outline=result.get("outline", ""),
            outline_version=result.get("outline_version", 1),
            stage=result.get("stage", "waiting_outline"),
        )

    elif stage == "outline_revision":
        # 获取当前状态
        current_state = {
            "session_id": session_id,
            "source_article": source_article,
            "outline": session.get("outline", ""),
            "outline_version": session.get("outline_version", 1),
            "rag_context": session.get("rag_context", ""),
        }

        # 使用 StateGraph 运行
        result = await run_outline_revision(
            session_id=session_id,
            current_state=current_state,
            user_feedback=user_feedback or user_input or "",
        )

        # 更新 session
        session = await session_manager.update_session(
            session_id,
            outline=result.get("outline", ""),
            outline_version=result.get("outline_version", session.get("outline_version", 1) + 1),
            stage=result.get("stage", "waiting_outline"),
        )

    elif stage == "content_generation":
        # 获取当前状态
        current_state = {
            "session_id": session_id,
            "source_article": source_article,
            "outline": session.get("outline", ""),
            "outline_version": session.get("outline_version", 1),
            "rag_context": session.get("rag_context", ""),
        }

        # 使用 StateGraph 运行
        result = await run_content_generation(
            session_id=session_id,
            current_state=current_state,
        )

        # 更新 session
        session = await session_manager.update_session(
            session_id,
            draft_v1=result.get("draft_v1", ""),
            current_draft=result.get("current_draft", ""),
            template_id=result.get("template_id"),  # 用于效果统计
            stage=result.get("stage", "tuning"),
        )

    return session