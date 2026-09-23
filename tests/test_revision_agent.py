"""Tests for the bounded Revision Agent and tuning intent parser."""

import copy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.agents.revision_agent import (
    adapt_critic_issues,
    check_outline_coverage,
    parse_tuning_intent,
    reconcile_issue_resolutions,
    run_revision_agent,
    validate_fact_usage,
)


@pytest.fixture
def research_result():
    return {
        "status": "complete", "tasks": [], "evidence": [],
        "facts": [{
            "fact_id": "F001", "task_id": "T01", "section_id": "S01",
            "claim": "锐博集团创办于2002年，总部位于广州。",
            "status": "verified", "confidence": 1.0, "evidence_ids": ["E001"],
            "review": {
                "decision": "accepted", "comment": "", "priority": "normal",
                "reviewed_at": "now",
            },
            "sources": [{
                "evidence_id": "E001", "source": "锐博官网", "title": "集团概况",
                "url": "https://www.nnngu.com/", "published_at": "",
                "retrieved_at": "2026-07-22",
            }],
        }],
        "coverage": 1.0, "warnings": [], "generated_at": "2026-07-22",
    }


@pytest.fixture
def generation_artifacts():
    sections = [
        {
            "section_id": "S01", "title": "集团背景", "purpose": "介绍背景",
            "fact_ids": ["F001"],
            "budget": {"target_chars": 200, "min_chars": 180, "max_chars": 220},
            "actual_chars": 20, "status": "too_short",
            "content": "锐博集团创办于2002年，总部位于广州。", "summary": "集团背景",
            "revision_attempts": 0,
        },
        {
            "section_id": "S02", "title": "业务说明", "purpose": "说明业务",
            "fact_ids": [],
            "budget": {"target_chars": 200, "min_chars": 180, "max_chars": 220},
            "actual_chars": 8, "status": "too_short", "content": "原有业务说明。",
            "summary": "业务说明", "revision_attempts": 0,
        },
    ]
    plan = {
        "mode": "section_sequential", "reason": "测试", "target_chars": 400,
        "min_chars": 380, "max_chars": 420, "counting_method": "可见字符",
        "sections": sections,
    }
    memory = {
        "thesis": "集团介绍", "terminology": {}, "used_fact_ids": ["F001"],
        "section_summaries": {"S01": "背景", "S02": "业务"}, "open_threads": [],
        "previous_tail": "原有业务说明。", "actual_chars": 28,
        "remaining_target_chars": 372,
    }
    draft = "## 集团背景\n\n锐博集团创办于2002年，总部位于广州。\n\n## 业务说明\n\n原有业务说明。"
    return plan, sections, memory, draft


def _user_task(operations, target="S02", target_chars=None):
    return {
        "task_id": "R001", "source": "user", "source_issue_ids": [],
        "operation_hints": operations, "target_section_ids": [target],
        "target_fact_ids": [], "requirements": ["按要求修改"],
        "constraints": {
            "target_chars": target_chars, "tolerance": 0.1, "target_tone": "",
            "preserve_facts": True, "preserve_other_sections": True,
        },
        "priority": 3, "status": "pending",
        "validation_rules": ["scope_boundary", "fact_usage", "critic"],
    }


def test_critic_adapter_prioritizes_fact_safety(research_result, generation_artifacts):
    _, sections, _, _ = generation_artifacts
    issues = [
        {"issue_id": "I001", "issue_type": "style", "severity": "medium",
         "span": "业务说明", "fact_id": "", "instruction": "调整风格"},
        {"issue_id": "I002", "issue_type": "unsupported_claim", "severity": "high",
         "span": "集团背景", "fact_id": "F001", "instruction": "替换无来源陈述"},
    ]

    tasks = adapt_critic_issues(issues, research_result, sections)

    assert tasks[0]["source_issue_ids"] == ["I002"]
    assert tasks[0]["operation_hints"] == ["replace_unsupported_claim"]
    assert tasks[1]["operation_hints"] == ["adjust_tone"]


@pytest.mark.asyncio
async def test_tuning_parser_converts_compound_request(research_result, generation_artifacts):
    plan, _, _, _ = generation_artifacts
    parser_json = """{
      "intent":"edit", "reason":"明确修改第二部分", "tasks":[{
        "target_sections":["第二部分"], "target_fact_ids":[],
        "operation_hints":["rewrite_section","adjust_tone","shrink_section"],
        "requirements":["降低技术难度","压缩到500字","公众号风格"],
        "target_chars":500, "target_tone":"公众号",
        "preserve_facts":true, "preserve_other_sections":true
      }]
    }"""
    with patch("forge.agents.revision_agent.LLMClient") as llm_class:
        llm = MagicMock()
        llm.chat_with_retry = AsyncMock(return_value=parser_json)
        llm_class.return_value = llm
        intent = await parse_tuning_intent("修改第二部分", plan, research_result)

    assert intent["intent"] == "edit"
    assert intent["tasks"][0]["target_section_ids"] == ["S02"]
    assert intent["tasks"][0]["constraints"]["target_chars"] == 500
    assert intent["tasks"][0]["constraints"]["preserve_other_sections"] is True


@pytest.mark.asyncio
async def test_parser_failure_never_guesses_an_edit(research_result, generation_artifacts):
    plan, _, _, _ = generation_artifacts
    with patch("forge.agents.revision_agent.LLMClient") as llm_class:
        llm = MagicMock()
        llm.chat_with_retry = AsyncMock(side_effect=RuntimeError("parser down"))
        llm_class.return_value = llm
        intent = await parse_tuning_intent("第二部分不太对", plan, research_result)

    assert intent["intent"] == "clarify"
    assert intent["tasks"] == []


@pytest.mark.asyncio
async def test_parser_routes_source_question_without_edit_tasks(research_result, generation_artifacts):
    plan, _, _, _ = generation_artifacts
    parser_json = '{"intent":"question","question":"2002年从哪里来？","reason":"询问来源","tasks":[]}'
    with patch("forge.agents.revision_agent.LLMClient") as llm_class:
        llm = MagicMock()
        llm.chat_with_retry = AsyncMock(return_value=parser_json)
        llm_class.return_value = llm
        intent = await parse_tuning_intent("2002年从哪里来？", plan, research_result)

    assert intent["intent"] == "question"
    assert intent["tasks"] == []
    assert intent["question"] == "2002年从哪里来？"


@pytest.mark.asyncio
async def test_revision_agent_only_changes_target_section(research_result, generation_artifacts):
    plan, sections, memory, draft = generation_artifacts
    revised = "修改后的业务说明内容" * 20
    with patch("forge.agents.revision_agent.LLMClient") as llm_class:
        llm = MagicMock()
        llm.chat_with_retry = AsyncMock(return_value=revised)
        llm_class.return_value = llm
        result = await run_revision_agent(
            draft=draft, research_result=research_result, raw_content={"text": "原文"},
            generation_plan=plan, section_reports=sections, writing_memory=memory,
            source="user", revision_cycle=1, tasks=[_user_task(["rewrite_section"])],
        )

    assert result["section_reports"][0]["content"] == sections[0]["content"]
    assert result["section_reports"][1]["content"] == revised
    assert result["revision_action_log"][0]["validation"]["scope_preserved"] is True
    assert result["revision_plan"]["source"] == "user"


@pytest.mark.asyncio
async def test_revision_agent_uses_fixed_tool_order(research_result, generation_artifacts):
    plan, sections, memory, draft = generation_artifacts
    outputs = ["改写" * 100, "压缩" * 100, "语气" * 100]
    task = _user_task(["adjust_tone", "shrink_section", "rewrite_section"])
    with patch("forge.agents.revision_agent.LLMClient") as llm_class:
        llm = MagicMock()
        llm.chat_with_retry = AsyncMock(side_effect=outputs)
        llm_class.return_value = llm
        result = await run_revision_agent(
            draft=draft, research_result=research_result, raw_content={"text": "原文"},
            generation_plan=plan, section_reports=sections, writing_memory=memory,
            source="user", revision_cycle=1, tasks=[task],
        )

    mutating = [item["tool"] for item in result["revision_action_log"] if item["tool"] not in {
        "count_words", "check_outline_coverage", "validate_fact_usage"
    }]
    assert mutating == ["rewrite_section", "shrink_section", "adjust_tone"]


@pytest.mark.asyncio
async def test_fact_token_loss_rejects_action(research_result, generation_artifacts):
    plan, sections, memory, draft = generation_artifacts
    task = _user_task(["adjust_tone"], target="S01")
    with patch("forge.agents.revision_agent.LLMClient") as llm_class:
        llm = MagicMock()
        llm.chat_with_retry = AsyncMock(return_value="集团位于广州。")
        llm_class.return_value = llm
        result = await run_revision_agent(
            draft=draft, research_result=research_result,
            raw_content={"text": "锐博集团创办于2002年。"},
            generation_plan=plan, section_reports=sections, writing_memory=memory,
            source="user", revision_cycle=1, tasks=[task],
        )

    first_action = result["revision_action_log"][0]
    assert first_action["status"] == "rejected"
    assert first_action["validation"]["missing_fact_tokens"] == ["2002"]
    assert result["section_reports"][0]["content"] == sections[0]["content"]
    assert result["status"] == "needs_review"


@pytest.mark.asyncio
async def test_revision_agent_stops_at_action_limit(research_result, generation_artifacts):
    plan, sections, memory, draft = generation_artifacts
    tasks = []
    for index in range(3):
        task = _user_task(["rewrite_section"])
        task["task_id"] = f"R{index + 1:03d}"
        tasks.append(task)
    with patch("forge.agents.revision_agent.LLMClient") as llm_class:
        llm = MagicMock()
        llm.chat_with_retry = AsyncMock(return_value="修改内容" * 40)
        llm_class.return_value = llm
        result = await run_revision_agent(
            draft=draft, research_result=research_result, raw_content={"text": "原文"},
            generation_plan=plan, section_reports=sections, writing_memory=memory,
            source="user", revision_cycle=1, tasks=tasks,
        )

    assert result["tool_action_count"] == 8
    assert result["revision_plan"]["tasks"][2]["status"] == "blocked"
    assert result["status"] == "blocked"


def test_validation_tools_detect_missing_sections_and_numbers(research_result, generation_artifacts):
    _, sections, _, _ = generation_artifacts
    missing = copy.deepcopy(sections)
    missing[1]["content"] = ""

    coverage = check_outline_coverage(missing)
    facts = validate_fact_usage(
        "锐博集团创办于2002年，拥有9999名员工。",
        research_result,
        {"text": "锐博集团创办于2002年。"},
    )

    assert coverage == {"valid": False, "missing_section_ids": ["S02"]}
    assert facts["valid"] is False
    assert "9999" in facts["unsupported_tokens"]


def test_critic_can_reopen_provisionally_resolved_issue():
    resolutions = [{
        "issue_id": "I001", "task_id": "R001", "status": "resolved",
        "tool_actions": ["A001"],
        "validation": {"final_semantic_review": "pending_critic"},
    }]
    critic_issues = [{
        "issue_id": "I001", "issue_type": "unsupported_claim", "severity": "high",
        "span": "问题陈述", "fact_id": "", "instruction": "仍未解决",
    }]

    reconciled, unresolved = reconcile_issue_resolutions(resolutions, critic_issues, False)

    assert reconciled[0]["status"] == "unresolved"
    assert reconciled[0]["validation"]["final_semantic_review"] == "failed"
    assert unresolved == ["I001"]
