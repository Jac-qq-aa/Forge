import asyncio

import forge.memory.service as memory_module
from forge.memory.experience import build_experience_query
from forge.memory.service import (
    PreferenceMemoryService,
    _calculate_confidence,
    _make_diff_summary,
    classify_instruction,
)


def test_classifies_common_outline_preference():
    result = classify_instruction("以后大纲都删掉行业背景，直接进入问题")

    assert result.intent_key == "remove_background"
    assert result.explicit_scope is True
    assert "背景" in result.instruction


def test_unknown_instruction_gets_stable_custom_key():
    first = classify_instruction("标题里加入一个数字")
    second = classify_instruction(" 标题里加入一个数字。 ")

    assert first.intent_key == second.intent_key
    assert first.intent_key.startswith("custom_")


def test_one_time_request_is_not_treated_as_long_term_preference():
    result = classify_instruction("这篇文章都不要使用感叹号")

    assert result.explicit_scope is False


def test_repeated_success_activates_implicit_preference():
    assert _calculate_confidence(1, 1, 0, False) < 0.7
    assert _calculate_confidence(3, 3, 0, False) >= 0.7


def test_explicit_preference_is_active_after_one_success():
    assert _calculate_confidence(1, 1, 0, True) >= 0.7


def test_diff_summary_contains_changed_lines():
    summary = _make_diff_summary("一、背景\n二、问题", "一、问题")

    assert "-一、背景" in summary
    assert "-二、问题" in summary
    assert "+一、问题" in summary


class _FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _FakeAcquire(self.conn)


def test_active_rules_are_compiled_into_generation_context(monkeypatch):
    class FakeConnection:
        async def fetch(self, *args):
            return [
                {"instruction": "避免泛化背景铺垫。", "confidence": 0.82},
                {"instruction": "使用较短段落。", "confidence": 0.76},
            ]

    async def fake_get_pool():
        return _FakePool(FakeConnection())

    monkeypatch.setattr(memory_module, "get_pg_pool", fake_get_pool)
    context = asyncio.run(
        PreferenceMemoryService().get_preference_context(
            memory_owner_id="hugo",
            stage="article",
            target_platform="wechat",
            content_type="industry",
        )
    )

    assert "避免泛化背景铺垫" in context
    assert "使用较短段落" in context
    assert "本次明确要求" in context


def test_unchanged_edit_is_not_recorded():
    event_id = asyncio.run(
        PreferenceMemoryService().record_edit_event(
            memory_owner_id="hugo",
            session_id="8c529d7b-e69a-4a78-89cd-33021cb4fd37",
            stage="article",
            target_platform="wechat",
            content_type="industry",
            instruction="精简一点",
            before_text="相同内容",
            after_text="相同内容",
        )
    )

    assert event_id is None


def test_experience_query_contains_task_conditions():
    query = build_experience_query(
        {
            "target_platform": "wechat_article",
            "user_input": "开头直接进入问题",
            "outline": "一、问题\n二、方案",
            "source_article": {
                "title": "企业转型",
                "content_type": "industry_commentary",
            },
            "research_result": {
                "facts": [
                    {
                        "claim": "已确认事实",
                        "status": "verified",
                        "review": {"decision": "accepted"},
                    },
                    {
                        "claim": "被排除事实",
                        "status": "verified",
                        "review": {"decision": "excluded"},
                    },
                ]
            },
        },
        "outline",
    )

    assert "平台：wechat_article" in query
    assert "内容类型：industry_commentary" in query
    assert "已确认事实" in query
    assert "被排除事实" not in query
