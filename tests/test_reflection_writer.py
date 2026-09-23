"""Tests for adaptive evidence-grounded writing and length control."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.agents.reflection_writer import (
    build_generation_plan,
    build_reflection_graph,
    count_visible_chars,
    critic_node,
    deterministic_checks,
    match_draft_facts,
    generator_node,
    reviser_node,
    run_reflection_writer,
    summarize_fact_matches,
)


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
            "retrieved_at": "2026-07-22T00:00:00+00:00",
            "excerpt": "锐博集团创办于2002年，总部位于广州。",
            "source_type": "source_article",
            "authority_score": 1.0,
            "relevance_score": 1.0,
            "content_hash": "hash",
        }],
        "facts": [{
            "fact_id": "F001",
            "task_id": "source",
            "section_id": "S01",
            "claim": "锐博集团创办于2002年，总部位于广州。",
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
                "retrieved_at": "2026-07-22T00:00:00+00:00",
            }],
        }],
        "coverage": 1.0,
        "warnings": [],
        "generated_at": "2026-07-22T00:00:00+00:00",
    }


def make_generator_result(plan, draft):
    sections = plan["sections"][:1] if plan["mode"] == "single_pass" else plan["sections"]
    if plan["mode"] == "single_pass":
        sections[0]["title"] = "全文"
        sections[0]["budget"] = {
            "target_chars": plan["target_chars"],
            "min_chars": plan["min_chars"],
            "max_chars": plan["max_chars"],
        }
        plan["sections"] = sections
    sections[0].update({
        "content": draft,
        "actual_chars": count_visible_chars(draft),
        "status": "within_budget",
        "summary": draft[:180],
    })
    return {
        "status": "generated",
        "draft": draft,
        "generation_plan": plan,
        "section_reports": sections,
        "writing_memory": {
            "thesis": "测试", "terminology": {},
            "used_fact_ids": sections[0]["fact_ids"],
            "section_summaries": {sections[0]["section_id"]: draft[:180]},
            "open_threads": [], "previous_tail": draft[-400:],
            "actual_chars": count_visible_chars(draft),
            "remaining_target_chars": max(0, plan["target_chars"] - count_visible_chars(draft)),
        },
        "generator_action_log": [],
        "generator_tool_action_count": 3,
        "generator_model_call_count": 4,
        "generator_stop_reason": "finish_generation",
        "errors": [],
    }


def test_count_visible_chars_excludes_url_and_markdown():
    assert count_visible_chars("## 标题\n正文123 https://example.com/a") == 7


def test_short_article_uses_single_pass(research_result):
    plan = build_generation_plan(
        "一、背景\n二、结论", research_result, "控制在800字", "zhihu_article", {}
    )

    assert plan["mode"] == "single_pass"
    assert plan["target_chars"] == 800


def test_short_article_with_many_headings_still_avoids_fragmentation(research_result):
    outline = "\n".join(f"{index}. 章节{index}" for index in range(1, 9))
    plan = build_generation_plan(
        outline, research_result, "控制在900字", "zhihu_article", {}
    )

    assert plan["mode"] == "single_pass"


def test_section_budgets_sum_to_total_target(research_result):
    plan = build_generation_plan(
        "一、背景\n二、业务\n三、案例\n四、总结",
        research_result,
        "写成2000字",
        "zhihu_article",
        {},
    )

    assert plan["mode"] == "section_sequential"
    assert sum(item["budget"]["target_chars"] for item in plan["sections"]) == 2000
    assert all(item["budget"]["min_chars"] < item["budget"]["max_chars"] for item in plan["sections"])


def test_few_complex_sections_use_hybrid_mode(research_result):
    plan = build_generation_plan(
        "一、背景\n二、完整招录流程",
        research_result,
        "写成1800字",
        "zhihu_article",
        {},
    )

    assert plan["mode"] == "hybrid"
    assert len(plan["sections"]) == 2


def test_deterministic_checks_reject_unsupported_number_and_length(research_result):
    issues, log = deterministic_checks(
        "锐博集团创办于2002年，目前拥有9999家公司。",
        research_result,
        {"text": "锐博集团创办于2002年。"},
        {
            "target_chars": 800, "min_chars": 760, "max_chars": 840,
            "actual_chars": 30, "deviation": -0.962, "status": "too_short",
        },
    )

    assert any(issue["issue_type"] == "unsupported_number" for issue in issues)
    assert any(issue["issue_type"] == "word_count_too_short" for issue in issues)
    assert any("unsupported_numbers=1" in item for item in log)


def test_fact_match_detects_same_relation_with_different_year(research_result):
    draft_facts = [{
        "draft_fact_id": "DF001",
        "claim": "锐博集团成立在2022年",
        "span": "锐博集团成立在2022年",
        "section_id": "S01",
        "subject": "锐博集团",
        "predicate": "founding_date",
        "value": "2022年",
        "normalized_value": "2022",
        "value_type": "year",
        "unit": "年",
        "qualifiers": {},
        "matched_fact_ids": ["F001"],
        "match_status": "not_verifiable",
        "confidence": 0.99,
        "conflicting_fields": [],
    }]

    matched = match_draft_facts(draft_facts, research_result)

    assert matched[0]["match_status"] == "contradicted"
    assert matched[0]["conflicting_fields"] == ["value"]
    assert summarize_fact_matches(matched)["contradicted"] == 1


@pytest.mark.asyncio
async def test_critic_uses_structured_fact_match_as_hard_grounding_gate(research_result):
    extraction_json = """{
      "draft_facts": [{
        "claim": "锐博集团成立在2022年",
        "span": "锐博集团成立在2022年",
        "section_id": "S01",
        "subject": "锐博集团",
        "predicate": "成立在",
        "value": "2022年",
        "value_type": "year",
        "matched_fact_ids": ["F001"],
        "match_status": "not_verifiable",
        "confidence": 0.99
      }]
    }"""
    critic_json = """{
      "scores": {"grounding": 1.0, "completeness": 1.0, "structure": 1.0, "style": 1.0},
      "issues": []
    }"""
    with patch("forge.agents.reflection_writer.LLMClient") as llm_class:
        llm = MagicMock()
        llm.chat_with_retry = AsyncMock(side_effect=[extraction_json, critic_json])
        llm_class.return_value = llm
        result = await critic_node({
            "current_draft": "锐博集团成立在2022年。",
            "research_result": research_result,
            "outline": "一、集团背景",
            "raw_content": {"text": "锐博集团创办于2002年。"},
            "target_platform": "zhihu_article",
        })

    assert result["approved"] is False
    assert result["scores"]["grounding"] <= 0.59
    assert result["fact_match_summary"]["contradicted"] == 1
    assert any(issue["issue_type"] == "contradiction" for issue in result["issues"])


@pytest.mark.asyncio
async def test_critic_failure_never_auto_approves(research_result):
    with patch("forge.agents.reflection_writer.LLMClient") as llm_class:
        llm = MagicMock()
        llm.chat_with_retry = AsyncMock(side_effect=RuntimeError("critic unavailable"))
        llm_class.return_value = llm
        result = await critic_node({
            "current_draft": "锐博集团创办于2002年。",
            "research_result": research_result,
            "outline": "一、集团背景",
            "raw_content": {"text": "锐博集团创办于2002年。"},
            "target_platform": "zhihu_article",
        })

    assert result["approved"] is False
    assert result["status"] == "needs_review"
    assert any(issue["issue_type"] == "critic_error" for issue in result["issues"])


@pytest.mark.asyncio
async def test_generator_hard_boundary_failure_requires_human_review(research_result):
    critic_json = """{
      "draft_facts": [],
      "scores": {"grounding": 1.0, "completeness": 1.0, "structure": 1.0, "style": 1.0},
      "issues": []
    }"""
    with patch("forge.agents.reflection_writer.LLMClient") as llm_class:
        llm = MagicMock()
        llm.chat_with_retry = AsyncMock(side_effect=[
            '{"draft_facts": []}', critic_json,
        ])
        llm_class.return_value = llm
        result = await critic_node({
            "current_draft": "部分草稿",
            "research_result": research_result,
            "outline": "一、集团背景",
            "raw_content": {"text": "锐博集团创办于2002年。"},
            "issues": [{
                "issue_id": "I001",
                "issue_type": "generator_agent_error",
                "severity": "high",
                "span": "",
                "fact_id": "",
                "instruction": "达到动作上限",
            }],
        })

    assert result["approved"] is False
    assert result["status"] == "needs_review"


def test_reflection_graph_has_adaptive_quality_loop():
    graph = build_reflection_graph()
    assert set(graph.nodes) == {"planning", "generator", "critic", "reviser"}


@pytest.mark.asyncio
async def test_generator_node_delegates_to_controlled_agent(research_result):
    async def agent_side_effect(**kwargs):
        return make_generator_result(kwargs["generation_plan"], "正文" * 150)

    with patch(
        "forge.agents.reflection_writer.run_generator_agent",
        new=AsyncMock(side_effect=agent_side_effect),
    ) as agent:
        result = await generator_node({
            "research_result": research_result,
            "outline": "一、集团背景",
            "raw_content": {"title": "集团概况", "url": "https://example.com/source"},
            "target_platform": "zhihu_article",
            "user_input": "写成300字",
        })

    agent.assert_awaited_once()
    call = agent.await_args.kwargs
    assert call["approved_outline"] == "一、集团背景"
    assert call["source_metadata"]["url"] == "https://example.com/source"
    assert result["generator_tool_action_count"] == 3
    assert result["generator_model_call_count"] == 4
    assert result["generator_stop_reason"] == "finish_generation"


@pytest.mark.asyncio
async def test_reviser_node_delegates_to_revision_agent(research_result):
    plan = build_generation_plan(
        "一、背景\n二、业务", research_result, "写成1300字", "zhihu_article", {}
    )
    for section in plan["sections"]:
        section["content"] = "原章节"
        section["actual_chars"] = 600
        section["status"] = "within_budget"
    agent_result = {
        "status": "revised", "draft": "Revision Agent 修改稿",
        "generation_plan": plan, "section_reports": plan["sections"],
        "writing_memory": {
            "thesis": "测试", "terminology": {}, "used_fact_ids": ["F001"],
            "section_summaries": {}, "open_threads": [], "previous_tail": "",
            "actual_chars": 10, "remaining_target_chars": 1290,
        },
        "revision_plan": {"revision_cycle": 1, "source": "critic", "tasks": [], "max_actions": 8},
        "revision_action_log": [], "issue_resolution_log": [],
        "tool_action_count": 0, "unresolved_issues": [], "errors": [],
    }
    with patch(
        "forge.agents.reflection_writer.run_revision_agent",
        new=AsyncMock(return_value=agent_result),
    ) as agent:
        result = await reviser_node({
            "current_draft": "旧全文",
            "research_result": research_result,
            "outline": "一、背景\n二、业务",
            "generation_plan": plan,
            "section_reports": plan["sections"],
            "raw_content": {"text": "原文"},
            "writing_memory": agent_result["writing_memory"],
            "issues": [{
                "issue_id": "I001", "issue_type": "style", "severity": "medium",
                "span": "业务", "fact_id": "", "instruction": "调整风格",
            }],
            "revision_attempts": 0,
        })

    agent.assert_awaited_once()
    assert result["current_draft"] == "Revision Agent 修改稿"
    assert result["revision_cycles"] == 1
    assert result["status"] == "revised"


@pytest.mark.asyncio
async def test_run_reflection_writer_returns_plan_memory_and_word_report(research_result):
    draft = "锐博集团位于广州" * 35
    critic_json = """{
      "draft_facts": [],
      "scores": {"grounding": 0.98, "completeness": 0.92, "structure": 0.88, "style": 0.86},
      "issues": []
    }"""
    async def generator_side_effect(**kwargs):
        return make_generator_result(kwargs["generation_plan"], draft)

    with patch("forge.agents.reflection_writer.LLMClient") as llm_class, patch(
        "forge.agents.reflection_writer.run_generator_agent",
        new=AsyncMock(side_effect=generator_side_effect),
    ):
        llm = MagicMock()
        llm.chat_with_retry = AsyncMock(side_effect=[
            '{"draft_facts": []}', critic_json,
        ])
        llm_class.return_value = llm
        result = await run_reflection_writer(
            research_result=research_result,
            outline="一、集团背景\n二、业务概况",
            raw_content={
                "title": "集团概况",
                "text": "锐博集团创办于2002年，总部位于广州。",
                "url": "https://www.nnngu.com/group-profile",
            },
            user_input="写成300字",
        )

    assert result["status"] == "approved"
    assert result["approved"] is True
    assert result["generation_plan"]["mode"] == "single_pass"
    assert result["word_count"]["status"] == "within_budget"
    assert result["section_reports"]
    assert result["writing_memory"]["actual_chars"] > 0
    assert result["revision_plan"]["tasks"] == []
    assert result["revision_action_log"] == []
    assert result["tool_action_count"] == 0
    assert result["generator_tool_action_count"] == 3
    assert result["generator_model_call_count"] == 4


@pytest.mark.asyncio
async def test_reflection_loop_uses_revision_agent_and_rechecks_critic(research_result):
    draft = "锐博集团位于广州" * 35
    extraction_json = '{"draft_facts": []}'
    first_critic = """{
      "draft_facts": [],
      "scores": {"grounding": 0.98, "completeness": 0.90, "structure": 0.85, "style": 0.50},
      "issues": [{
        "issue_type":"style", "severity":"medium", "span":"全文",
        "fact_id":"", "instruction":"调整为更自然的知乎文章风格"
      }]
    }"""
    second_critic = """{
      "draft_facts": [],
      "scores": {"grounding": 0.98, "completeness": 0.92, "structure": 0.88, "style": 0.90},
      "issues": []
    }"""
    async def generator_side_effect(**kwargs):
        return make_generator_result(kwargs["generation_plan"], draft)

    with patch("forge.agents.reflection_writer.LLMClient") as writer_llm_class, patch(
        "forge.agents.revision_agent.LLMClient"
    ) as revision_llm_class, patch(
        "forge.agents.reflection_writer.run_generator_agent",
        new=AsyncMock(side_effect=generator_side_effect),
    ):
        writer_llm = MagicMock()
        writer_llm.chat_with_retry = AsyncMock(side_effect=[
            extraction_json, first_critic, extraction_json, second_critic,
        ])
        writer_llm_class.return_value = writer_llm
        revision_llm = MagicMock()
        revision_llm.chat_with_retry = AsyncMock(return_value=draft)
        revision_llm_class.return_value = revision_llm
        result = await run_reflection_writer(
            research_result=research_result,
            outline="一、集团背景",
            raw_content={"title": "集团概况", "text": "锐博集团位于广州。"},
            user_input="写成300字",
        )

    assert result["status"] == "approved"
    assert result["revision_cycles"] == 1
    assert result["revision_plan"]["source"] == "critic"
    assert result["revision_action_log"][0]["tool"] == "adjust_tone"
    assert result["issue_resolution_log"][0]["validation"]["final_semantic_review"] == "passed"
