"""Bounded Revision Agent shared by Critic-driven and user-driven edits."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Literal, TypedDict

from forge.agents.research_types import (
    GenerationPlan,
    GenerationSection,
    IssueResolution,
    ReflectionIssue,
    ResearchResult,
    RevisionAction,
    RevisionPlan,
    RevisionTask,
    TuningIntent,
    WritingMemory,
    format_verified_facts,
    is_fact_approved,
)
from forge.tools.llm_client import LLMClient


MAX_REVISION_ACTIONS = 8
ALLOWED_TOOLS = {
    "rewrite_section",
    "replace_unsupported_claim",
    "fix_fact",
    "add_transition",
    "remove_repetition",
    "expand_section",
    "shrink_section",
    "adjust_tone",
    "count_words",
    "check_outline_coverage",
    "validate_fact_usage",
}
MUTATING_TOOLS = ALLOWED_TOOLS - {
    "count_words", "check_outline_coverage", "validate_fact_usage"
}
TOOL_ORDER = {
    "replace_unsupported_claim": 1,
    "fix_fact": 1,
    "rewrite_section": 2,
    "remove_repetition": 3,
    "add_transition": 3,
    "expand_section": 4,
    "shrink_section": 4,
    "adjust_tone": 5,
}


class RevisionAgentResult(TypedDict):
    status: Literal["revised", "needs_review", "blocked"]
    draft: str
    generation_plan: GenerationPlan
    section_reports: list[GenerationSection]
    writing_memory: WritingMemory
    revision_plan: RevisionPlan
    revision_action_log: list[RevisionAction]
    issue_resolution_log: list[IssueResolution]
    tool_action_count: int
    unresolved_issues: list[str]
    errors: list[str]


def _parse_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("response does not contain a JSON object")
        candidate = candidate[start:end + 1]
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def _count_visible_chars(text: str) -> int:
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[`#*_>\-]", "", text)
    return len(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", text))


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _issue_signature(issue: dict[str, Any]) -> str:
    value = "|".join(
        str(issue.get(key, "")) for key in ("issue_type", "span", "fact_id")
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _fact_tokens_preserved(before: str, after: str, facts: str) -> tuple[bool, list[str]]:
    pattern = r"\d{1,4}(?:\.\d+)?%?|\d{4}年|\d{1,2}月\d{1,2}日"
    required = set(re.findall(pattern, before)) & set(re.findall(pattern, facts))
    missing = sorted(required - set(re.findall(pattern, after)))
    return not missing, missing


def _section_hashes(sections: list[GenerationSection]) -> dict[str, str]:
    return {section["section_id"]: _content_hash(section["content"]) for section in sections}


def _fact_map(result: ResearchResult) -> dict[str, dict[str, Any]]:
    return {fact["fact_id"]: fact for fact in result.get("facts", [])}


def _facts_for_ids(result: ResearchResult, fact_ids: list[str]) -> str:
    facts = [
        fact for fact in result.get("facts", [])
        if fact["fact_id"] in fact_ids and is_fact_approved(fact)
    ]
    selected = {**result, "facts": facts}
    return format_verified_facts(selected)  # type: ignore[arg-type]


def _render_draft(plan: GenerationPlan, sections: list[GenerationSection]) -> str:
    if plan.get("mode") == "single_pass" or len(sections) == 1:
        return sections[0]["content"] if sections else ""
    return "\n\n".join(
        f"## {section['title']}\n\n{section['content']}"
        for section in sections if section["content"]
    )


def _sync_sections_from_draft(
    draft: str,
    plan: GenerationPlan,
    sections: list[GenerationSection],
) -> None:
    if not sections:
        return
    if plan.get("mode") == "single_pass" or len(sections) == 1:
        sections[0]["content"] = draft
        sections[0]["actual_chars"] = _count_visible_chars(draft)
        sections[0]["summary"] = re.sub(r"\s+", " ", draft).strip()[:180]
        return
    chunks = re.split(r"(?m)^##\s+(.+?)\s*$", draft)
    by_title = {
        chunks[index].strip(): chunks[index + 1].strip()
        for index in range(1, len(chunks) - 1, 2)
    }
    for section in sections:
        if section["title"] in by_title:
            section["content"] = by_title[section["title"]]
            section["actual_chars"] = _count_visible_chars(section["content"])
            section["summary"] = re.sub(r"\s+", " ", section["content"]).strip()[:180]


def _refresh_section(section: GenerationSection) -> None:
    actual = _count_visible_chars(section["content"])
    budget = section["budget"]
    if actual < budget["min_chars"]:
        status = "too_short"
    elif actual > budget["max_chars"]:
        status = "too_long"
    else:
        status = "within_budget"
    section.update({
        "actual_chars": actual,
        "status": status,
        "summary": re.sub(r"\s+", " ", section["content"]).strip()[:180],
    })


def _refresh_memory(
    previous: WritingMemory,
    sections: list[GenerationSection],
    target: int,
) -> WritingMemory:
    actual = sum(section["actual_chars"] for section in sections)
    return {
        **previous,
        "used_fact_ids": list(dict.fromkeys(
            fact_id for section in sections for fact_id in section["fact_ids"]
        )),
        "section_summaries": {
            section["section_id"]: section["summary"] for section in sections
        },
        "open_threads": [],
        "previous_tail": sections[-1]["content"][-400:] if sections else "",
        "actual_chars": actual,
        "remaining_target_chars": max(0, target - actual),
    }


def _resolve_section_ids(
    references: list[str],
    sections: list[GenerationSection],
) -> list[str]:
    resolved: list[str] = []
    for reference in references:
        text = str(reference).strip()
        if not text:
            continue
        if text in {"全文", "全部", "all"}:
            return [section["section_id"] for section in sections]
        for section in sections:
            if text == section["section_id"] or text in section["title"] or section["title"] in text:
                resolved.append(section["section_id"])
        ordinal_match = re.search(r"第\s*([一二三四五六七八九十\d]+)\s*(?:部分|章|节)", text)
        if ordinal_match:
            token = ordinal_match.group(1)
            chinese = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                       "七": 7, "八": 8, "九": 9, "十": 10}
            index = int(token) if token.isdigit() else chinese.get(token, 0)
            if 1 <= index <= len(sections):
                resolved.append(sections[index - 1]["section_id"])
    return list(dict.fromkeys(resolved))


def _infer_operations(requirements: list[str]) -> list[str]:
    text = " ".join(requirements)
    operations: list[str] = []
    if re.search(r"无来源|没有证据|删除.*数据", text):
        operations.append("replace_unsupported_claim")
    if re.search(r"事实|数字|日期|错误|纠正", text):
        operations.append("fix_fact")
    if re.search(r"过渡|衔接|跳跃", text):
        operations.append("add_transition")
    if re.search(r"重复|去重", text):
        operations.append("remove_repetition")
    if re.search(r"扩写|详细|太短|补充", text):
        operations.append("expand_section")
    if re.search(r"压缩|精简|缩短|太长|字以内", text):
        operations.append("shrink_section")
    if re.search(r"语气|风格|口语|专业|技术化|公众号", text):
        operations.append("adjust_tone")
    if not operations:
        operations.append("rewrite_section")
    return list(dict.fromkeys(operations))


def _target_chars(text: str) -> int | None:
    match = re.search(r"(\d{2,5})\s*(?:字|字符)", text)
    return int(match.group(1)) if match else None


def _empty_constraints() -> dict[str, Any]:
    return {
        "target_chars": None,
        "tolerance": 0.1,
        "target_tone": "",
        "preserve_facts": True,
        "preserve_other_sections": True,
    }


def adapt_critic_issues(
    issues: list[ReflectionIssue],
    research_result: ResearchResult,
    sections: list[GenerationSection],
) -> list[RevisionTask]:
    """Normalize Critic issues into the same tasks used for user edits."""
    fact_by_id = _fact_map(research_result)
    operation_map = {
        "unsupported_claim": "replace_unsupported_claim",
        "unsupported_number": "replace_unsupported_claim",
        "contradiction": "fix_fact",
        "missing_fact": "fix_fact",
        "repetition": "remove_repetition",
        "transition": "add_transition",
        "word_count_too_short": "expand_section",
        "word_count_too_long": "shrink_section",
        "section_too_short": "expand_section",
        "section_too_long": "shrink_section",
        "style": "adjust_tone",
        "tone": "adjust_tone",
        "structure": "rewrite_section",
    }
    priority_map = {
        "unsupported_claim": 1, "unsupported_number": 1, "contradiction": 1,
        "missing_fact": 2, "structure": 2, "repetition": 3, "transition": 3,
        "word_count_too_short": 4, "word_count_too_long": 4,
        "section_too_short": 4, "section_too_long": 4, "style": 5, "tone": 5,
    }
    tasks: list[RevisionTask] = []
    for index, issue in enumerate(issues, 1):
        issue_type = issue["issue_type"]
        fact_id = issue.get("fact_id", "")
        target_sections: list[str] = []
        if issue_type == "word_count_too_short":
            target_sections = [
                section["section_id"] for section in sections
                if section["status"] == "too_short"
            ] or [section["section_id"] for section in sections]
        elif issue_type == "word_count_too_long":
            target_sections = [
                section["section_id"] for section in sections
                if section["status"] == "too_long"
            ] or [section["section_id"] for section in sections]
        if fact_id in fact_by_id:
            section_id = str(fact_by_id[fact_id].get("section_id", ""))
            if any(section["section_id"] == section_id for section in sections):
                target_sections.append(section_id)
        target_sections += _resolve_section_ids([issue.get("span", "")], sections)
        if not target_sections and issue.get("span"):
            target_sections += [
                section["section_id"] for section in sections
                if issue["span"] in section["content"]
            ]
        if not target_sections:
            target_sections = [sections[0]["section_id"]] if sections else []
        constraints = _empty_constraints()
        operation = operation_map.get(issue_type, "rewrite_section")
        if issue_type.startswith("section_") and len(target_sections) == 1:
            target_section = next(
                (section for section in sections if section["section_id"] == target_sections[0]),
                None,
            )
            if target_section:
                constraints["target_chars"] = target_section["budget"]["target_chars"]
        tasks.append({
            "task_id": f"R{index:03d}",
            "source": "critic",
            "source_issue_ids": [issue["issue_id"]],
            "operation_hints": [operation],
            "target_section_ids": list(dict.fromkeys(target_sections)),
            "target_fact_ids": [fact_id] if fact_id else [],
            "requirements": [issue["instruction"]],
            "constraints": constraints,  # type: ignore[typeddict-item]
            "priority": priority_map.get(issue_type, 3),
            "status": "pending",
            "validation_rules": ["scope_boundary", "fact_usage", "critic"],
        })
    return sorted(tasks, key=lambda item: (item["priority"], item["task_id"]))


async def parse_tuning_intent(
    request: str,
    generation_plan: GenerationPlan,
    research_result: ResearchResult,
) -> TuningIntent:
    """Convert natural-language tuning into bounded structured tasks without editing."""
    sections = generation_plan.get("sections", [])
    section_text = "\n".join(
        f"{item['section_id']}: {item['title']} ({item['actual_chars']}字)"
        for item in sections
    )
    fact_text = "\n".join(
        f"{fact['fact_id']}: {fact['claim']}" for fact in research_result.get("facts", [])
        if is_fact_approved(fact)
    )
    prompt = f"""你是文章微调意图解析器，只负责理解，不修改文章。

用户请求：{request}

章节：
{section_text}

事实：
{fact_text}

可用操作：{', '.join(sorted(MUTATING_TOOLS))}

只输出 JSON：
{{
  "intent":"edit|question|clarify",
  "question":"仅question填写",
  "clarification_question":"仅clarify填写",
  "reason":"判断理由",
  "tasks":[{{
    "target_sections":["S02或第二部分"],
    "target_fact_ids":["F001"],
    "operation_hints":["rewrite_section","shrink_section","adjust_tone"],
    "requirements":["明确可执行要求"],
    "target_chars":500,
    "target_tone":"公众号",
    "preserve_facts":true,
    "preserve_other_sections":true
  }}]
}}

规则：只是询问来源或内容时用 question；修改目标无法定位或要求不明确时用 clarify；禁止自行补充用户没有提出的修改。
"""
    try:
        payload = _parse_json_object(await LLMClient().chat_with_retry(prompt))
    except Exception as exc:
        if re.search(r"[？?]|为什么|哪里|什么|如何|是否|来源", request):
            return {
                "intent": "question", "original_request": request, "tasks": [],
                "question": request, "clarification_question": "", "reason": f"解析器降级：{exc}",
            }
        return {
            "intent": "clarify", "original_request": request, "tasks": [], "question": "",
            "clarification_question": "请明确要修改的章节，以及希望调整事实、结构、字数还是语气。",
            "reason": f"意图解析失败，为避免误改文章而请求澄清：{exc}",
        }

    intent = str(payload.get("intent") or "clarify")
    if intent not in {"edit", "question", "clarify"}:
        intent = "clarify"
    tasks: list[RevisionTask] = []
    if intent == "edit":
        for index, raw in enumerate(payload.get("tasks", [])[:8], 1):
            if not isinstance(raw, dict):
                continue
            requirements = [str(item).strip() for item in raw.get("requirements", []) if str(item).strip()]
            references = [str(item) for item in raw.get("target_sections", [])]
            target_sections = _resolve_section_ids(references, sections)
            if not target_sections:
                continue
            operations = [
                str(item) for item in raw.get("operation_hints", [])
                if str(item) in MUTATING_TOOLS
            ] or _infer_operations(requirements)
            constraints = _empty_constraints()
            constraints.update({
                "target_chars": raw.get("target_chars") or _target_chars(" ".join(requirements)),
                "target_tone": str(raw.get("target_tone") or ""),
                "preserve_facts": bool(raw.get("preserve_facts", True)),
                "preserve_other_sections": bool(raw.get("preserve_other_sections", True)),
            })
            tasks.append({
                "task_id": f"R{index:03d}", "source": "user", "source_issue_ids": [],
                "operation_hints": list(dict.fromkeys(operations)),
                "target_section_ids": target_sections,
                "target_fact_ids": [str(item) for item in raw.get("target_fact_ids", [])],
                "requirements": requirements or [request],
                "constraints": constraints,  # type: ignore[typeddict-item]
                "priority": 3, "status": "pending",
                "validation_rules": ["scope_boundary", "fact_usage", "word_count", "critic"],
            })
        if not tasks:
            intent = "clarify"
    return {
        "intent": intent,  # type: ignore[typeddict-item]
        "original_request": request,
        "tasks": tasks,
        "question": str(payload.get("question") or request if intent == "question" else ""),
        "clarification_question": str(
            payload.get("clarification_question")
            or "请明确要修改的章节和具体要求。" if intent == "clarify" else ""
        ),
        "reason": str(payload.get("reason") or ""),
    }


async def answer_tuning_question(question: str, research_result: ResearchResult) -> str:
    facts = format_verified_facts(research_result)
    prompt = f"""依据事实注册表回答用户问题。回答涉及事实时必须给出 source、title、url 和 evidence_id；没有证据时明确说明。

问题：{question}

事实注册表：
{facts}
"""
    try:
        return await LLMClient().chat_with_retry(prompt)
    except Exception:
        return f"无法调用问答模型。可核验事实如下：\n{facts}"


async def _rewrite_with_instruction(
    content: str,
    instruction: str,
    facts: str,
    context: str,
    budget: dict[str, int],
) -> str:
    prompt = f"""只修改给定章节，不输出标题或说明。

章节正文：
{content}

修改要求：
{instruction}

可用事实：
{facts}

上下文：
{context}

字数预算：目标 {budget['target_chars']} 字，允许 {budget['min_chars']}～{budget['max_chars']} 字。

禁止修改事实含义，禁止增加没有来源的数字、日期、人物、机构或案例。只输出修改后的章节正文。
"""
    return await LLMClient().chat_with_retry(prompt)


async def rewrite_section(content: str, requirements: list[str], facts: str, context: str, budget: dict[str, int]) -> str:
    return await _rewrite_with_instruction(content, "；".join(requirements), facts, context, budget)


async def replace_unsupported_claim(content: str, requirements: list[str], facts: str, context: str, budget: dict[str, int]) -> str:
    instruction = "删除或用可用事实替换无证据陈述，不确定时直接删除。" + "；".join(requirements)
    return await _rewrite_with_instruction(content, instruction, facts, context, budget)


async def fix_fact(content: str, requirements: list[str], facts: str, context: str, budget: dict[str, int]) -> str:
    instruction = "严格依据 Fact ID 修正事实，保留来源支持的限定条件。" + "；".join(requirements)
    return await _rewrite_with_instruction(content, instruction, facts, context, budget)


async def add_transition(content: str, requirements: list[str], facts: str, context: str, budget: dict[str, int]) -> str:
    instruction = "只调整本章开头或结尾以修复章节过渡，主体正确内容保持不变。" + "；".join(requirements)
    return await _rewrite_with_instruction(content, instruction, facts, context, budget)


async def remove_repetition(content: str, requirements: list[str], facts: str, context: str, budget: dict[str, int]) -> str:
    instruction = "删除与其他章节重复的表达，保留本章独有事实和必要承接。" + "；".join(requirements)
    return await _rewrite_with_instruction(content, instruction, facts, context, budget)


async def expand_section(content: str, requirements: list[str], facts: str, context: str, budget: dict[str, int]) -> str:
    instruction = "通过解释已有事实之间的关系扩写，禁止重复凑字数或创造新事实。" + "；".join(requirements)
    return await _rewrite_with_instruction(content, instruction, facts, context, budget)


async def shrink_section(content: str, requirements: list[str], facts: str, context: str, budget: dict[str, int]) -> str:
    instruction = "删除重复、冗余修饰和次要解释，必须保留事实、限定条件与过渡。" + "；".join(requirements)
    return await _rewrite_with_instruction(content, instruction, facts, context, budget)


async def adjust_tone(content: str, requirements: list[str], facts: str, context: str, budget: dict[str, int]) -> str:
    instruction = "只调整表达语气和平台风格，不改变数字、日期、机构、人物、事实和结论。" + "；".join(requirements)
    return await _rewrite_with_instruction(content, instruction, facts, context, budget)


def count_words(draft: str, sections: list[GenerationSection], target: int) -> dict[str, Any]:
    actual = _count_visible_chars(draft)
    return {
        "target_chars": target,
        "actual_chars": actual,
        "deviation": round((actual - target) / target, 3) if target else 0.0,
        "sections": {
            section["section_id"]: {
                "target_chars": section["budget"]["target_chars"],
                "actual_chars": _count_visible_chars(section["content"]),
            }
            for section in sections
        },
    }


def check_outline_coverage(sections: list[GenerationSection]) -> dict[str, Any]:
    missing = [section["section_id"] for section in sections if not section["content"].strip()]
    return {"valid": not missing, "missing_section_ids": missing}


def validate_fact_usage(
    draft: str,
    research_result: ResearchResult,
    raw_content: dict[str, Any],
) -> dict[str, Any]:
    """Deterministically validate numeric/date tokens; semantic entities remain for Critic."""
    allowed = str(raw_content.get("text", "")) + "\n" + "\n".join(
        fact["claim"] for fact in research_result.get("facts", []) if is_fact_approved(fact)
    )
    pattern = r"\d{1,4}(?:\.\d+)?%?|\d{4}年|\d{1,2}月\d{1,2}日"
    draft_tokens = set(re.findall(pattern, draft))
    allowed_tokens = set(re.findall(pattern, allowed))
    unsupported = sorted(draft_tokens - allowed_tokens)
    return {
        "valid": not unsupported,
        "unsupported_tokens": unsupported,
        "semantic_entity_review": "delegated_to_critic",
    }


def reconcile_issue_resolutions(
    resolutions: list[IssueResolution],
    critic_issues: list[ReflectionIssue],
    approved: bool,
) -> tuple[list[IssueResolution], list[str]]:
    """Reconcile provisional tool validation with the authoritative Critic result."""
    reconciled = copy.deepcopy(resolutions)
    critic_by_id = {issue["issue_id"]: issue for issue in critic_issues}
    critic_ids = set(critic_by_id)
    for resolution in reconciled:
        current_issue = critic_by_id.get(resolution["issue_id"])
        source_signature = resolution["validation"].get("source_issue_signature")
        same_issue = current_issue is not None and (
            not source_signature or source_signature == _issue_signature(current_issue)
        )
        if same_issue:
            resolution["status"] = "unresolved"
            resolution["validation"]["final_semantic_review"] = "failed"
        elif approved and resolution["status"] == "resolved":
            resolution["validation"]["final_semantic_review"] = "passed"
    unresolved = {
        resolution["issue_id"] for resolution in reconciled
        if resolution["status"] != "resolved"
    }
    if not approved:
        unresolved.update(critic_ids)
    return reconciled, sorted(unresolved)


TOOL_FUNCTIONS = {
    "rewrite_section": rewrite_section,
    "replace_unsupported_claim": replace_unsupported_claim,
    "fix_fact": fix_fact,
    "add_transition": add_transition,
    "remove_repetition": remove_repetition,
    "expand_section": expand_section,
    "shrink_section": shrink_section,
    "adjust_tone": adjust_tone,
}


def _tool_context(section_id: str, sections: list[GenerationSection]) -> str:
    index = next((i for i, item in enumerate(sections) if item["section_id"] == section_id), 0)
    previous = sections[index - 1]["content"][-300:] if index > 0 else "无上一章"
    following = sections[index + 1]["content"][:300] if index + 1 < len(sections) else "无下一章"
    summaries = "\n".join(f"{item['section_id']}: {item['summary']}" for item in sections)
    return f"上一章结尾：{previous}\n下一章开头：{following}\n全文摘要：\n{summaries}"


async def run_revision_agent(
    *,
    draft: str,
    research_result: ResearchResult,
    raw_content: dict[str, Any],
    generation_plan: GenerationPlan,
    section_reports: list[GenerationSection],
    writing_memory: WritingMemory,
    source: Literal["critic", "user"],
    revision_cycle: int,
    tasks: list[RevisionTask] | None = None,
    critic_issues: list[ReflectionIssue] | None = None,
) -> RevisionAgentResult:
    """Execute prioritized tasks with scoped tools, action limits, and validation logs."""
    plan = copy.deepcopy(generation_plan)
    sections = copy.deepcopy(section_reports or plan.get("sections", []))
    _sync_sections_from_draft(draft, plan, sections)
    plan["sections"] = sections
    normalized_tasks = copy.deepcopy(tasks) if tasks is not None else adapt_critic_issues(
        critic_issues or [], research_result, sections
    )
    normalized_tasks.sort(key=lambda item: (item["priority"], item["task_id"]))
    revision_plan: RevisionPlan = {
        "revision_cycle": revision_cycle,
        "source": source,
        "tasks": normalized_tasks,
        "max_actions": MAX_REVISION_ACTIONS,
    }
    actions: list[RevisionAction] = []
    resolutions: list[IssueResolution] = []
    errors: list[str] = []
    action_count = 0
    critic_issue_by_id = {
        issue["issue_id"]: issue for issue in (critic_issues or [])
    }

    for task in normalized_tasks:
        # Reserve three slots for mandatory fact, outline, and word-count validation.
        if action_count >= MAX_REVISION_ACTIONS - 3:
            task["status"] = "blocked"
            errors.append(f"{task['task_id']} blocked by action limit")
            continue
        task["status"] = "in_progress"
        target_ids = [
            section_id for section_id in task["target_section_ids"]
            if any(section["section_id"] == section_id for section in sections)
        ]
        if not target_ids:
            task["status"] = "blocked"
            errors.append(f"{task['task_id']} has no resolvable target section")
            continue
        task_action_ids: list[str] = []
        task_failed = False
        target_chars = task["constraints"].get("target_chars")
        ordered_operations = sorted(
            list(dict.fromkeys(task["operation_hints"])),
            key=lambda name: TOOL_ORDER.get(name, 99),
        )
        for operation in ordered_operations:
            if operation not in MUTATING_TOOLS:
                continue
            for section_id in target_ids:
                if action_count >= MAX_REVISION_ACTIONS - 3:
                    task_failed = True
                    break
                section = next(item for item in sections if item["section_id"] == section_id)
                if target_chars:
                    tolerance = float(task["constraints"].get("tolerance", 0.1))
                    section["budget"] = {
                        "target_chars": int(target_chars),
                        "min_chars": round(int(target_chars) * (1 - tolerance)),
                        "max_chars": round(int(target_chars) * (1 + tolerance)),
                    }
                before_sections = copy.deepcopy(sections)
                before_hashes = _section_hashes(before_sections)
                allowed_fact_ids = list(dict.fromkeys(section["fact_ids"] + task["target_fact_ids"]))
                facts = _facts_for_ids(research_result, allowed_fact_ids)
                action_id = f"A{action_count + 1:03d}"
                try:
                    updated = await TOOL_FUNCTIONS[operation](
                        section["content"],
                        task["requirements"],
                        facts,
                        _tool_context(section_id, sections),
                        section["budget"],
                    )
                    section["content"] = updated
                    _refresh_section(section)
                    fact_tokens_valid, missing_fact_tokens = _fact_tokens_preserved(
                        before_sections[
                            next(i for i, item in enumerate(before_sections) if item["section_id"] == section_id)
                        ]["content"],
                        updated,
                        facts,
                    )
                    after_hashes = _section_hashes(sections)
                    changed_non_targets = [
                        sid for sid, digest in before_hashes.items()
                        if sid not in target_ids and after_hashes.get(sid) != digest
                    ]
                    scope_valid = not changed_non_targets
                    preserve_facts = task["constraints"].get("preserve_facts", True)
                    if (
                        task["constraints"].get("preserve_other_sections", True) and not scope_valid
                    ) or (preserve_facts and not fact_tokens_valid):
                        sections = before_sections
                        plan["sections"] = sections
                        status = "rejected"
                        message = (
                            f"非目标章节发生变化：{changed_non_targets}；"
                            f"丢失事实标记：{missing_fact_tokens}"
                        )
                        task_failed = True
                    else:
                        status = "success"
                        message = "局部修改完成"
                    validation = {
                        "scope_preserved": scope_valid,
                        "changed_non_target_sections": changed_non_targets,
                        "fact_tokens_preserved": fact_tokens_valid,
                        "missing_fact_tokens": missing_fact_tokens,
                    }
                except Exception as exc:
                    sections = before_sections
                    plan["sections"] = sections
                    after_hashes = before_hashes
                    status = "failed"
                    message = str(exc)
                    validation = {"scope_preserved": True, "tool_error": str(exc)}
                    task_failed = True
                actions.append({
                    "action_id": action_id,
                    "task_id": task["task_id"],
                    "tool": operation,
                    "target_section_ids": [section_id],
                    "status": status,  # type: ignore[typeddict-item]
                    "before_hashes": before_hashes,
                    "after_hashes": after_hashes,
                    "validation": validation,
                    "message": message,
                })
                task_action_ids.append(action_id)
                action_count += 1
                if task_failed:
                    break
            if task_failed:
                break

        current_draft = _render_draft(plan, sections)
        fact_validation = validate_fact_usage(current_draft, research_result, raw_content)
        coverage_validation = check_outline_coverage(sections)
        word_validation = count_words(current_draft, sections, plan.get("target_chars", 0))
        for tool_name, validation in (
            ("validate_fact_usage", fact_validation),
            ("check_outline_coverage", coverage_validation),
            ("count_words", word_validation),
        ):
            if action_count >= MAX_REVISION_ACTIONS:
                break
            action_id = f"A{action_count + 1:03d}"
            actions.append({
                "action_id": action_id, "task_id": task["task_id"], "tool": tool_name,
                "target_section_ids": target_ids, "status": "success",
                "before_hashes": _section_hashes(sections),
                "after_hashes": _section_hashes(sections),
                "validation": validation, "message": "确定性验证完成",
            })
            task_action_ids.append(action_id)
            action_count += 1
        valid = (
            not task_failed
            and bool(fact_validation.get("valid"))
            and bool(coverage_validation.get("valid"))
        )
        if target_chars:
            target_sections = [item for item in sections if item["section_id"] in target_ids]
            valid = valid and all(item["status"] == "within_budget" for item in target_sections)
        task["status"] = "resolved" if valid else "unresolved"
        issue_ids = task["source_issue_ids"] or [f"USER:{task['task_id']}"]
        for issue_id in issue_ids:
            resolutions.append({
                "issue_id": issue_id,
                "task_id": task["task_id"],
                "status": "resolved" if valid else "unresolved",
                "tool_actions": task_action_ids,
                "validation": {
                    "fact_usage": fact_validation,
                    "outline_coverage": coverage_validation,
                    "word_count": word_validation,
                    "final_semantic_review": "pending_critic",
                    "source_issue_signature": (
                        _issue_signature(critic_issue_by_id[issue_id])
                        if issue_id in critic_issue_by_id else ""
                    ),
                },
            })

    final_draft = _render_draft(plan, sections)
    memory = _refresh_memory(writing_memory, sections, plan.get("target_chars", 0))
    unresolved = [
        resolution["issue_id"] for resolution in resolutions
        if resolution["status"] != "resolved"
    ]
    if any(task["status"] == "blocked" for task in normalized_tasks):
        status = "blocked"
    elif errors or unresolved or any(action["status"] != "success" for action in actions):
        status = "needs_review"
    else:
        status = "revised"
    return {
        "status": status,  # type: ignore[typeddict-item]
        "draft": final_draft,
        "generation_plan": plan,
        "section_reports": sections,
        "writing_memory": memory,
        "revision_plan": revision_plan,
        "revision_action_log": actions,
        "issue_resolution_log": resolutions,
        "tool_action_count": action_count,
        "unresolved_issues": unresolved,
        "errors": errors,
    }


__all__ = [
    "ALLOWED_TOOLS",
    "MAX_REVISION_ACTIONS",
    "adapt_critic_issues",
    "add_transition",
    "adjust_tone",
    "answer_tuning_question",
    "check_outline_coverage",
    "count_words",
    "expand_section",
    "fix_fact",
    "parse_tuning_intent",
    "remove_repetition",
    "reconcile_issue_resolutions",
    "replace_unsupported_claim",
    "rewrite_section",
    "run_revision_agent",
    "shrink_section",
    "validate_fact_usage",
]
