"""Tests for structured research evidence and fact provenance."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.agents.research_agent import (
    gather_evidence,
    plan_research_tasks,
    verify_facts,
)
from forge.agents.research_types import (
    apply_fact_reviews,
    format_verified_facts,
    validate_research_result,
)


@pytest.mark.asyncio
async def test_planner_degrades_to_bounded_tasks():
    with patch("forge.agents.research_agent.LLMClient") as llm_class:
        llm = MagicMock()
        llm.chat_with_retry = AsyncMock(side_effect=RuntimeError("planner unavailable"))
        llm_class.return_value = llm

        tasks, warnings = await plan_research_tasks(
            "一、集团背景\n二、业务范围",
            {"title": "集团概况", "text": "正文"},
            "改写",
            [],
        )

    assert len(tasks) == 2
    assert all(task["source_policy"] == "official_preferred" for task in tasks)
    assert warnings


@pytest.mark.asyncio
async def test_gather_evidence_keeps_source_chunks_and_adds_knowledge_context(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    tasks = [{
        "task_id": "T01",
        "section_id": "S01",
        "question": "核验集团创办时间",
        "required": True,
        "source_policy": "official_preferred",
        "freshness_days": None,
        "status": "pending",
    }]

    source_evidence = [{
        "evidence_id": "E001", "task_id": "source", "source": "锐博官网",
        "title": "集团概况", "url": "https://www.nnngu.com/group-profile",
        "published_at": "", "retrieved_at": "now", "excerpt": "锐博集团创办于2002年。",
        "source_type": "source_article", "authority_score": 1.0, "relevance_score": 1.0,
        "content_hash": "hash", "chunk_id": "SRC-C001", "section_path": [],
        "paragraph_index": 0, "char_start": 0, "char_end": 13,
    }]
    evidence, warnings = await gather_evidence(tasks, source_evidence, "内部知识")

    assert evidence[0]["source"] == "锐博官网"
    assert evidence[0]["url"] == "https://www.nnngu.com/group-profile"
    assert evidence[1]["url"] == "milvus://ruibo_knowledge_v2"
    assert warnings


@pytest.mark.asyncio
async def test_verified_fact_copies_source_metadata():
    tasks = [{
        "task_id": "T01",
        "section_id": "S01",
        "question": "核验创办时间",
        "required": True,
        "source_policy": "official_preferred",
        "freshness_days": None,
        "status": "completed",
    }]
    evidence = [{
        "evidence_id": "E001",
        "task_id": "T01",
        "source": "锐博官网",
        "title": "集团概况",
        "url": "https://www.nnngu.com/group-profile",
        "published_at": "",
        "retrieved_at": "2026-07-22T00:00:00+00:00",
        "excerpt": "锐博集团创办于2002年。",
        "source_type": "web",
        "authority_score": 1.0,
        "relevance_score": 1.0,
        "content_hash": "hash",
    }]
    response = """{
      "facts": [{
        "task_id": "T01", "section_id": "S01",
        "claim": "锐博集团创办于2002年。", "status": "verified",
        "confidence": 0.99, "evidence_ids": ["E001"],
        "subject": "锐博集团", "predicate": "founding_date",
        "object": "2002年", "normalized_value": "2002",
        "operator": "=", "unit": "年", "qualifiers": {},
        "fact_type": "company_fact"
      }]
    }"""
    with patch("forge.agents.research_agent.LLMClient") as llm_class:
        llm = MagicMock()
        llm.chat_with_retry = AsyncMock(return_value=response)
        llm_class.return_value = llm
        facts, warnings = await verify_facts("一、背景", tasks, evidence, [])

    assert warnings == []
    assert facts[0]["sources"][0]["source"] == "锐博官网"
    assert facts[0]["sources"][0]["url"] == "https://www.nnngu.com/group-profile"
    result = {
        "status": "complete", "tasks": tasks, "evidence": evidence,
        "facts": facts, "coverage": 1.0, "warnings": [], "generated_at": "now",
    }
    assert validate_research_result(result) == []


def test_contract_rejects_verified_fact_without_url():
    result = {
        "evidence": [{
            "evidence_id": "E001", "source": "未知", "title": "标题",
            "url": "", "retrieved_at": "now", "excerpt": "内容",
        }],
        "facts": [{
            "fact_id": "F001", "status": "verified",
            "review": {
                "decision": "accepted", "comment": "", "priority": "normal",
                "reviewed_at": "now",
            },
            "evidence_ids": ["E001"], "sources": [],
        }],
    }
    assert "evidence E001 missing url" in validate_research_result(result)
    assert "evidence-backed fact F001 has no source metadata" in validate_research_result(result)


def test_contract_rejects_source_with_unknown_evidence_id():
    result = {
        "evidence": [{
            "evidence_id": "E001", "source": "锐博官网", "title": "文章",
            "url": "https://www.nnngu.com/article", "retrieved_at": "now", "excerpt": "正文",
        }],
        "facts": [{
            "fact_id": "F001", "status": "verified", "evidence_ids": ["E001"],
            "review": {
                "decision": "accepted", "comment": "", "priority": "normal",
                "reviewed_at": "now",
            },
            "sources": [{
                "evidence_id": "E999", "source": "锐博官网", "title": "文章",
                "url": "https://www.nnngu.com/article", "retrieved_at": "now",
            }],
        }],
    }

    errors = validate_research_result(result)

    assert any("source references unknown evidence E999" in item for item in errors)


def test_contract_rejects_source_quote_with_wrong_global_offsets():
    result = {
        "evidence": [{
            "evidence_id": "E001", "source": "原文章", "title": "文章",
            "url": "https://example.com", "retrieved_at": "now",
            "excerpt": "锐博集团成立于2011年。", "char_start": 100,
        }],
        "facts": [{
            "fact_id": "F001", "status": "source_claim", "evidence_ids": ["E001"],
            "sources": [{
                "evidence_id": "E001", "source": "原文章", "title": "文章",
                "url": "https://example.com", "retrieved_at": "now",
            }],
            "review": {
                "decision": "pending", "comment": "", "priority": "normal", "reviewed_at": "",
            },
            "subject": "锐博集团", "predicate": "founding_date", "object": "2011年",
            "normalized_value": "2011", "fact_type": "company_fact",
            "evidence_spans": [{
                "evidence_id": "E001", "chunk_id": "SRC-C001",
                "quote": "成立于2011年", "char_start": 0, "char_end": 8,
            }],
        }],
    }

    assert any("invalid source offsets" in item for item in validate_research_result(result))


def test_fact_review_controls_which_fact_enters_writing():
    result = {
        "status": "complete",
        "tasks": [],
        "evidence": [],
        "facts": [
            {
                "fact_id": "F001", "task_id": "T01", "section_id": "S01",
                "claim": "可使用事实", "status": "verified", "confidence": 1.0,
                "evidence_ids": [], "sources": [],
                "review": {
                    "decision": "pending", "comment": "", "priority": "normal",
                    "reviewed_at": "",
                },
            },
            {
                "fact_id": "F002", "task_id": "T02", "section_id": "S02",
                "claim": "被排除事实", "status": "verified", "confidence": 1.0,
                "evidence_ids": [], "sources": [],
                "review": {
                    "decision": "pending", "comment": "", "priority": "normal",
                    "reviewed_at": "",
                },
            },
        ],
        "coverage": 1.0, "warnings": [], "generated_at": "now",
    }
    updated, errors = apply_fact_reviews(result, [
        {"fact_id": "F001", "action": "accepted", "comment": "", "priority": "high"},
        {"fact_id": "F002", "action": "excluded", "comment": "不写", "priority": "normal"},
    ])

    assert errors == []
    formatted = format_verified_facts(updated)
    assert "F001" in formatted
    assert "F002" not in formatted


def test_unsupported_fact_cannot_be_accepted():
    result = {
        "status": "degraded", "tasks": [], "evidence": [],
        "facts": [{
            "fact_id": "F005", "task_id": "T01", "section_id": "S01",
            "claim": "效率提升40%", "status": "unsupported", "confidence": 0.1,
            "evidence_ids": [], "sources": [],
            "review": {
                "decision": "pending", "comment": "", "priority": "normal",
                "reviewed_at": "",
            },
        }],
        "coverage": 0.0, "warnings": [], "generated_at": "now",
    }

    updated, errors = apply_fact_reviews(result, [
        {"fact_id": "F005", "action": "accepted", "comment": "", "priority": "high"},
    ])

    assert errors == ["unsupported fact F005 cannot be accepted"]
    assert updated["facts"][0]["review"]["decision"] == "pending"
