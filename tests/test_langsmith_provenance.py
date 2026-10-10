from __future__ import annotations

from uuid import uuid4

import pytest

from forge.infrastructure.langsmith_trace import current_headers, current_trace_id, trusted_headers
from forge.infrastructure.telemetry import observed_node


def test_langsmith_context_is_disabled_without_opt_in(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    assert current_headers() == {}
    assert current_trace_id() is None
    assert trusted_headers({"LangSmith-Trace": "parent", "Authorization": "secret"}) == {
        "langsmith-trace": "parent"
    }


@pytest.mark.asyncio
async def test_generator_and_critic_keep_versioned_business_provenance():
    fact_id, paragraph_id, section_id, evidence_id = [str(uuid4()) for _ in range(4)]
    paragraph = {"paragraph_id": paragraph_id, "section_id": section_id,
                 "fact_ids": [fact_id], "content": "公司提供服务。"}
    state = {"conversation_id": str(uuid4()), "identity": {"user_id": str(uuid4())},
             "facts": [{"fact_id": fact_id, "version": 2, "claim": "公司提供服务",
                        "decision_id": str(uuid4()), "evidence": [{"evidence_id": evidence_id,
                        "source_kind": "database", "title": "公司业务", "quote": "提供服务",
                        "url": "", "metadata": {"tables": ["business_offerings"]}}]}],
             "draft_history": [], "review_history": []}

    async def generator(_state):
        return {"draft_changed": True, "draft_paragraphs": [paragraph]}

    generated = await observed_node(generator, None)(state, {})
    snapshot = generated["draft_history"][0]
    ref = snapshot["fact_refs"][paragraph_id][0]
    assert ref["fact_id"] == fact_id and ref["version"] == 2
    assert ref["evidence"][0]["metadata"]["tables"] == ["business_offerings"]

    async def critic(_state):
        return {"critic_status": "pass", "critic_failures": 0, "critic_issues": [],
                "fact_checks": [{"fact_id": fact_id, "status": "accept"}]}

    reviewed = await observed_node(critic, None)({**state, **generated}, {})
    report = reviewed["review_history"][0]
    assert report["snapshot_id"] == snapshot["snapshot_id"]
    assert report["fact_checks"][0]["check_id"]
