from uuid import uuid4

import pytest

from forge.domain.models import ArticleFact, CriticIssue
from forge.infrastructure.model import OpenAICompatibleModel


def test_user_fact_without_evidence_is_marked_and_unverified() -> None:
    fact = ArticleFact(claim="用户提供的事实", origin="user")

    assert fact.marked is True
    assert "user_created" in fact.mark_reasons
    assert fact.verification == "unverified"


def test_critic_fingerprint_uses_rule_and_target_instead_of_wording() -> None:
    fact_id = uuid4()
    first = CriticIssue(kind="fact", rule_code="value_conflict", message="数字不一致", fact_id=fact_id)
    second = CriticIssue(kind="fact", rule_code="value_conflict", message="这个数值仍然冲突", fact_id=fact_id)

    assert first.fingerprint() == second.fingerprint()


@pytest.mark.asyncio
async def test_structured_model_rejects_response_without_json() -> None:
    model = object.__new__(OpenAICompatibleModel)

    async def fake_text(**_kwargs):
        return "没有结构化内容"

    model.text = fake_text
    with pytest.raises(ValueError, match="没有 JSON 对象"):
        await model.structured(system="", prompt="", schema={})
