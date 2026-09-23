"""Tests for the bounded observe-decide-act Generator Agent."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.agents.generator_agent import run_generator_agent


@pytest.fixture
def research_result():
    return {
        "status": "complete",
        "tasks": [],
        "evidence": [{
            "evidence_id": "E001",
            "task_id": "source",
            "source": "锐博官网",
            "title": "集团概况",
            "url": "https://www.nnngu.com/group-profile",
            "published_at": "2026-01-01",
            "retrieved_at": "2026-07-23T00:00:00+00:00",
            "excerpt": "锐博集团创办于2002年。",
            "source_type": "source_article",
            "authority_score": 1.0,
            "relevance_score": 1.0,
            "content_hash": "hash",
        }],
        "facts": [{
            "fact_id": "F001",
            "task_id": "source",
            "section_id": "S01",
            "claim": "锐博集团创办于2002年。",
            "status": "verified",
            "confidence": 1.0,
            "review": {
                "decision": "accepted", "comment": "", "priority": "normal",
                "reviewed_at": "now",
            },
            "evidence_ids": ["E001"],
            "sources": [{
                "evidence_id": "E001",
                "source": "锐博官网",
                "title": "集团概况",
                "url": "https://www.nnngu.com/group-profile",
                "published_at": "2026-01-01",
                "retrieved_at": "2026-07-23T00:00:00+00:00",
            }],
        }],
        "coverage": 1.0,
        "warnings": [],
        "generated_at": "now",
    }


@pytest.fixture
def plan():
    return {
        "mode": "single_pass",
        "reason": "短文",
        "target_chars": 300,
        "min_chars": 270,
        "max_chars": 330,
        "counting_method": "可见字符",
        "sections": [{
            "section_id": "S01",
            "title": "集团背景",
            "purpose": "说明集团背景",
            "fact_ids": ["F001"],
            "budget": {"target_chars": 300, "min_chars": 270, "max_chars": 330},
            "actual_chars": 0,
            "status": "pending",
            "content": "",
            "summary": "",
            "revision_attempts": 0,
        }],
    }


@pytest.fixture
def memory():
    return {
        "thesis": "集团概况",
        "terminology": {},
        "used_fact_ids": [],
        "section_summaries": {},
        "open_threads": [],
        "previous_tail": "",
        "actual_chars": 0,
        "remaining_target_chars": 300,
    }


def decision(action, section_id="S01", fact_ids=None):
    return json.dumps({
        "action": action,
        "section_id": section_id,
        "fact_ids": ["F001"] if fact_ids is None else fact_ids,
        "reason": "依据最新观察选择",
    }, ensure_ascii=False)


def written(content, fact_ids=None):
    return json.dumps({
        "content": content,
        "used_fact_ids": ["F001"] if fact_ids is None else fact_ids,
    }, ensure_ascii=False)


async def run_with_outputs(outputs, plan, memory, research_result):
    with patch("forge.agents.generator_agent.LLMClient") as llm_class:
        llm = MagicMock()
        llm.chat_with_retry = AsyncMock(side_effect=outputs)
        llm_class.return_value = llm
        result = await run_generator_agent(
            generation_plan=plan,
            writing_memory=memory,
            research_result=research_result,
            approved_outline="一、集团背景",
            user_input="写成300字",
            platform_style="知乎文章",
            source_metadata={
                "title": "集团概况",
                "url": "https://www.nnngu.com/group-profile",
            },
        )
    return result, llm


@pytest.mark.asyncio
async def test_controller_selects_length_tool_from_observation(plan, memory, research_result):
    short = "锐博集团创办于2002年。"
    corrected = "锐博集团创办于2002年。" + "业务内容" * 70
    outputs = [
        decision("draft_section"),
        written(short),
        decision("adjust_section_length"),
        written(corrected),
        decision("accept_section"),
        decision("finish_generation", "", []),
    ]

    result, llm = await run_with_outputs(outputs, plan, memory, research_result)

    assert result["status"] == "generated"
    assert [item["tool"] for item in result["generator_action_log"]] == [
        "draft_section", "adjust_section_length", "accept_section", "finish_generation",
    ]
    assert result["section_reports"][0]["revision_attempts"] == 1
    assert result["generator_model_call_count"] == 6
    assert llm.chat_with_retry.await_count == 6


@pytest.mark.asyncio
async def test_controller_repairs_fact_boundary_before_length(plan, memory, research_result):
    unsupported = "锐博集团创办于2002年，拥有999个项目。" + "内容" * 140
    repaired = "锐博集团创办于2002年。" + "业务内容" * 70
    outputs = [
        decision("draft_section"),
        written(unsupported),
        decision("repair_section_boundary"),
        written(repaired),
        decision("accept_section"),
        decision("finish_generation", "", []),
    ]

    result, _ = await run_with_outputs(outputs, plan, memory, research_result)

    assert result["status"] == "generated"
    assert result["generator_action_log"][0]["observation"]["unsupported_fact_tokens"] == ["999"]
    assert result["generator_action_log"][1]["tool"] == "repair_section_boundary"
    assert "999" not in result["draft"]


@pytest.mark.asyncio
async def test_controller_cannot_finish_before_sections_are_accepted(plan, memory, research_result):
    result, _ = await run_with_outputs(
        [decision("finish_generation", "", [])],
        plan,
        memory,
        research_result,
    )

    assert result["status"] == "blocked"
    assert result["generator_action_log"] == []
    assert "allowed_actions" in result["generator_stop_reason"]


@pytest.mark.asyncio
async def test_invalid_plan_fact_blocks_before_model_call(plan, memory, research_result):
    plan["sections"][0]["fact_ids"] = ["F999"]
    with patch("forge.agents.generator_agent.LLMClient") as llm_class:
        result = await run_generator_agent(
            generation_plan=plan,
            writing_memory=memory,
            research_result=research_result,
            approved_outline="一、集团背景",
            user_input="写成300字",
            platform_style="知乎文章",
            source_metadata={},
        )

    assert result["status"] == "blocked"
    assert "F999" in result["generator_stop_reason"]
    llm_class.assert_not_called()


@pytest.mark.asyncio
async def test_action_limit_is_a_hard_stop(plan, memory, research_result):
    content = "锐博集团创办于2002年。" + "业务内容" * 70
    with patch("forge.agents.generator_agent.MAX_GENERATOR_ACTIONS", 1):
        result, _ = await run_with_outputs(
            [decision("draft_section"), written(content)],
            plan,
            memory,
            research_result,
        )

    assert result["status"] == "needs_review"
    assert result["generator_tool_action_count"] == 1
    assert result["generator_stop_reason"] == "达到 Generator Agent 动作上限"


@pytest.mark.asyncio
async def test_writer_prompt_contains_fact_source_outline_budget_and_url(plan, memory, research_result):
    content = "锐博集团创办于2002年。" + "业务内容" * 70
    result, llm = await run_with_outputs(
        [
            decision("draft_section"),
            written(content),
            decision("accept_section"),
            decision("finish_generation", "", []),
        ],
        plan,
        memory,
        research_result,
    )

    assert result["status"] == "generated"
    writer_prompt = llm.chat_with_retry.await_args_list[1].args[0]
    assert "source=锐博官网" in writer_prompt
    assert "url=https://www.nnngu.com/group-profile" in writer_prompt
    assert "一、集团背景" in writer_prompt
    assert "目标字数：300字" in writer_prompt


@pytest.mark.asyncio
async def test_controller_can_choose_transition_tool_after_observing_second_section(
    memory,
    research_result,
):
    research_result["facts"].append({
        **research_result["facts"][0],
        "fact_id": "F002",
        "section_id": "S02",
        "claim": "锐博集团总部位于广州。",
    })
    plan = {
        "mode": "section_sequential",
        "reason": "分章生成",
        "target_chars": 400,
        "min_chars": 360,
        "max_chars": 440,
        "counting_method": "可见字符",
        "sections": [
            {
                "section_id": "S01", "title": "集团背景", "purpose": "介绍背景",
                "fact_ids": ["F001"],
                "budget": {"target_chars": 200, "min_chars": 180, "max_chars": 220},
                "actual_chars": 0, "status": "pending", "content": "", "summary": "",
                "revision_attempts": 0,
            },
            {
                "section_id": "S02", "title": "业务说明", "purpose": "说明业务",
                "fact_ids": ["F002"],
                "budget": {"target_chars": 200, "min_chars": 180, "max_chars": 220},
                "actual_chars": 0, "status": "pending", "content": "", "summary": "",
                "revision_attempts": 0,
            },
        ],
    }
    memory["remaining_target_chars"] = 400
    first = "锐博集团创办于2002年。" + "背景内容" * 43
    second = "锐博集团总部位于广州。" + "业务内容" * 43
    transitioned = "承接前文，锐博集团总部位于广州。" + "业务内容" * 43
    outputs = [
        decision("draft_section"),
        written(first),
        decision("accept_section"),
        decision("draft_section", "S02", ["F002"]),
        written(second, ["F002"]),
        decision("improve_transition", "S02", ["F002"]),
        written(transitioned, ["F002"]),
        decision("accept_section", "S02", ["F002"]),
        decision("finish_generation", "", []),
    ]

    result, llm = await run_with_outputs(outputs, plan, memory, research_result)

    assert result["status"] == "generated"
    assert "improve_transition" in [item["tool"] for item in result["generator_action_log"]]
    second_writer_prompt = llm.chat_with_retry.await_args_list[4].args[0]
    assert first[-80:] in second_writer_prompt
    assert result["writing_memory"]["section_summaries"].keys() == {"S01", "S02"}
