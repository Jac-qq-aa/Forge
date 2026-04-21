"""AI Detector Node - Detect AI-generated content using Perplexity and Burstiness analysis.

This node uses an INDEPENDENT model for judgment (qwen-max),
separate from the rewriting model (qwen-plus), ensuring objective AI detection.

Model isolation (same API, different models):
- Rewriting: qwen-plus (Editor, Humanizer_Editor)
- Judgment: qwen-max (AI_Detector) - stronger model
"""

import logging
import re
import numpy as np
from forge.graph.state import GraphState
from forge.tools.judge_llm_client import JudgeLLMClient, has_judge_client, JudgeLLMClientError
from forge.config import AI_THRESHOLD

logger = logging.getLogger(__name__)

# AI常用词汇列表（英文+中文）
AI_COMMON_WORDS = [
    # 英文 AI 常用词
    "delve into", "tapestry", "furthermore", "moreover", "in conclusion",
    "it is worth noting", "comprehensive", "multifaceted", "nuanced",
    "underscore", "emphasize", "highlight", "pivotal", "crucial",
    "embark on", "landscape", "realm", "sphere", "paradigm",
    # 中文 AI 常用词
    "综上所述", "总而言之", "值得注意的是", "值得一提的是",
    "作为一个", "从角度来看", "从某种程度上", "不可否认",
    "毋庸置疑", "显而易见", "众所周知", "由此可见",
    "首先", "其次", "最后", "一方面", "另一方面",
]


def local_ai_detection(content: str) -> dict:
    """本地 AI 特征统计分析（Fallback方案）。

    当 Claude API 不可用时使用。

    Args:
        content: 文本内容。

    Returns:
        包含 ai_score, burstiness, ai_word_count 的字典。
    """
    # 1. 计算句子长度变化（Burstiness）
    sentences = re.split(r'[。！？\n]', content)
    lengths = [len(s) for s in sentences if s.strip()]

    if lengths:
        burstiness = np.std(lengths) / np.mean(lengths) if np.mean(lengths) > 0 else 0
    else:
        burstiness = 0

    # 2. 统计 AI 常用词
    ai_word_count = sum(1 for word in AI_COMMON_WORDS if word in content)

    # 3. 估算 AI 概率
    ai_score = 0.3  # 基础分数
    if burstiness < 0.5:
        ai_score += 0.3  # 低变化度 → AI特征
    if ai_word_count > 3:
        ai_score += 0.2 * min(ai_word_count - 3, 5)  # AI词汇

    logger.info(f"[AI_Detector] Local fallback: burstiness={burstiness:.2f}, ai_words={ai_word_count}, score={ai_score:.2f}")

    return {
        "ai_score": min(ai_score, 1.0),
        "burstiness": burstiness,
        "ai_word_count": ai_word_count,
    }


async def ai_detector_node(state: GraphState) -> dict:
    """Detect AI-generated content characteristics using Claude.

    Uses independent Claude model for objective judgment,
    separate from the Qwen rewriting model.

    Args:
        state: Current workflow state.

    Returns:
        Updated state with ai_score, humanize_feedback, ruibo_feedback.
    """
    rewritten_draft = state.get("rewritten_draft", "")
    raw_content = state.get("raw_content", {})
    humanize_revisions = state.get("humanize_revisions", 0)

    logger.info(f"[AI_Detector] Starting detection (attempt {humanize_revisions + 1})")
    logger.info(f"[AI_Detector] Using qwen-max for independent judgment (isolated from qwen-plus rewriting)")

    original_title = raw_content.get("title", "")
    original_text = raw_content.get("text", "")

    # 检查是否配置了判断模型
    if not has_judge_client():
        logger.warning("[AI_Detector] QWEN_API_KEY not configured, using local fallback")
        local_result = local_ai_detection(rewritten_draft)
        ai_score = local_result["ai_score"]
        humanize_feedback = f"本地检测：AI概率 {ai_score:.0%}（建议配置 Qwen API Key）" if ai_score > AI_THRESHOLD else ""
        return {
            "ai_score": ai_score,
            "humanize_feedback": humanize_feedback,
            "ruibo_feedback": "",
            "humanize_revisions": humanize_revisions,
        }

    # 使用 Claude 进行独立判断
    try:
        judge = JudgeLLMClient()

        # 分析 Prompt：关注 Perplexity 和 Burstiness
        prompt = f"""请作为专业的AI内容检测专家，分析以下文章的 AI 生成概率。

注意：你是独立的判断模型，需要客观评估内容特征，不受改写模型的影响。

## 分析维度

### 1. Perplexity (文本复杂度/惊喜度)
- 词汇多样性：是否使用丰富、不常见的词汇组合？
- 表达惊喜感：是否有意想不到的表达方式？
- AI文本特征：低 Perplexity = 平淡、可预测的词汇组合

### 2. Burstiness (句子长度变化)
- 长短句变化：句子长度是否有明显波动？
- 结构多样性：是否混合简单句、复杂句、并列句？
- AI文本特征：低 Burstiness = 均匀、模板化的句子长度

### 3. AI 常用词汇检测
检查是否频繁出现以下 AI 常用表达：
- 英文：delve into, tapestry, furthermore, moreover, comprehensive
- 中文：综上所述, 总而言之, 值得注意的是, 首先其次最后

### 4. 锐博品牌嵌入合理性
- 锐博信息占比是否过高（建议不超过全文15%）
- 是否自然融入原文观点，而非生硬插入
- 是否喧宾夺主，把原文观点变成锐博广告

---

文章标题：{original_title}
文章内容：
{rewritten_draft}

原文核心观点摘要：
{original_text[:300]}...

---

请严格按以下格式回复（必须包含具体数值）：

【AI概率得分】0.XX (例如: 0.85 表示 85% 概率是AI生成)

【Perplexity分析】高/中/低
原因：(简短说明)

【Burstiness分析】高/中/低
原因：(简短说明)

【AI词汇检测】发现/未发现
具体词汇：(列出检测到的AI常用词)

【锐博品牌审核】通过/不通过
原因：(简短说明)

【人性化建议】(如果AI得分>0.5，给出具体修改建议)
"""

        system_prompt = """你是一位专业的AI内容检测专家，使用 qwen-max 模型进行独立判断。
你的判断独立于改写模型(qwen-plus)，目标是客观评估内容的AI生成特征。
重点分析 Perplexity 和 Burstiness 两个维度。
请严格按照指定格式回复，确保数值准确。"""

        response = await judge.judge_with_retry(prompt, system_prompt)
        logger.info(f"[AI_Detector] Judge model response: {response[:300]}...")

    except JudgeLLMClientError as e:
        logger.error(f"[AI_Detector] Judge model API failed: {e}, using local fallback")
        local_result = local_ai_detection(rewritten_draft)
        ai_score = local_result["ai_score"]
        humanize_feedback = f"判断模型失败，本地估算AI概率 {ai_score:.0%}" if ai_score > AI_THRESHOLD else ""
        return {
            "ai_score": ai_score,
            "humanize_feedback": humanize_feedback,
            "ruibo_feedback": "",
            "humanize_revisions": humanize_revisions,
        }

    # 解析 Claude 的判断结果
    ai_score = 0.0
    score_match = re.search(r"【AI概率得分】(\d+\.?\d*)", response)
    if score_match:
        try:
            ai_score = float(score_match.group(1))
            if ai_score > 1.0:
                ai_score = ai_score / 100
            ai_score = max(0.0, min(1.0, ai_score))
        except ValueError:
            logger.warning("[AI_Detector] Failed to parse judge model score")
            ai_score = 0.5

    # 提取其他分析结果
    perplexity = "中"
    perplexity_match = re.search(r"【Perplexity分析】(高|中|低)", response)
    if perplexity_match:
        perplexity = perplexity_match.group(1)

    burstiness = "中"
    burstiness_match = re.search(r"【Burstiness分析】(高|中|低)", response)
    if burstiness_match:
        burstiness = burstiness_match.group(1)

    # 提取锐博品牌审核
    ruibo_passed = "通过" in response and "【锐博品牌审核】通过" in response
    ruibo_feedback = ""
    if not ruibo_passed:
        ruibo_match = re.search(r"【锐博品牌审核】不通过\s*\n原因：([^\n【]+)", response)
        if ruibo_match:
            ruibo_feedback = f"锐博品牌问题：{ruibo_match.group(1).strip()}"

    # 构建人性化反馈
    humanize_feedback = ""
    if ai_score > AI_THRESHOLD:
        humanize_match = re.search(r"【人性化建议】(.+)", response)
        if humanize_match:
            humanize_feedback = humanize_match.group(1).strip()
        else:
            humanize_feedback = f"""判断模型(qwen-max)分析：AI概率 {ai_score:.0%}
- Perplexity: {perplexity}
- Burstiness: {burstiness}

修改建议：
1. 增加长短句交替
2. 使用口语化表达
3. 替换AI常用词
4. 加入个人观点"""

    logger.info(f"[AI_Detector] Judgment result: ai_score={ai_score:.2f}")
    logger.info(f"[AI_Detector] Perplexity: {perplexity}, Burstiness: {burstiness}")
    logger.info(f"[AI_Detector] Ruibo passed: {ruibo_passed}")
    logger.info(f"[AI_Detector] Needs humanization: {ai_score > AI_THRESHOLD}")

    return {
        "ai_score": ai_score,
        "humanize_feedback": humanize_feedback,
        "ruibo_feedback": ruibo_feedback,
        "humanize_revisions": humanize_revisions,
    }