"""Humanizer Editor Node - Reduce AI-generated content characteristics.

This node specializes in "humanizing" AI-generated content by:
1. Increasing Burstiness (sentence length variation)
2. Using more colloquial, personal expressions
3. Replacing common AI phrases with natural alternatives
4. Adding personal opinions and emotional expressions

Handles empty LLM response gracefully to prevent content loss.
"""

import logging
from langsmith import traceable
from forge.graph.state import GraphState
from forge.tools.llm_client import LLMClient
from forge.config import MAX_HUMANIZE_REVISIONS
from forge.evaluation.probe_decorator import with_probe

logger = logging.getLogger(__name__)

# AI词汇到自然表达的映射
AI_TO_NATURAL_MAP = {
    "综上所述": "总结来说", "总而言之": "简单总结一下",
    "值得注意的是": "有意思的是", "值得一提的是": "值得一提的是",  # 保持，但减少使用频率
    "作为一个": "身为", "从...角度来看": "换个角度看",
    "从某种程度上": "某种程度上", "不可否认": "必须承认",
    "毋庸置疑": "毫无疑问", "显而易见": "很明显",
    "众所周知": "大家都知道", "由此可见": "这说明",
    "首先": "第一", "其次": "第二", "最后": "最后一点",
}


@traceable(name="Humanizer_Editor")
@with_probe("humanizer_editor", loop_type="humanize_loop")
async def humanizer_editor_node(state: GraphState) -> dict:
    """Humanize AI-generated content to reduce AI score.

    Focuses on:
    - Burstiness: Create varied sentence lengths (short punchy + long flowing)
    - Perplexity: Use surprising, diverse vocabulary
    - Personal voice: Add opinions, emotions, conversational tone

    Args:
        state: Current workflow state.

    Returns:
        Updated state with rewritten_draft and incremented humanize_revisions.
    """
    rewritten_draft = state.get("rewritten_draft", "")
    humanize_feedback = state.get("humanize_feedback", "")
    ruibo_feedback = state.get("ruibo_feedback", "")
    humanize_revisions = state.get("humanize_revisions", 0)
    raw_content = state.get("raw_content", {})

    logger.info(f"[Humanizer] Starting humanization (attempt {humanize_revisions + 1}/{MAX_HUMANIZE_REVISIONS})")

    original_title = raw_content.get("title", "")
    original_text = raw_content.get("text", "")

    # 检查是否已达到最大迭代次数
    if humanize_revisions >= MAX_HUMANIZE_REVISIONS:
        logger.info(f"[Humanizer] Max revisions ({MAX_HUMANIZE_REVISIONS}) reached, keeping current content")
        return {
            "rewritten_draft": rewritten_draft,
            "humanize_revisions": humanize_revisions,
        }

    llm = LLMClient()

    # 根据迭代次数调整严格程度
    strictness_note = ""
    if humanize_revisions >= 2:
        strictness_note = "\n注意：这是最后一次修改机会，请适度放宽标准，只修改最明显的问题，保持内容完整性。"

    # 构建 Prompt，强调 Burstiness 和口语化
    prompt = f"""请对以下文章进行"人性化"改写，降低 AI 生成特征。

## 核心目标

### 1. 增加 Burstiness（句子长度变化）
- 穿插使用极短的句子（5-10字）和较长的复杂句（30-50字）
- 避免"均匀"的句子长度分布
- 示例："这个问题很复杂。但如果我们换个角度思考，会发现其中蕴含着更深层的意义，值得深入探讨。"

### 2. 提高 Perplexity（词汇多样性）
- 使用不常见、有"惊喜感"的词汇组合
- 避免平淡、可预测的表达
- 示例："这个现象很普遍" → "这个现象几乎随处可见，却又容易被忽视"

### 3. 替换 AI 常用词
以下词汇请尽量替换：
- "综上所述" → 用自然总结替代
- "首先/其次/最后" → 用更灵活的过渡
- "作为一个..." → 用具体身份替代或省略
- "从...角度来看" → 用更直接的表达

### 4. 加入个人观点和情感
- 使用"我认为"、"我觉得"、"在我看来"等表达
- 加入情感词："令人惊讶的是"、"让人感慨的是"
- 避免"客观"的模板化陈述

{strictness_note}

---

当前文章：
{rewritten_draft}

原文核心观点（必须保留）：
标题：{original_title}
核心内容：{original_text[:200]}...

问题诊断：
{humanize_feedback}

品牌问题（如有）：
{ruibo_feedback}

---

改写要求：
1. 【重要】必须保留原文的核心观点和主要内容
2. 增加句子长度的多样性（长短句交替）
3. 使用更口语化、个人化的表达
4. 替换检测到的 AI 常用词
5. 加入个人观点和情感表达
6. 如果有品牌问题，调整锐博信息的融入方式
7. 控制篇幅与原文相近
8. ⚠️ 信息真实性红线：绝不编造具体信息（课程名、时间、数字等），引用知识库信息必须标注来源

直接输出改写后的完整内容（不要分段输出）。"""

    system_prompt = """你是一位擅长"人性化"改写的编辑，专门降低内容的AI生成特征。
你的核心技巧是：
1. Burstiness：制造句子长度的大幅波动
2. Perplexity：使用令人惊喜的词汇组合
3. Personal voice：加入个人观点和情感

重要原则：
- 原文核心观点必须保留
- 不能大幅删减内容
- 如果输入内容有问题，只针对性修改，不要大范围重写
- ⚠️ 信息真实性红线：绝不编造具体信息，引用必须标注来源"""

    new_content = await llm.chat_with_retry(prompt, system_prompt)

    # 关键：处理空内容返回
    if not new_content or new_content.strip() == "":
        logger.warning("[Humanizer] LLM returned empty content! Keeping original draft.")
        # 不清空内容，保持原文，增加计数并继续
        return {
            "rewritten_draft": rewritten_draft,  # 保持原文
            "humanize_revisions": humanize_revisions + 1,
        }

    # 检查内容长度是否大幅减少（可能表示失败）
    if len(new_content) < len(rewritten_draft) * 0.5:
        logger.warning(f"[Humanizer] Content length dropped significantly: {len(rewritten_draft)} → {len(new_content)}")
        # 仍然使用新内容，但记录警告
        # 如果连续两次长度大幅减少，可能是系统性问题

    logger.info(f"[Humanizer] Generated new draft ({len(new_content)} chars)")
    logger.info(f"[Humanizer] Content change: {len(rewritten_draft)} → {len(new_content)} chars")

    return {
        "rewritten_draft": new_content,
        "humanize_revisions": humanize_revisions + 1,
        "humanize_feedback": "",  # 清除反馈，让 AI_Detector 重新评估
        "ruibo_feedback": "",  # 清除品牌反馈
    }