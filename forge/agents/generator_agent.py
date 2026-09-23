"""Controlled tool-using Generator Agent for grounded article drafting."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Literal, TypedDict

from forge.reference_articles.models import ReferencePack

from forge.agents.research_types import (
    GenerationPlan,
    GenerationSection,
    GeneratorAction,
    GeneratorDecision,
    GeneratorObservation,
    ResearchResult,
    WritingMemory,
    format_verified_facts,
    is_fact_approved,
)
from forge.tools.llm_client import LLMClient


MAX_GENERATOR_ACTIONS = 40
MAX_GENERATOR_MODEL_CALLS = 64
MAX_LENGTH_ADJUSTMENTS_PER_SECTION = 1
MAX_BOUNDARY_REPAIRS_PER_SECTION = 1
MAX_TRANSITION_IMPROVEMENTS_PER_SECTION = 1

GENERATOR_TOOLS = {
    "draft_section",
    "adjust_section_length",
    "repair_section_boundary",
    "improve_transition",
    "accept_section",
    "finish_generation",
}

WRITING_TOOLS = {
    "draft_section",
    "adjust_section_length",
    "repair_section_boundary",
    "improve_transition",
}


class GeneratorAgentResult(TypedDict):
    status: Literal["generated", "needs_review", "blocked"]
    draft: str
    generation_plan: GenerationPlan
    section_reports: list[GenerationSection]
    writing_memory: WritingMemory
    generator_action_log: list[GeneratorAction]
    generator_tool_action_count: int
    generator_model_call_count: int
    generator_stop_reason: str
    errors: list[str]


class _WriterResult(TypedDict):
    content: str
    used_fact_ids: list[str]


def _parse_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型响应不包含 JSON 对象")
        candidate = candidate[start:end + 1]
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise ValueError("模型响应必须是 JSON 对象")
    return value


def _count_visible_chars(text: str) -> int:
    without_urls = re.sub(r"https?://\S+", "", text)
    without_markup = re.sub(r"[`#*_>\-]", "", without_urls)
    return len(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", without_markup))


def _word_status(section: GenerationSection, content: str) -> str:
    actual = _count_visible_chars(content)
    if actual < section["budget"]["min_chars"]:
        return "too_short"
    if actual > section["budget"]["max_chars"]:
        return "too_long"
    return "within_budget"


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _fact_tokens(text: str) -> set[str]:
    return set(re.findall(r"\d{1,4}(?:\.\d+)?%?|\d{4}年|\d{1,2}月\d{1,2}日", text))


def _facts_by_ids(result: ResearchResult, fact_ids: list[str]) -> str:
    selected = {
        **result,
        "facts": [
            fact for fact in result.get("facts", [])
            if fact["fact_id"] in fact_ids and is_fact_approved(fact)
        ],
    }
    return format_verified_facts(selected)  # type: ignore[arg-type]


def _fact_claims_by_ids(result: ResearchResult, fact_ids: list[str]) -> str:
    return "\n".join(
        fact["claim"] for fact in result.get("facts", [])
        if fact["fact_id"] in fact_ids and is_fact_approved(fact)
    )


def _compact_summary(content: str, limit: int = 180) -> str:
    return re.sub(r"\s+", " ", content).strip()[:limit]


def _outline_signature(sections: list[GenerationSection]) -> list[tuple[str, str, str, tuple[str, ...]]]:
    return [
        (
            section["section_id"],
            section["title"],
            section["purpose"],
            tuple(section["fact_ids"]),
        )
        for section in sections
    ]


def _foreign_section_ids(
    content: str,
    target_id: str,
    sections: list[GenerationSection],
) -> list[str]:
    foreign: list[str] = []
    for section in sections:
        if section["section_id"] == target_id:
            continue
        title = re.escape(section["title"])
        if re.search(rf"(?m)^\s*(?:#{{1,6}}\s*)?{title}\s*$", content):
            foreign.append(section["section_id"])
    return foreign


def _observe(
    section: GenerationSection,
    tool: str,
    used_fact_ids: list[str],
    research_result: ResearchResult,
    sections: list[GenerationSection],
) -> GeneratorObservation:
    allowed_fact_ids = set(section["fact_ids"])
    declared_fact_ids = set(used_fact_ids)
    allowed_claims = _fact_claims_by_ids(research_result, section["fact_ids"])
    unsupported_tokens = sorted(_fact_tokens(section["content"]) - _fact_tokens(allowed_claims))
    foreign_ids = _foreign_section_ids(section["content"], section["section_id"], sections)
    missing_fact_ids = sorted(allowed_fact_ids - declared_fact_ids)
    return {
        "section_id": section["section_id"],
        "tool": tool,
        "actual_chars": _count_visible_chars(section["content"]),
        "word_status": _word_status(section, section["content"]),  # type: ignore[typeddict-item]
        "fact_boundary_valid": not unsupported_tokens,
        "unsupported_fact_tokens": unsupported_tokens,
        "declared_fact_ids_valid": declared_fact_ids <= allowed_fact_ids,
        "missing_fact_ids": missing_fact_ids,
        "outline_boundary_valid": not foreign_ids,
        "foreign_section_ids": foreign_ids,
        "content_hash": _content_hash(section["content"]),
    }


def _empty_observation(section_id: str, tool: str) -> GeneratorObservation:
    return {
        "section_id": section_id,
        "tool": tool,
        "actual_chars": 0,
        "word_status": "failed",
        "fact_boundary_valid": False,
        "unsupported_fact_tokens": [],
        "declared_fact_ids_valid": False,
        "missing_fact_ids": [],
        "outline_boundary_valid": False,
        "foreign_section_ids": [],
        "content_hash": "",
    }


def _observation_valid(observation: GeneratorObservation) -> bool:
    return (
        observation["word_status"] == "within_budget"
        and observation["fact_boundary_valid"]
        and observation["declared_fact_ids_valid"]
        and not observation["missing_fact_ids"]
        and observation["outline_boundary_valid"]
    )


def _render_draft(plan: GenerationPlan, sections: list[GenerationSection]) -> str:
    if plan["mode"] == "single_pass" or len(sections) == 1:
        return sections[0]["content"] if sections else ""
    return "\n\n".join(
        f"## {section['title']}\n\n{section['content']}"
        for section in sections if section["content"]
    )


def _update_memory_after_accept(
    memory: WritingMemory,
    section: GenerationSection,
) -> None:
    memory["used_fact_ids"] = list(dict.fromkeys(
        memory["used_fact_ids"] + section["fact_ids"]
    ))
    memory["section_summaries"][section["section_id"]] = section["summary"]
    memory["previous_tail"] = section["content"][-400:]


def _refresh_memory_totals(
    memory: WritingMemory,
    sections: list[GenerationSection],
    accepted_ids: set[str],
    target_chars: int,
) -> None:
    actual = sum(
        section["actual_chars"] for section in sections
        if section["section_id"] in accepted_ids
    )
    memory["actual_chars"] = actual
    memory["remaining_target_chars"] = max(0, target_chars - actual)


def _writing_context(
    section: GenerationSection,
    sections: list[GenerationSection],
    memory: WritingMemory,
) -> str:
    index = next(i for i, item in enumerate(sections) if item["section_id"] == section["section_id"])
    next_title = sections[index + 1]["title"] if index + 1 < len(sections) else "无，当前是最后一章"
    summaries = "\n".join(
        f"{section_id}: {summary}"
        for section_id, summary in memory["section_summaries"].items()
    ) or "无"
    return (
        f"已完成章节摘要：\n{summaries}\n\n"
        f"上一章结尾：\n{memory['previous_tail'] or '无，当前是第一章'}\n\n"
        f"下一章标题：{next_title}"
    )


def _writer_prompt(
    *,
    tool: str,
    section: GenerationSection,
    sections: list[GenerationSection],
    memory: WritingMemory,
    research_result: ResearchResult,
    approved_outline: str,
    user_input: str,
    platform_style: str,
    source_metadata: dict[str, Any],
    reference_pack: ReferencePack,
) -> str:
    instructions = {
        "draft_section": "从零生成当前章节。",
        "adjust_section_length": (
            "只调整当前正文长度；过长时删除重复和次要解释，过短时解释已有事实之间的关系。"
        ),
        "repair_section_boundary": (
            "删除当前章节事实清单不支持的数字、日期和陈述，并删除其他章节标题或内容。"
        ),
        "improve_transition": (
            "只改善当前章节开头与上一章的承接，主体事实、结论和篇幅保持稳定。"
        ),
    }
    current = section["content"] or "尚未生成"
    facts = _facts_by_ids(research_result, section["fact_ids"])
    section_order = " → ".join(item["title"] for item in sections)
    return f"""你是受控 Generator Agent 的章节写作工具。{instructions[tool]}

## 批准大纲
{approved_outline}

## 固定章节顺序
{section_order}

## 当前唯一允许修改的章节
ID：{section['section_id']}
标题：{section['title']}
目的：{section['purpose']}
目标字数：{section['budget']['target_chars']}字
允许范围：{section['budget']['min_chars']}～{section['budget']['max_chars']}字

## 当前正文
{current}

## 当前章节唯一允许使用的事实
{facts}

## 写作上下文
{_writing_context(section, sections, memory)}

## 原始来源元数据
标题：{source_metadata.get('title', '')}
URL：{source_metadata.get('url') or source_metadata.get('source_url') or 'source://article'}

## 用户要求和平台风格
{user_input}
{platform_style}

## 参考文章库给出的结构与风格约束
{reference_pack.prompt_context()}

硬规则：
1. 只输出当前章节，不得生成或改写其他章节。
2. 不得改变批准大纲、章节标题、章节顺序和章节目的。
3. 数字、日期、人物、机构、案例和事实只能来自当前章节事实清单。
4. 必须在 used_fact_ids 中声明正文实际使用的 Fact ID；不得声明未提供的 Fact ID。
5. 不输出章节标题、字数统计、Fact ID、来源列表或写作说明。
6. content 必须是非空字符串。
7. 参考样本只用于学习结构角色、节奏和语气；不得复用其句子、事实、数字或案例。

只输出 JSON：
{{"content":"修改后的当前章节正文","used_fact_ids":["F001"]}}
"""


def _controller_prompt(
    *,
    plan: GenerationPlan,
    sections: list[GenerationSection],
    memory: WritingMemory,
    allowed_actions: list[dict[str, Any]],
    last_observation: GeneratorObservation | None,
    action_count: int,
) -> str:
    section_state = [
        {
            "section_id": item["section_id"],
            "title": item["title"],
            "status": item["status"],
            "actual_chars": item["actual_chars"],
            "budget": item["budget"],
            "fact_ids": item["fact_ids"],
        }
        for item in sections
    ]
    return f"""你是受控 Generator Agent 的控制器，只选择下一步工具，不写文章。

目标：依据批准大纲、章节事实和字数预算完成文章。

硬规则：
1. 只能从 allowed_actions 原样选择一个动作。
2. action、section_id、fact_ids 必须与所选 allowed_action 完全一致。
3. 每次只选择一个动作，不得自行创建工具或章节。
4. 事实或章节边界失败时优先修复；字数失败时调整长度。
5. 只有全部章节被接受后才能 finish_generation。

生成模式：{plan['mode']}
章节状态：{json.dumps(section_state, ensure_ascii=False)}
写作记忆：{json.dumps(memory, ensure_ascii=False)}
最近观察：{json.dumps(last_observation, ensure_ascii=False) if last_observation else '无'}
已执行动作：{action_count}/{MAX_GENERATOR_ACTIONS}

allowed_actions：
{json.dumps(allowed_actions, ensure_ascii=False)}

只输出 JSON：
{{"action":"工具名","section_id":"S01或空字符串","fact_ids":[],"reason":"简短选择理由"}}
"""


def _normalize_writer_result(payload: dict[str, Any]) -> _WriterResult:
    content = str(payload.get("content") or "").strip()
    if not content:
        raise ValueError("写作工具返回了空正文")
    raw_ids = payload.get("used_fact_ids", [])
    if not isinstance(raw_ids, list):
        raise ValueError("used_fact_ids 必须是数组")
    return {
        "content": content,
        "used_fact_ids": list(dict.fromkeys(str(item) for item in raw_ids)),
    }


def _normalize_decision(
    payload: dict[str, Any],
    allowed_actions: list[dict[str, Any]],
) -> GeneratorDecision:
    action = str(payload.get("action") or "")
    section_id = str(payload.get("section_id") or "")
    raw_ids = payload.get("fact_ids", [])
    if not isinstance(raw_ids, list):
        raise ValueError("控制器 fact_ids 必须是数组")
    fact_ids = [str(item) for item in raw_ids]
    if action not in GENERATOR_TOOLS:
        raise ValueError(f"控制器选择了未知工具：{action}")
    exact = next(
        (
            item for item in allowed_actions
            if item["action"] == action
            and item["section_id"] == section_id
            and item["fact_ids"] == fact_ids
        ),
        None,
    )
    if exact is None:
        raise ValueError("控制器选择超出 allowed_actions 硬边界")
    return {
        "action": action,  # type: ignore[typeddict-item]
        "section_id": section_id,
        "fact_ids": fact_ids,
        "reason": str(payload.get("reason") or "按当前观察选择下一步"),
    }


def _allowed_actions(
    sections: list[GenerationSection],
    accepted_ids: set[str],
    active_section_id: str,
    observations: dict[str, GeneratorObservation],
    length_attempts: dict[str, int],
    boundary_attempts: dict[str, int],
    transition_attempts: dict[str, int],
) -> tuple[list[dict[str, Any]], str]:
    if active_section_id:
        section = next(item for item in sections if item["section_id"] == active_section_id)
        observation = observations[active_section_id]
        boundary_valid = (
            observation["fact_boundary_valid"]
            and observation["declared_fact_ids_valid"]
            and not observation["missing_fact_ids"]
            and observation["outline_boundary_valid"]
        )
        if not boundary_valid:
            if boundary_attempts[active_section_id] >= MAX_BOUNDARY_REPAIRS_PER_SECTION:
                return [], f"章节 {active_section_id} 修复后仍违反事实或大纲边界"
            return [{
                "action": "repair_section_boundary",
                "section_id": active_section_id,
                "fact_ids": section["fact_ids"],
            }], ""
        if observation["word_status"] != "within_budget":
            if length_attempts[active_section_id] >= MAX_LENGTH_ADJUSTMENTS_PER_SECTION:
                return [], f"章节 {active_section_id} 调整后仍不满足字数预算"
            return [{
                "action": "adjust_section_length",
                "section_id": active_section_id,
                "fact_ids": section["fact_ids"],
            }], ""
        choices = [{
            "action": "accept_section",
            "section_id": active_section_id,
            "fact_ids": section["fact_ids"],
        }]
        index = next(i for i, item in enumerate(sections) if item["section_id"] == active_section_id)
        if index > 0 and transition_attempts[active_section_id] < MAX_TRANSITION_IMPROVEMENTS_PER_SECTION:
            choices.append({
                "action": "improve_transition",
                "section_id": active_section_id,
                "fact_ids": section["fact_ids"],
            })
        return choices, ""

    pending = next(
        (item for item in sections if item["section_id"] not in accepted_ids),
        None,
    )
    if pending:
        return [{
            "action": "draft_section",
            "section_id": pending["section_id"],
            "fact_ids": pending["fact_ids"],
        }], ""
    return [{"action": "finish_generation", "section_id": "", "fact_ids": []}], ""


def _validate_plan_facts(plan: GenerationPlan, research_result: ResearchResult) -> list[str]:
    available = {
        fact["fact_id"] for fact in research_result.get("facts", [])
        if is_fact_approved(fact)
    }
    errors: list[str] = []
    seen: set[str] = set()
    for section in plan["sections"]:
        section_id = section["section_id"]
        if section_id in seen:
            errors.append(f"生成计划存在重复章节ID：{section_id}")
        seen.add(section_id)
        invalid = sorted(set(section["fact_ids"]) - available)
        if invalid:
            errors.append(f"章节 {section_id} 引用了不可用事实：{invalid}")
    return errors


def _prepare_plan(
    generation_plan: GenerationPlan,
    approved_outline: str,
    research_result: ResearchResult,
) -> GenerationPlan:
    plan = copy.deepcopy(generation_plan)
    if plan["mode"] == "single_pass" and plan["sections"]:
        section = plan["sections"][0]
        section["title"] = "全文"
        section["purpose"] = f"按照批准大纲完整生成文章：{approved_outline}"
        section["fact_ids"] = [
            fact["fact_id"] for fact in research_result.get("facts", [])
            if is_fact_approved(fact)
        ]
        section["budget"] = {
            "target_chars": plan["target_chars"],
            "min_chars": plan["min_chars"],
            "max_chars": plan["max_chars"],
        }
        plan["sections"] = [section]
    return plan


async def run_generator_agent(
    *,
    generation_plan: GenerationPlan,
    writing_memory: WritingMemory,
    research_result: ResearchResult,
    approved_outline: str,
    user_input: str,
    platform_style: str,
    source_metadata: dict[str, Any],
    reference_pack: ReferencePack | None = None,
) -> GeneratorAgentResult:
    """Run a bounded observe-decide-act loop until hard stop conditions are met."""
    errors = _validate_plan_facts(generation_plan, research_result)
    plan = _prepare_plan(generation_plan, approved_outline, research_result)
    sections = plan["sections"]
    memory = copy.deepcopy(writing_memory)
    reference_pack = reference_pack or ReferencePack.empty()
    errors.extend(_validate_plan_facts(plan, research_result))
    if not sections:
        errors.append("生成计划没有章节")
    if errors:
        return {
            "status": "blocked",
            "draft": "",
            "generation_plan": plan,
            "section_reports": sections,
            "writing_memory": memory,
            "generator_action_log": [],
            "generator_tool_action_count": 0,
            "generator_model_call_count": 0,
            "generator_stop_reason": "；".join(errors),
            "errors": errors,
        }

    outline_signature = _outline_signature(sections)
    llm = LLMClient()
    actions: list[GeneratorAction] = []
    observations: dict[str, GeneratorObservation] = {}
    accepted_ids: set[str] = set()
    active_section_id = ""
    length_attempts = {section["section_id"]: 0 for section in sections}
    boundary_attempts = {section["section_id"]: 0 for section in sections}
    transition_attempts = {section["section_id"]: 0 for section in sections}
    model_calls = 0
    stop_reason = ""
    finished = False

    while len(actions) < MAX_GENERATOR_ACTIONS:
        if model_calls >= MAX_GENERATOR_MODEL_CALLS:
            stop_reason = "达到 Generator Agent 模型调用上限"
            errors.append(stop_reason)
            break
        allowed, blocked_reason = _allowed_actions(
            sections,
            accepted_ids,
            active_section_id,
            observations,
            length_attempts,
            boundary_attempts,
            transition_attempts,
        )
        if blocked_reason:
            stop_reason = blocked_reason
            errors.append(blocked_reason)
            break
        last_observation = observations.get(active_section_id) if active_section_id else None
        try:
            model_calls += 1
            controller_raw = await llm.chat_with_retry(_controller_prompt(
                plan=plan,
                sections=sections,
                memory=memory,
                allowed_actions=allowed,
                last_observation=last_observation,
                action_count=len(actions),
            ))
            decision = _normalize_decision(_parse_json_object(controller_raw), allowed)
        except Exception as exc:
            stop_reason = f"Generator Controller 决策失败：{exc}"
            errors.append(stop_reason)
            break

        tool = decision["action"]
        section_id = decision["section_id"]
        if tool == "finish_generation":
            if len(accepted_ids) != len(sections):
                stop_reason = "存在未接受章节，禁止结束生成"
                errors.append(stop_reason)
                break
            action_id = f"G{len(actions) + 1:03d}"
            actions.append({
                "action_id": action_id,
                "tool": tool,
                "section_id": "",
                "fact_ids": [],
                "status": "success",
                "reason": decision["reason"],
                "observation": _empty_observation("", tool),
                "model_calls": model_calls,
                "message": "所有章节通过硬边界并完成组装",
            })
            stop_reason = "finish_generation"
            finished = True
            break

        section = next(item for item in sections if item["section_id"] == section_id)
        if tool == "accept_section":
            observation = observations[section_id]
            if not _observation_valid(observation):
                stop_reason = f"章节 {section_id} 未通过硬边界，禁止接受"
                errors.append(stop_reason)
                break
            section.update({
                "actual_chars": observation["actual_chars"],
                "status": "within_budget",
                "summary": _compact_summary(section["content"]),
            })
            accepted_ids.add(section_id)
            _update_memory_after_accept(memory, section)
            _refresh_memory_totals(memory, sections, accepted_ids, plan["target_chars"])
            active_section_id = ""
            actions.append({
                "action_id": f"G{len(actions) + 1:03d}",
                "tool": tool,
                "section_id": section_id,
                "fact_ids": decision["fact_ids"],
                "status": "success",
                "reason": decision["reason"],
                "observation": observation,
                "model_calls": model_calls,
                "message": "章节通过事实、大纲和字数硬边界",
            })
            continue

        before_content = section["content"]
        try:
            if model_calls >= MAX_GENERATOR_MODEL_CALLS:
                raise RuntimeError("达到 Generator Agent 模型调用上限")
            model_calls += 1
            writer_raw = await llm.chat_with_retry(_writer_prompt(
                tool=tool,
                section=section,
                sections=sections,
                memory=memory,
                research_result=research_result,
                approved_outline=approved_outline,
                user_input=user_input,
                platform_style=platform_style,
                source_metadata=source_metadata,
                reference_pack=reference_pack,
            ))
            writer_result = _normalize_writer_result(_parse_json_object(writer_raw))
            section["content"] = writer_result["content"]
            observation = _observe(
                section,
                tool,
                writer_result["used_fact_ids"],
                research_result,
                sections,
            )
            section.update({
                "actual_chars": observation["actual_chars"],
                "status": observation["word_status"],
                "summary": _compact_summary(section["content"]),
            })
            if tool == "draft_section":
                active_section_id = section_id
            elif tool == "adjust_section_length":
                length_attempts[section_id] += 1
                section["revision_attempts"] += 1
            elif tool == "repair_section_boundary":
                boundary_attempts[section_id] += 1
                section["revision_attempts"] += 1
            elif tool == "improve_transition":
                transition_attempts[section_id] += 1
                section["revision_attempts"] += 1
            if _outline_signature(sections) != outline_signature:
                section["content"] = before_content
                raise RuntimeError("写作工具修改了批准大纲或章节事实分配")
            observations[section_id] = observation
            actions.append({
                "action_id": f"G{len(actions) + 1:03d}",
                "tool": tool,
                "section_id": section_id,
                "fact_ids": decision["fact_ids"],
                "status": "success",
                "reason": decision["reason"],
                "observation": observation,
                "model_calls": model_calls,
                "message": "写作工具执行完成，等待控制器依据观察继续决策",
            })
        except Exception as exc:
            section["content"] = before_content
            section["status"] = "failed" if not before_content else section["status"]
            observation = _empty_observation(section_id, tool)
            actions.append({
                "action_id": f"G{len(actions) + 1:03d}",
                "tool": tool,
                "section_id": section_id,
                "fact_ids": decision["fact_ids"],
                "status": "failed",
                "reason": decision["reason"],
                "observation": observation,
                "model_calls": model_calls,
                "message": str(exc),
            })
            stop_reason = f"工具 {tool} 执行失败：{exc}"
            errors.append(stop_reason)
            break

    if not finished and not stop_reason:
        stop_reason = "达到 Generator Agent 动作上限"
        errors.append(stop_reason)

    draft = _render_draft(plan, sections)
    if finished:
        status: Literal["generated", "needs_review", "blocked"] = "generated"
    elif draft:
        status = "needs_review"
    else:
        status = "blocked"
    return {
        "status": status,
        "draft": draft,
        "generation_plan": plan,
        "section_reports": sections,
        "writing_memory": memory,
        "generator_action_log": actions,
        "generator_tool_action_count": len(actions),
        "generator_model_call_count": model_calls,
        "generator_stop_reason": stop_reason,
        "errors": errors,
    }


__all__ = [
    "GENERATOR_TOOLS",
    "MAX_GENERATOR_ACTIONS",
    "MAX_GENERATOR_MODEL_CALLS",
    "run_generator_agent",
]
