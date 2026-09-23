"""Adaptive evidence-grounded writing with explicit length budgets and memory."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from forge.agents.research_types import (
    DraftFact,
    FactMatchSummary,
    GenerationPlan,
    GenerationSection,
    GeneratorAction,
    QualityScores,
    ReflectionIssue,
    ReflectionResult,
    ResearchResult,
    RevisionAction,
    RevisionPlan,
    IssueResolution,
    WordBudget,
    WordCountReport,
    WritingMemory,
    format_verified_facts,
    is_fact_approved,
)
from forge.agents.generator_agent import run_generator_agent
from forge.agents.revision_agent import reconcile_issue_resolutions, run_revision_agent
from forge.tools.llm_client import LLMClient
from forge.reference_articles.models import ReferencePack

logger = logging.getLogger(__name__)

MAX_REFLECTION_REVISIONS = int(os.getenv("GENERATION_MAX_REVISION_ROUNDS", "2"))
PASS_THRESHOLDS: QualityScores = {
    "grounding": 0.9,
    "completeness": 0.8,
    "structure": 0.75,
    "style": 0.75,
}
PLATFORM_DEFAULT_TARGETS = {
    "xhs_video": 900,
    "zhihu_article": 2000,
    "zhihu_video": 1500,
}


class ReflectionState(TypedDict, total=False):
    research_result: ResearchResult
    outline: str
    raw_content: dict[str, Any]
    target_platform: str
    user_input: str
    generation_plan: GenerationPlan
    writing_memory: WritingMemory
    section_reports: list[GenerationSection]
    word_count: WordCountReport
    current_draft: str
    generator_action_log: list[GeneratorAction]
    generator_tool_action_count: int
    generator_model_call_count: int
    generator_stop_reason: str
    scores: QualityScores
    issues: list[ReflectionIssue]
    draft_facts: list[DraftFact]
    fact_match_summary: FactMatchSummary
    verification_log: list[str]
    revision_attempts: int
    approved: bool
    status: str
    revision_plan: RevisionPlan
    revision_action_log: list[RevisionAction]
    issue_resolution_log: list[IssueResolution]
    revision_cycles: int
    tool_action_count: int
    unresolved_issues: list[str]
    reference_pack: ReferencePack


def _parse_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("critic response does not contain JSON")
        candidate = candidate[start:end + 1]
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise ValueError("critic response must be an object")
    return value


def _platform_style(platform: str) -> str:
    return {
        "xhs_video": "小红书短视频：口语化、适合朗读、节奏明快，不堆砌术语",
        "zhihu_article": "知乎文章：论证清晰、专业克制、事实与观点边界明确",
        "zhihu_video": "知乎视频：叙述自然、适合口播、有清晰的信息递进",
    }.get(platform, "通用文章：准确、清楚、结构完整")


def count_visible_chars(text: str) -> int:
    """Count user-visible Chinese characters and alphanumerics, excluding URLs/markup."""
    without_urls = re.sub(r"https?://\S+", "", text)
    without_markup = re.sub(r"[`#*_>\-]", "", without_urls)
    return len(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", without_markup))


def _word_status(actual: int, minimum: int, maximum: int) -> str:
    if actual < minimum:
        return "too_short"
    if actual > maximum:
        return "too_long"
    return "within_budget"


def _word_count_report(actual: int, target: int, minimum: int, maximum: int) -> WordCountReport:
    return {
        "target_chars": target,
        "min_chars": minimum,
        "max_chars": maximum,
        "actual_chars": actual,
        "deviation": round((actual - target) / target, 3) if target else 0.0,
        "status": _word_status(actual, minimum, maximum),  # type: ignore[typeddict-item]
    }


def make_word_count_report(draft: str, plan: GenerationPlan) -> WordCountReport:
    """Recount a draft against an existing generation plan."""
    return _word_count_report(
        count_visible_chars(draft),
        max(1, plan.get("target_chars", 1)),
        plan.get("min_chars", 0),
        plan.get("max_chars", max(1, count_visible_chars(draft))),
    )


PREDICATE_ALIASES = {
    "founding_date": {"成立时间", "成立于", "成立在", "创办于", "创立于", "始建于", "正式成立"},
    "project_start_date": {"项目启动时间", "启动于", "启动时间", "开始于", "正式启动"},
    "customer_count": {"客户数量", "服务客户", "客户达到", "覆盖客户"},
    "employee_count": {"员工数量", "员工人数", "拥有员工"},
    "project_location": {"项目地点", "项目位于", "坐落于"},
    "headquarters_location": {"总部地点", "总部位于", "总部设在"},
}


def _canonical_predicate(value: str) -> str:
    normalized = re.sub(r"\s+", "", value).lower()
    for canonical, aliases in PREDICATE_ALIASES.items():
        if normalized == canonical or any(alias in normalized for alias in aliases):
            return canonical
    return normalized or "unknown"


def _normalize_scalar(value: Any, value_type: str) -> str:
    text = re.sub(r"\s+", "", str(value or "")).strip("，。；;：:")
    if value_type in {"year", "date"}:
        year = re.search(r"(?<!\d)(\d{4})(?:年)?", text)
        if year:
            return year.group(1)
    if value_type in {"number", "percentage", "money"}:
        number = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
        if number:
            return number.group(0)
    return text.lower()


def _normalize_draft_facts(value: Any) -> list[DraftFact]:
    if not isinstance(value, list):
        raise ValueError("fact extractor response must contain draft_facts list")
    facts: list[DraftFact] = []
    allowed_statuses = {
        "supported", "contradicted", "unsupported", "partial", "not_verifiable"
    }
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or "").strip()
        span = str(item.get("span") or claim).strip()[:500]
        if not claim or not span:
            continue
        value_type = str(item.get("value_type") or "text").strip().lower()
        raw_value = str(item.get("value") or "").strip()
        status = str(item.get("match_status") or "not_verifiable")
        if status not in allowed_statuses:
            status = "not_verifiable"
        matched_ids = [
            str(fact_id) for fact_id in item.get("matched_fact_ids", [])
            if re.fullmatch(r"F\d{3}", str(fact_id))
        ]
        confidence = max(0.0, min(1.0, float(item.get("confidence") or 0.0)))
        qualifiers = item.get("qualifiers")
        facts.append({
            "draft_fact_id": f"DF{index:03d}",
            "claim": claim,
            "span": span,
            "section_id": str(item.get("section_id") or ""),
            "subject": str(item.get("subject") or "").strip(),
            "predicate": _canonical_predicate(str(item.get("predicate") or "")),
            "value": raw_value,
            "normalized_value": _normalize_scalar(raw_value, value_type),
            "value_type": value_type,
            "unit": str(item.get("unit") or "").strip(),
            "qualifiers": {
                str(key): str(item_value)
                for key, item_value in (qualifiers.items() if isinstance(qualifiers, dict) else [])
            },
            "matched_fact_ids": list(dict.fromkeys(matched_ids)),
            "match_status": status,  # type: ignore[typeddict-item]
            "confidence": round(confidence, 3),
            "conflicting_fields": [],
        })
    return facts


async def extract_draft_facts(
    draft: str,
    research_result: ResearchResult,
    sections: list[GenerationSection],
) -> list[DraftFact]:
    """Extract atomic draft claims and propose candidate registry facts for matching."""
    facts = format_verified_facts(research_result)
    section_text = "\n".join(
        f"{section['section_id']} {section['title']} facts={','.join(section['fact_ids']) or 'none'}"
        for section in sections
    ) or "没有结构化章节"
    prompt = f"""你是文章原子事实抽取器。只抽取可由证据验证的客观陈述，不抽取观点、修辞或过渡句。

## 已确认事实注册表
{facts}

## 章节与允许事实
{section_text}

## 待检查文章
{draft}

将每个事实拆成单一断言，并标准化主体、关系和值。关系优先使用：
founding_date、project_start_date、customer_count、employee_count、project_location、headquarters_location；
其他关系使用简短 snake_case。"成立于"、"成立在"、"创办于"统一为 founding_date。

matched_fact_ids 是语义上描述同一主体和关系的候选 Fact ID，不要求值相同。
match_status 是初步语义判断：supported、contradicted、unsupported、partial、not_verifiable。
只输出 JSON：
{{
  "draft_facts": [{{
    "claim": "原子事实",
    "span": "文章中的原文片段",
    "section_id": "S01",
    "subject": "主体",
    "predicate": "founding_date",
    "value": "2022年",
    "value_type": "year|date|number|percentage|money|entity|location|text",
    "unit": "年或空字符串",
    "qualifiers": {{}},
    "matched_fact_ids": ["F001"],
    "match_status": "contradicted",
    "confidence": 0.0
  }}]
}}
"""
    payload = _parse_json_object(await LLMClient().chat_with_retry(prompt))
    if "draft_facts" not in payload:
        raise ValueError("fact extractor response missing draft_facts")
    return _normalize_draft_facts(payload["draft_facts"])


def _registered_value(claim: str, predicate: str, value_type: str) -> str:
    if predicate == "founding_date":
        match = re.search(
            r"(?:成立|创办|创立|始建)(?:时间)?(?:于|在)?\s*(\d{4})年?",
            claim,
        )
        return match.group(1) if match else ""
    if predicate == "project_start_date":
        match = re.search(r"(?:启动|开始)(?:时间)?(?:于|在)?\s*(\d{4})年?", claim)
        return match.group(1) if match else ""
    if value_type in {"year", "date"}:
        years = re.findall(r"(?<!\d)(\d{4})年?", claim)
        return years[0] if len(years) == 1 else ""
    if value_type in {"number", "percentage", "money"}:
        numbers = re.findall(r"-?\d+(?:\.\d+)?", claim.replace(",", ""))
        return numbers[0] if len(numbers) == 1 else ""
    return ""


def _predicate_from_claim(claim: str) -> str:
    return _canonical_predicate(claim)


def match_draft_facts(
    draft_facts: list[DraftFact],
    research_result: ResearchResult,
) -> list[DraftFact]:
    """Validate model-proposed matches and deterministically arbitrate comparable values."""
    approved = {
        fact["fact_id"]: fact
        for fact in research_result.get("facts", [])
        if is_fact_approved(fact)
    }
    matched: list[DraftFact] = []
    for source in draft_facts:
        item = dict(source)
        candidate_ids = [fact_id for fact_id in source["matched_fact_ids"] if fact_id in approved]
        if not candidate_ids and source["predicate"] != "unknown":
            candidate_ids = [
                fact_id for fact_id, fact in approved.items()
                if _predicate_from_claim(fact["claim"]) == source["predicate"]
            ]
        item["matched_fact_ids"] = candidate_ids
        status = source["match_status"]
        conflicts: list[str] = []
        if not candidate_ids:
            if status != "not_verifiable":
                status = "unsupported"
        else:
            registered_values = {
                _registered_value(
                    approved[fact_id]["claim"], source["predicate"], source["value_type"]
                )
                for fact_id in candidate_ids
            }
            registered_values.discard("")
            draft_value = source["normalized_value"]
            if draft_value and registered_values:
                if draft_value in registered_values:
                    if status in {"contradicted", "unsupported", "not_verifiable"}:
                        status = "supported"
                else:
                    status = "contradicted"
                    conflicts.append("value")
            elif status == "not_verifiable":
                status = "partial"
        item["match_status"] = status
        item["conflicting_fields"] = conflicts
        matched.append(item)  # type: ignore[arg-type]
    return matched


def summarize_fact_matches(draft_facts: list[DraftFact]) -> FactMatchSummary:
    summary: FactMatchSummary = {
        "total": len(draft_facts),
        "supported": 0,
        "contradicted": 0,
        "unsupported": 0,
        "partial": 0,
        "not_verifiable": 0,
    }
    for fact in draft_facts:
        summary[fact["match_status"]] += 1
    return summary


def _fact_match_issues(
    draft_facts: list[DraftFact],
    research_result: ResearchResult,
    start: int,
) -> list[ReflectionIssue]:
    approved = {
        fact["fact_id"]: fact
        for fact in research_result.get("facts", [])
        if is_fact_approved(fact)
    }
    issues: list[ReflectionIssue] = []
    for draft_fact in draft_facts:
        status = draft_fact["match_status"]
        if status not in {"contradicted", "unsupported", "partial"}:
            continue
        fact_id = draft_fact["matched_fact_ids"][0] if draft_fact["matched_fact_ids"] else ""
        expected = approved[fact_id]["claim"] if fact_id in approved else ""
        if status == "contradicted":
            issue_type = "contradiction"
            severity = "high"
            instruction = f"依据 {fact_id} 修正该事实，正确依据为：{expected}"
        elif status == "unsupported":
            issue_type = "unsupported_claim"
            severity = "high"
            instruction = "删除该无依据事实，或改写为事实注册表能够直接支持的内容"
        else:
            issue_type = "partial_fact"
            severity = "medium"
            instruction = f"补全事实的主体、关系和值或限定条件；可用依据：{expected}"
        issues.append({
            "issue_id": f"I{start + len(issues) + 1:03d}",
            "issue_type": issue_type,
            "severity": severity,  # type: ignore[typeddict-item]
            "span": draft_fact["span"],
            "fact_id": fact_id,
            "instruction": instruction,
        })
    return issues


def _parse_outline(outline: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    heading_pattern = re.compile(
        r"^(?:#{1,6}\s*|[一二三四五六七八九十]+[、.]\s*|\d+[、.)．]\s*)(.+)$"
    )
    for line in outline.splitlines():
        clean = line.strip()
        if not clean:
            continue
        match = heading_pattern.match(clean)
        if match:
            title = match.group(1).strip().strip("#")
            if title:
                sections.append((f"S{len(sections) + 1:02d}", title))
    if not sections:
        title = next((line.strip("# ") for line in outline.splitlines() if line.strip()), "正文")
        sections.append(("S01", title[:80]))
    return sections[:12]


def _extract_target_chars(
    user_input: str,
    target_platform: str,
    section_count: int,
    fact_count: int,
    raw_content: dict[str, Any],
) -> tuple[int, str]:
    explicit_value = raw_content.get("target_word_count") or raw_content.get("target_chars")
    if explicit_value:
        try:
            return max(300, min(12000, int(explicit_value))), "使用请求中的明确目标字数"
        except (TypeError, ValueError):
            pass
    match = re.search(r"(\d{3,5})\s*(?:字|字符)", user_input)
    if match:
        return max(300, min(12000, int(match.group(1)))), "使用用户明确指定的目标字数"
    minute_match = re.search(r"(\d+(?:\.\d+)?)\s*分钟", user_input)
    if minute_match and "video" in target_platform:
        target = round(float(minute_match.group(1)) * 240)
        return max(300, min(12000, target)), "根据目标口播时长按每分钟240字估算"
    platform_default = PLATFORM_DEFAULT_TARGETS.get(target_platform, 1600)
    complexity_target = 360 + section_count * 280 + min(fact_count, 20) * 55
    target = max(600, min(platform_default, complexity_target))
    return target, "根据章节数、事实数和平台默认长度估算"


def _section_fact_ids(
    sections: list[tuple[str, str]],
    research_result: ResearchResult,
) -> dict[str, list[str]]:
    assignments = {section_id: [] for section_id, _ in sections}
    approved_by_id = {
        fact["fact_id"]: fact
        for fact in research_result.get("facts", [])
        if is_fact_approved(fact)
    }
    explicitly_assigned: set[str] = set()
    for section_id, title in sections:
        for fact_id in re.findall(r"\bF\d{3}\b", title):
            if fact_id in approved_by_id and fact_id not in explicitly_assigned:
                assignments[section_id].append(fact_id)
                explicitly_assigned.add(fact_id)

    unassigned: list[str] = []
    for fact in research_result.get("facts", []):
        if not is_fact_approved(fact):
            continue
        if fact["fact_id"] in explicitly_assigned:
            continue
        section_id = fact.get("section_id", "")
        if section_id in assignments:
            assignments[section_id].append(fact["fact_id"])
        else:
            unassigned.append(fact["fact_id"])
    for index, fact_id in enumerate(unassigned):
        section_id = sections[index % len(sections)][0]
        assignments[section_id].append(fact_id)
    return assignments


def _allocate_targets(total: int, weights: list[float]) -> list[int]:
    weight_sum = sum(weights) or 1.0
    raw = [total * weight / weight_sum for weight in weights]
    allocated = [int(value) for value in raw]
    remainder = total - sum(allocated)
    order = sorted(range(len(raw)), key=lambda index: raw[index] - allocated[index], reverse=True)
    for index in order[:remainder]:
        allocated[index] += 1
    return allocated


def _budget_for_target(target: int, tolerance: float) -> WordBudget:
    return {
        "target_chars": target,
        "min_chars": max(1, round(target * (1 - tolerance))),
        "max_chars": max(1, round(target * (1 + tolerance))),
    }


def build_generation_plan(
    outline: str,
    research_result: ResearchResult,
    user_input: str,
    target_platform: str,
    raw_content: dict[str, Any],
) -> GenerationPlan:
    """Build a deterministic adaptive plan whose section budgets sum to the total target."""
    parsed_sections = _parse_outline(outline)
    usable_facts = [fact for fact in research_result.get("facts", []) if is_fact_approved(fact)]
    target, target_reason = _extract_target_chars(
        user_input,
        target_platform,
        len(parsed_sections),
        len(usable_facts),
        raw_content,
    )
    assignments = _section_fact_ids(parsed_sections, research_result)
    fact_counts = [len(assignments[section_id]) for section_id, _ in parsed_sections]
    if target <= 1200:
        mode = "single_pass"
        reason = f"{target_reason}；目标篇幅较短，采用整篇生成以避免章节碎片化"
    elif len(parsed_sections) <= 2 or any(count >= 7 for count in fact_counts):
        mode = "hybrid"
        reason = f"{target_reason}；章节较少但内容较复杂，按章节生成"
    else:
        mode = "section_sequential"
        reason = f"{target_reason}；采用顺序分章生成以控制结构和字数"

    weights: list[float] = []
    for index, ((_, title), fact_count) in enumerate(zip(parsed_sections, fact_counts)):
        base = 1.0 + min(fact_count, 8) * 0.16
        if index == 0 and re.search(r"引言|导语|开头", title):
            base *= 0.65
        if index == len(parsed_sections) - 1 and re.search(r"总结|结语|展望", title):
            base *= 0.7
        weights.append(base)
    targets = _allocate_targets(target, weights)
    tolerance = 0.15 if target <= 1200 else 0.1
    sections: list[GenerationSection] = []
    for (section_id, title), section_target in zip(parsed_sections, targets):
        clean_title = re.sub(r"【事实[:：][^】]*】", "", title).strip()
        sections.append({
            "section_id": section_id,
            "title": clean_title,
            "purpose": f"围绕“{clean_title}”完成本节表达，并承接全文中心主题",
            "fact_ids": assignments[section_id],
            "budget": _budget_for_target(section_target, tolerance),
            "actual_chars": 0,
            "status": "pending",
            "content": "",
            "summary": "",
            "revision_attempts": 0,
        })
    total_budget = _budget_for_target(target, 0.08 if target <= 1200 else 0.05)
    return {
        "mode": mode,  # type: ignore[typeddict-item]
        "reason": reason,
        "target_chars": target,
        "min_chars": total_budget["min_chars"],
        "max_chars": total_budget["max_chars"],
        "counting_method": "中文字符、英文字母和数字；不计URL、Markdown符号及空白",
        "sections": sections,
    }


def _initial_memory(outline: str, raw_content: dict[str, Any], target: int) -> WritingMemory:
    thesis = str(raw_content.get("title") or next(
        (line.strip("# ") for line in outline.splitlines() if line.strip()),
        "",
    ))
    return {
        "thesis": thesis,
        "terminology": {},
        "used_fact_ids": [],
        "section_summaries": {},
        "open_threads": [],
        "previous_tail": "",
        "actual_chars": 0,
        "remaining_target_chars": target,
    }


async def planning_node(state: ReflectionState) -> dict[str, Any]:
    plan = build_generation_plan(
        state.get("outline", ""),
        state.get("research_result", {}),  # type: ignore[arg-type]
        state.get("user_input", ""),
        state.get("target_platform", "zhihu_article"),
        state.get("raw_content", {}),
    )
    return {
        "generation_plan": plan,
        "writing_memory": _initial_memory(
            state.get("outline", ""), state.get("raw_content", {}), plan["target_chars"]
        ),
        "section_reports": [],
        "status": "planned",
    }


async def generator_node(state: ReflectionState) -> dict[str, Any]:
    """Delegate drafting to the bounded observe-decide-act Generator Agent."""
    plan = state.get("generation_plan")
    if not plan:
        planned = await planning_node(state)
        plan = planned["generation_plan"]
        memory = planned["writing_memory"]
    else:
        memory = state.get("writing_memory") or _initial_memory(
            state.get("outline", ""), state.get("raw_content", {}), plan["target_chars"]
        )
    agent_result = await run_generator_agent(
        generation_plan=plan,
        writing_memory=memory,
        research_result=state.get("research_result", {}),  # type: ignore[arg-type]
        approved_outline=state.get("outline", ""),
        user_input=state.get("user_input", ""),
        platform_style=_platform_style(state.get("target_platform", "zhihu_article")),
        source_metadata=state.get("raw_content", {}),
        reference_pack=state.get("reference_pack") or ReferencePack.empty(),
    )
    draft = agent_result["draft"]
    plan = agent_result["generation_plan"]
    report = make_word_count_report(draft, plan)
    issues: list[ReflectionIssue] = []
    if agent_result["status"] != "generated":
        issues.append({
            "issue_id": "I001",
            "issue_type": "generator_agent_error",
            "severity": "high",
            "span": "",
            "fact_id": "",
            "instruction": "Generator Agent 未满足硬边界："
            + ("；".join(agent_result["errors"]) or agent_result["generator_stop_reason"]),
        })
    return {
        "generation_plan": plan,
        "writing_memory": agent_result["writing_memory"],
        "section_reports": agent_result["section_reports"],
        "word_count": report,
        "current_draft": draft,
        "generator_action_log": agent_result["generator_action_log"],
        "generator_tool_action_count": agent_result["generator_tool_action_count"],
        "generator_model_call_count": agent_result["generator_model_call_count"],
        "generator_stop_reason": agent_result["generator_stop_reason"],
        "issues": issues,
        "revision_attempts": 0,
        "status": agent_result["status"] if not issues else "needs_review",
    }


def _next_issue_id(issues: list[ReflectionIssue]) -> str:
    return f"I{len(issues) + 1:03d}"


def deterministic_checks(
    draft: str,
    research_result: ResearchResult,
    raw_content: dict[str, Any],
    word_count: WordCountReport | None = None,
) -> tuple[list[ReflectionIssue], list[str]]:
    """Run deterministic grounding and length checks before the LLM critic."""
    issues: list[ReflectionIssue] = []
    log: list[str] = []
    if not draft.strip():
        issues.append({
            "issue_id": _next_issue_id(issues),
            "issue_type": "empty_draft",
            "severity": "high",
            "span": "",
            "fact_id": "",
            "instruction": "重新生成完整草稿",
        })
        return issues, ["draft is empty"]
    verified_facts = [
        fact for fact in research_result.get("facts", []) if is_fact_approved(fact)
    ]
    if not verified_facts:
        issues.append({
            "issue_id": _next_issue_id(issues),
            "issue_type": "no_verified_facts",
            "severity": "high",
            "span": "",
            "fact_id": "",
            "instruction": "事实清单没有已验证事实，草稿必须进入人工复核",
        })
    allowed_text = "\n".join(
        [str(raw_content.get("text", ""))] + [fact["claim"] for fact in verified_facts]
    )
    draft_numbers = {
        value for value in re.findall(r"\d+(?:\.\d+)?%?", draft)
        if len(value.rstrip("%")) >= 2 or value.endswith("%")
    }
    allowed_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", allowed_text))
    unsupported_numbers = sorted(draft_numbers - allowed_numbers)
    for value in unsupported_numbers[:10]:
        issues.append({
            "issue_id": _next_issue_id(issues),
            "issue_type": "unsupported_number",
            "severity": "high",
            "span": value,
            "fact_id": "",
            "instruction": f"删除数字 {value}，或补充能直接支持它的证据",
        })
    if word_count and word_count["status"] != "within_budget":
        issues.append({
            "issue_id": _next_issue_id(issues),
            "issue_type": f"word_count_{word_count['status']}",
            "severity": "high",
            "span": str(word_count["actual_chars"]),
            "fact_id": "",
            "instruction": (
                f"将全文调整到 {word_count['min_chars']}～{word_count['max_chars']} 字，"
                "保持事实、大纲和章节过渡不变"
            ),
        })
    log.extend([
        f"verified_facts={len(verified_facts)}",
        f"unsupported_numbers={len(unsupported_numbers)}",
        f"research_status={research_result.get('status', 'unknown')}",
    ])
    if word_count:
        log.append(
            f"word_count={word_count['actual_chars']}/{word_count['target_chars']}:{word_count['status']}"
        )
    return issues, log


def _normalize_scores(value: Any) -> QualityScores:
    raw = value if isinstance(value, dict) else {}
    return {
        key: round(max(0.0, min(1.0, float(raw.get(key, 0.0)))), 3)
        for key in ("grounding", "completeness", "structure", "style")
    }  # type: ignore[return-value]


def _normalize_issues(value: Any, start: int = 0) -> list[ReflectionIssue]:
    issues: list[ReflectionIssue] = []
    if not isinstance(value, list):
        return issues
    for index, item in enumerate(value, start + 1):
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity") or "medium")
        if severity not in {"low", "medium", "high"}:
            severity = "medium"
        issues.append({
            "issue_id": f"I{index:03d}",
            "issue_type": str(item.get("issue_type") or "quality_issue"),
            "severity": severity,  # type: ignore[typeddict-item]
            "span": str(item.get("span") or "")[:300],
            "fact_id": str(item.get("fact_id") or ""),
            "instruction": str(item.get("instruction") or "请修正该问题"),
        })
    return issues


def _reindex_issues(issues: list[ReflectionIssue]) -> list[ReflectionIssue]:
    for index, issue in enumerate(issues, 1):
        issue["issue_id"] = f"I{index:03d}"
    return issues


def _section_report_issues(
    section_reports: list[GenerationSection],
    start: int,
) -> list[ReflectionIssue]:
    issues: list[ReflectionIssue] = []
    for section in section_reports:
        if section["status"] == "within_budget":
            continue
        if section["status"] == "failed":
            instruction = "恢复缺失章节，并依据该章节事实和预算重新生成"
        else:
            instruction = (
                f"将本章调整到 {section['budget']['min_chars']}～"
                f"{section['budget']['max_chars']} 字，不改变事实边界"
            )
        issues.append({
            "issue_id": f"I{start + len(issues) + 1:03d}",
            "issue_type": f"section_{section['status']}",
            "severity": "high",
            "span": section["title"],
            "fact_id": "",
            "instruction": instruction,
        })
    return issues


async def critic_node(state: ReflectionState) -> dict[str, Any]:
    """Evaluate grounding, completeness, cross-section consistency, structure, and style."""
    draft = state.get("current_draft", "")
    research_result = state.get("research_result", {})
    deterministic, verification_log = deterministic_checks(
        draft,
        research_result,  # type: ignore[arg-type]
        state.get("raw_content", {}),
        state.get("word_count"),
    )
    existing = list(state.get("issues", []))
    generator_blocked = any(
        issue.get("issue_type") == "generator_agent_error" for issue in existing
    )
    existing += _section_report_issues(
        state.get("section_reports", []), len(existing)
    )
    reference_pack = state.get("reference_pack") or ReferencePack.empty()
    for phrase in reference_pack.prohibited_phrases:
        if phrase and phrase in draft:
            existing.append({
                "issue_id": f"I{len(existing) + 1:03d}",
                "issue_type": "reference_copy_risk",
                "severity": "medium",
                "span": phrase,
                "fact_id": "",
                "instruction": "删除参考文章中的模板化原句，按本段具体事实重新表达",
            })
    try:
        extracted_facts = await extract_draft_facts(
            draft,
            research_result,  # type: ignore[arg-type]
            state.get("section_reports", []),
        )
        draft_facts = match_draft_facts(extracted_facts, research_result)  # type: ignore[arg-type]
        fact_match_summary = summarize_fact_matches(draft_facts)
        existing += _fact_match_issues(
            draft_facts,
            research_result,  # type: ignore[arg-type]
            len(existing),
        )
    except Exception as exc:
        draft_facts = []
        fact_match_summary = summarize_fact_matches(draft_facts)
        existing.append({
            "issue_id": f"I{len(existing) + 1:03d}",
            "issue_type": "fact_extraction_error",
            "severity": "high",
            "span": "",
            "fact_id": "",
            "instruction": f"文章事实抽取或匹配失败，禁止自动通过：{exc}",
        })
    facts = format_verified_facts(research_result)  # type: ignore[arg-type]
    plan = state.get("generation_plan", {})
    plan_summary = "\n".join(
        f"{item['section_id']} {item['title']} target={item['budget']['target_chars']} "
        f"actual={item['actual_chars']} facts={','.join(item['fact_ids']) or 'none'}"
        for item in plan.get("sections", [])
    )
    prompt = f"""你是全文质量审查器。依据事实注册表和批准大纲审查文章，不能凭常识补证据。

## 事实注册表
{facts}

## 用户批准的大纲
{state.get('outline', '')}

## 章节生成报告
{plan_summary}

## 完整草稿
{draft}

## 文章原子事实及匹配结果
{json.dumps(draft_facts, ensure_ascii=False, indent=2)}

## 事实匹配汇总
{json.dumps(fact_match_summary, ensure_ascii=False)}

## 用户要求与平台风格
{state.get('user_input', '')}
{_platform_style(state.get('target_platform', 'zhihu_article'))}

## 参考文章库约束
{reference_pack.prompt_context()}

重点检查：事实依据、章节覆盖、前后称谓与结论一致、相邻章节承接、跨章节重复、整体风格、
是否符合结构角色和节奏，以及是否出现照搬参考文章或模板套话的风险。
只输出 JSON：
{{
  "scores": {{"grounding":0.0,"completeness":0.0,"structure":0.0,"style":0.0}},
  "issues": [{{
    "issue_type":"unsupported_claim|missing_fact|contradiction|repetition|transition|structure|style",
    "severity":"low|medium|high",
    "span":"原文片段或章节名",
    "fact_id":"F001或空字符串",
    "instruction":"可执行的局部修改要求"
  }}]
}}
"""
    try:
        payload = _parse_json_object(await LLMClient().chat_with_retry(prompt))
        scores = _normalize_scores(payload.get("scores"))
        issues = existing + deterministic
        issues += _normalize_issues(payload.get("issues"), len(issues))
        issues = _reindex_issues(issues)
        if fact_match_summary["contradicted"] or fact_match_summary["unsupported"]:
            scores["grounding"] = min(scores["grounding"], 0.59)
        elif fact_match_summary["partial"]:
            scores["grounding"] = min(scores["grounding"], 0.79)
        has_high = any(issue["severity"] == "high" for issue in issues)
        approved = not generator_blocked and not has_high and all(
            scores[key] >= threshold for key, threshold in PASS_THRESHOLDS.items()
        )
        return {
            "scores": scores,
            "issues": issues,
            "draft_facts": draft_facts,
            "fact_match_summary": fact_match_summary,
            "verification_log": verification_log,
            "approved": approved,
            "status": (
                "approved" if approved
                else "needs_review" if generator_blocked
                else "needs_revision"
            ),
        }
    except Exception as exc:
        issues = existing + deterministic
        issues.append({
            "issue_id": f"I{len(issues) + 1:03d}",
            "issue_type": "critic_error",
            "severity": "high",
            "span": "",
            "fact_id": "",
            "instruction": f"质量审查失败，禁止自动通过：{exc}",
        })
        issues = _reindex_issues(issues)
        return {
            "scores": _normalize_scores({}),
            "issues": issues,
            "draft_facts": draft_facts,
            "fact_match_summary": fact_match_summary,
            "verification_log": verification_log + [f"critic_error={exc}"],
            "approved": False,
            "status": "needs_review",
        }


async def reviser_node(state: ReflectionState) -> dict[str, Any]:
    """Delegate Critic issues to the bounded Revision Agent."""
    attempts = state.get("revision_attempts", 0) + 1
    try:
        agent_result = await run_revision_agent(
            draft=state.get("current_draft", ""),
            research_result=state.get("research_result", {}),  # type: ignore[arg-type]
            raw_content=state.get("raw_content", {}),
            generation_plan=state.get("generation_plan", _empty_plan()),
            section_reports=state.get("section_reports", []),
            writing_memory=state.get("writing_memory", _empty_memory()),
            source="critic",
            revision_cycle=attempts,
            critic_issues=state.get("issues", []),
        )
        revised = agent_result["draft"]
        plan = agent_result["generation_plan"]
        report = make_word_count_report(revised, plan)
        blocking_issues: list[ReflectionIssue] = []
        if agent_result["status"] in {"blocked", "needs_review"}:
            blocking_issues.append({
                "issue_id": "I001",
                "issue_type": "revision_agent_error",
                "severity": "high",
                "span": "",
                "fact_id": "",
                "instruction": "Revision Agent 未能安全完成修改："
                + "；".join(agent_result["errors"] or agent_result["unresolved_issues"]),
            })
        return {
            "current_draft": revised,
            "word_count": report,
            "generation_plan": plan,
            "section_reports": agent_result["section_reports"],
            "writing_memory": agent_result["writing_memory"],
            "revision_plan": agent_result["revision_plan"],
            "revision_action_log": state.get("revision_action_log", [])
            + agent_result["revision_action_log"],
            "issue_resolution_log": state.get("issue_resolution_log", [])
            + agent_result["issue_resolution_log"],
            "revision_cycles": attempts,
            "tool_action_count": state.get("tool_action_count", 0)
            + agent_result["tool_action_count"],
            "unresolved_issues": agent_result["unresolved_issues"],
            "issues": blocking_issues,
            "revision_attempts": attempts,
            "status": "revised" if not blocking_issues else "needs_review",
        }
    except Exception as exc:
        issues = [{
            "issue_id": "I001",
            "issue_type": "revision_agent_error",
            "severity": "high",
            "span": "",
            "fact_id": "",
            "instruction": f"Revision Agent 执行失败，需要人工处理：{exc}",
        }]
        return {
            "revision_attempts": attempts,
            "revision_cycles": attempts,
            "issues": issues,
            "approved": False,
            "status": "needs_review",
        }


def route_after_critic(state: ReflectionState) -> str:
    if state.get("approved"):
        return "approved"
    if state.get("status") == "needs_review":
        return "needs_review"
    if state.get("revision_attempts", 0) >= MAX_REFLECTION_REVISIONS:
        return "max_revisions"
    return "reviser"


def build_reflection_graph() -> StateGraph:
    graph = StateGraph(ReflectionState)
    graph.add_node("planning", planning_node)
    graph.add_node("generator", generator_node)
    graph.add_node("critic", critic_node)
    graph.add_node("reviser", reviser_node)
    graph.add_edge(START, "planning")
    graph.add_edge("planning", "generator")
    graph.add_edge("generator", "critic")
    graph.add_conditional_edges(
        "critic",
        route_after_critic,
        {
            "approved": END,
            "needs_review": END,
            "max_revisions": END,
            "reviser": "reviser",
        },
    )
    graph.add_edge("reviser", "critic")
    return graph


def _empty_plan() -> GenerationPlan:
    return {
        "mode": "single_pass",
        "reason": "流程失败，未生成计划",
        "target_chars": 0,
        "min_chars": 0,
        "max_chars": 0,
        "counting_method": "中文字符、英文字母和数字",
        "sections": [],
    }


def _empty_memory() -> WritingMemory:
    return {
        "thesis": "",
        "terminology": {},
        "used_fact_ids": [],
        "section_summaries": {},
        "open_threads": [],
        "previous_tail": "",
        "actual_chars": 0,
        "remaining_target_chars": 0,
    }


def _empty_revision_plan() -> RevisionPlan:
    return {
        "revision_cycle": 0,
        "source": "critic",
        "tasks": [],
        "max_actions": 8,
    }


async def run_reflection_writer(
    research_result: ResearchResult,
    outline: str,
    raw_content: dict[str, Any],
    target_platform: str = "zhihu_article",
    user_input: str = "",
    reference_pack: ReferencePack | None = None,
) -> ReflectionResult:
    """Plan, generate adaptively, inspect, and revise without silently approving failures."""
    initial: ReflectionState = {
        "research_result": research_result,
        "outline": outline,
        "raw_content": raw_content,
        "target_platform": target_platform,
        "user_input": user_input,
        "reference_pack": reference_pack or ReferencePack.empty(),
        "revision_attempts": 0,
        "approved": False,
    }
    try:
        result = await build_reflection_graph().compile().ainvoke(initial)
        approved = bool(result.get("approved"))
        attempts = int(result.get("revision_attempts", 0))
        internal_status = result.get("status", "needs_review")
        if approved:
            status = "approved"
        elif internal_status == "needs_review":
            status = "needs_review"
        elif attempts >= MAX_REFLECTION_REVISIONS:
            status = "max_revisions"
        else:
            status = "needs_review"
        plan = result.get("generation_plan", _empty_plan())
        draft = result.get("current_draft", "")
        resolutions, unresolved = reconcile_issue_resolutions(
            result.get("issue_resolution_log", []),
            result.get("issues", []),
            approved,
        )
        word_count = result.get("word_count") or _word_count_report(
            count_visible_chars(draft),
            max(1, plan["target_chars"]),
            plan["min_chars"],
            plan["max_chars"],
        )
        return {
            "status": status,  # type: ignore[typeddict-item]
            "draft": draft,
            "approved": approved,
            "revision_attempts": attempts,
            "scores": result.get("scores", _normalize_scores({})),
            "issues": result.get("issues", []),
            "draft_facts": result.get("draft_facts", []),
            "fact_match_summary": result.get(
                "fact_match_summary", summarize_fact_matches([])
            ),
            "verification_log": result.get("verification_log", []),
            "generation_plan": plan,
            "writing_memory": result.get("writing_memory", _empty_memory()),
            "section_reports": result.get("section_reports", []),
            "word_count": word_count,
            "generator_action_log": result.get("generator_action_log", []),
            "generator_tool_action_count": int(result.get("generator_tool_action_count", 0)),
            "generator_model_call_count": int(result.get("generator_model_call_count", 0)),
            "generator_stop_reason": str(result.get("generator_stop_reason", "")),
            "revision_plan": result.get("revision_plan", _empty_revision_plan()),
            "revision_action_log": result.get("revision_action_log", []),
            "issue_resolution_log": resolutions,
            "revision_cycles": int(result.get("revision_cycles", attempts)),
            "tool_action_count": int(result.get("tool_action_count", 0)),
            "unresolved_issues": sorted(set(result.get("unresolved_issues", [])) | set(unresolved)),
            "reference_pack": (reference_pack or ReferencePack.empty()).to_dict(),
        }
    except Exception as exc:
        draft = str(raw_content.get("text", ""))
        return {
            "status": "failed",
            "draft": draft,
            "approved": False,
            "revision_attempts": 0,
            "scores": _normalize_scores({}),
            "issues": [{
                "issue_id": "I001",
                "issue_type": "reflection_pipeline_error",
                "severity": "high",
                "span": "",
                "fact_id": "",
                "instruction": f"Reflection 流程失败：{exc}",
            }],
            "draft_facts": [],
            "fact_match_summary": summarize_fact_matches([]),
            "verification_log": [f"pipeline_error={exc}"],
            "generation_plan": _empty_plan(),
            "writing_memory": _empty_memory(),
            "section_reports": [],
            "word_count": _word_count_report(count_visible_chars(draft), 1, 0, 1),
            "generator_action_log": [],
            "generator_tool_action_count": 0,
            "generator_model_call_count": 0,
            "generator_stop_reason": "reflection_pipeline_error",
            "revision_plan": _empty_revision_plan(),
            "revision_action_log": [],
            "issue_resolution_log": [],
            "revision_cycles": 0,
            "tool_action_count": 0,
            "unresolved_issues": [],
            "reference_pack": (reference_pack or ReferencePack.empty()).to_dict(),
        }


__all__ = [
    "ReflectionState",
    "build_generation_plan",
    "build_reflection_graph",
    "count_visible_chars",
    "critic_node",
    "deterministic_checks",
    "extract_draft_facts",
    "generator_node",
    "match_draft_facts",
    "make_word_count_report",
    "planning_node",
    "reviser_node",
    "run_reflection_writer",
    "summarize_fact_matches",
]
