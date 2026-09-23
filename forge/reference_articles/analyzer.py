"""无模型依赖的文章分析、归类和近重复指纹。"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

from forge.reference_articles.models import (
    AnalyzedReferenceArticle,
    ArticleArchetype,
    CompositionType,
    ReferenceSection,
    StyleProfile,
)

BRAND_TERMS = ("锐博集团", "锐博", "集团业务", "服务客户", "集团成立", "集团总部")
POLICY_TERMS = (
    "政策", "规定", "办法", "条例", "通知", "实施", "施行", "监管", "合规",
    "人社", "税务", "法院", "国务院", "劳动合同", "劳务派遣", "社保",
)
RISK_TERMS = ("严查", "风险", "处罚", "后果", "预警", "风暴", "违法", "追责")
GUIDE_TERMS = ("如何", "攻略", "指南", "实操", "计算", "步骤", "应对", "案例")
TRANSITIONS = ("因此", "不过", "同时", "此外", "实际上", "那么", "接下来", "这意味着")
GENERIC_COPY_PHRASES = (
    "接下来，让我们一同深入剖析",
    "面对这一变化，企业并非束手无策",
    "综上所述",
    "值得注意的是",
)


def normalize_text(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text or "")
    return re.sub(r"\s+", "", text).lower()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def dedup_fingerprint(text: str, width: int = 5) -> str:
    """对高频字符 shingles 做稳定指纹，降低换标点造成的重复漏检。"""
    normalized = "".join(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", normalize_text(text)))
    if len(normalized) <= width:
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    shingles = Counter(normalized[i:i + width] for i in range(len(normalized) - width + 1))
    canonical = "|".join(
        value for value, _ in sorted(shingles.items(), key=lambda item: (-item[1], item[0]))[:128]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[。！？!?；;])", text) if part.strip()]


def split_sentences(text: str) -> list[str]:
    return _sentences(text)


def _paragraphs(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n+", text) if part.strip()]
    if len(paragraphs) > 1:
        return paragraphs
    sentences = _sentences(text)
    return ["".join(sentences[i:i + 4]) for i in range(0, len(sentences), 4)] or [text]


def _density(text: str, terms: tuple[str, ...]) -> float:
    size = max(1, len(normalize_text(text)))
    return round(sum(text.count(term) for term in terms) * 1000 / size, 4)


def _composition(text: str) -> CompositionType:
    brand = sum(text.count(term) for term in BRAND_TERMS)
    policy = sum(text.count(term) for term in POLICY_TERMS)
    if brand >= 2 and policy >= 3:
        return "mixed"
    if brand >= 2 and brand >= policy:
        return "company"
    return "policy"


def _archetype(title: str, text: str, composition: CompositionType) -> ArticleArchetype:
    combined = title + text[:1500]
    if composition == "company":
        return "company_profile"
    if composition == "mixed":
        return "trend_brand_mix"
    if any(term in combined for term in RISK_TERMS):
        return "risk_warning"
    if sum(combined.count(term) for term in GUIDE_TERMS) >= 2:
        return "practical_guide"
    if any(term in title for term in ("新规", "新政", "解读", "发布", "明确")):
        return "policy_quick_read"
    return "industry_analysis"


def _style_profile(text: str) -> StyleProfile:
    sentences = _sentences(text)
    paragraphs = _paragraphs(text)
    visible = max(1, len(normalize_text(text)))
    questions = text.count("？") + text.count("?")
    exclamations = text.count("！") + text.count("!")
    list_markers = len(re.findall(r"(?:^|\n)\s*(?:\d+[、.)）]|[一二三四五六七八九十]+、)", text))
    first_person = sum(text.count(term) for term in ("我们", "笔者", "大家", "你", "您"))
    transition_count = sum(text.count(term) for term in TRANSITIONS)
    question_ratio = questions / max(1, len(sentences))
    exclamation_ratio = exclamations / max(1, len(sentences))
    if question_ratio >= 0.08 or exclamation_ratio >= 0.08:
        tone = "强钩子、问答推进"
    elif first_person * 1000 / visible >= 1.0:
        tone = "对话式、解释型"
    else:
        tone = "专业、克制"
    return StyleProfile(
        average_sentence_chars=round(sum(map(len, sentences)) / max(1, len(sentences)), 1),
        average_paragraph_chars=round(sum(map(len, paragraphs)) / max(1, len(paragraphs)), 1),
        question_ratio=round(question_ratio, 4),
        exclamation_ratio=round(exclamation_ratio, 4),
        list_marker_ratio=round(list_markers / max(1, len(paragraphs)), 4),
        number_density=round(len(re.findall(r"\d+(?:\.\d+)?%?", text)) * 1000 / visible, 4),
        first_person_density=round(first_person * 1000 / visible, 4),
        transition_density=round(transition_count * 1000 / visible, 4),
        tone=tone,
    )


ROLE_SEQUENCES: dict[str, tuple[str, ...]] = {
    "company_profile": ("定位开场", "发展历程", "业务能力", "成果证明", "品牌收束"),
    "policy_quick_read": ("政策变化", "核心条款", "影响对象", "行动建议"),
    "risk_warning": ("风险钩子", "规则解释", "后果拆解", "计算或案例", "合规建议"),
    "industry_analysis": ("问题提出", "背景分析", "关键判断", "趋势影响", "结论"),
    "practical_guide": ("场景导入", "规则说明", "操作步骤", "案例测算", "检查清单"),
    "trend_brand_mix": ("热点导入", "政策或趋势分析", "企业影响", "锐博能力", "行动建议"),
}


def _sections(title: str, text: str, archetype: str) -> tuple[ReferenceSection, ...]:
    paragraphs = _paragraphs(text)
    roles = ROLE_SEQUENCES[archetype]
    sections: list[ReferenceSection] = []
    for index, role in enumerate(roles):
        start = index * len(paragraphs) // len(roles)
        end = max(start + 1, (index + 1) * len(paragraphs) // len(roles))
        excerpt = "".join(paragraphs[start:end])[:180]
        sections.append(ReferenceSection(role, title if index == 0 else role, excerpt, index))
    return tuple(sections)


def analyze_reference_article(raw: dict[str, Any]) -> AnalyzedReferenceArticle:
    article_id = str(raw.get("id") or "").strip()
    title = str(raw.get("title") or "").strip()
    content = str(raw.get("content") or raw.get("text") or "").strip()
    if not article_id or not title or len(normalize_text(content)) < 100:
        raise ValueError("参考文章必须包含 id、title 和至少 100 个有效字符")
    composition = _composition(content)
    archetype = _archetype(title, content, composition)
    profile = _style_profile(content)
    topic_text = f"{title}\n{content[:1800]}"
    style_text = (
        f"原型={archetype};构成={composition};语气={profile.tone};"
        f"句长={profile.average_sentence_chars};段长={profile.average_paragraph_chars};"
        f"问句率={profile.question_ratio};感叹率={profile.exclamation_ratio};"
        f"列表率={profile.list_marker_ratio};数字密度={profile.number_density}"
    )
    return AnalyzedReferenceArticle(
        article_id=article_id,
        title=title,
        content=content,
        source_url=str(raw.get("source_url") or ""),
        platform=str(raw.get("platform") or "zhihu_article"),
        composition_type=composition,
        archetype=archetype,
        topic_text=topic_text,
        style_text=style_text,
        content_hash=content_hash(content),
        dedup_fingerprint=dedup_fingerprint(content),
        style_profile=profile,
        sections=_sections(title, content, archetype),
        publish_date=str(raw.get("publish_date") or ""),
        author=str(raw.get("author") or ""),
    )


def generic_copy_phrases() -> tuple[str, ...]:
    return GENERIC_COPY_PHRASES


def discover_boilerplate(sentences_by_article: list[list[str]], min_documents: int = 3) -> set[str]:
    """找出跨文章重复的长句；只按文档频次计数，避免单篇重复污染结果。"""
    frequency: Counter[str] = Counter()
    for sentences in sentences_by_article:
        unique = {
            normalize_text(sentence)
            for sentence in sentences
            if 24 <= len(normalize_text(sentence)) <= 500
        }
        frequency.update(unique)
    return {sentence for sentence, count in frequency.items() if count >= min_documents}


def strip_boilerplate(text: str, boilerplate: set[str]) -> tuple[str, list[str]]:
    kept: list[str] = []
    removed: list[str] = []
    for sentence in _sentences(text):
        if normalize_text(sentence) in boilerplate:
            removed.append(sentence)
        else:
            kept.append(sentence)
    cleaned = "".join(kept).strip()
    return (cleaned or text.strip()), removed


COMPANY_FACT_TERMS = (
    "成立", "总部", "营收", "员工", "客户", "业务覆盖", "服务范围", "资质", "分公司", "直营网点",
)
TEMPORAL_TERMS = ("目前", "当前", "近期", "今年", "去年", "最新", "截至")


def extract_company_fact_candidates(article_id: str, text: str) -> list[dict[str, Any]]:
    """只产出待人工审核的集团事实候选，不直接写入正式事实知识库。"""
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sentence in _sentences(text):
        normalized = normalize_text(sentence)
        if not any(term in sentence for term in BRAND_TERMS):
            continue
        if not any(term in sentence for term in COMPANY_FACT_TERMS):
            continue
        fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        candidates.append({
            "article_id": article_id,
            "claim": sentence.strip(),
            "normalized_hash": fingerprint,
            "is_temporal": bool(re.search(r"(?:19|20)\d{2}年?", sentence))
            or any(term in sentence for term in TEMPORAL_TERMS),
            "review_status": "pending",
        })
    return candidates
