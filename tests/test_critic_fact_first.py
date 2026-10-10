from __future__ import annotations

import json
from uuid import uuid4

import pytest

from forge.domain.models import Identity
from forge.workflow.nodes import WorkflowNodes


class FactFirstModel:
    def __init__(self) -> None:
        self.revisions: list[str] = []
        self.omission_rechecks = 0

    async def structured(self, *, system, prompt, schema):
        data = json.loads(prompt)
        if "依据已确认事实逐条核对文章" in system:
            if data.get("recheck"):
                self.omission_rechecks += 1
            fact, paragraph = data["fact"], data["target_paragraph"]
            claim = fact["claim"]
            content = paragraph["content"]
            if "错误" in content:
                return {"decision": "refuse", "quote": content, "reason": "与事实冲突",
                        "requires_user": True}
            if claim in content:
                return {"decision": "accept", "quote": claim, "reason": "准确", "requires_user": False}
            return {"decision": "missing", "quote": "", "reason": "未写入", "requires_user": False}
        if "逐段摘录所有可核查" in system:
            claims = []
            for paragraph in data["paragraphs"]:
                for claim in ("事实一", "事实二"):
                    if claim in paragraph["content"]:
                        claims.append({"claim": claim, "paragraph_id": paragraph["paragraph_id"]})
            return {"claims": claims}
        if "逐条判断正文声明" in system:
            matched = next(fact for fact in data["facts"] if fact["claim"] == data["draft_claim"])
            return {"decision": "accept", "fact_id": matched["fact_id"],
                    "reason": "一致", "expected_fact": matched["claim"], "requires_user": False}
        if "检查全文" in system:
            return {"issues": [], "new_facts": []}
        raise AssertionError(system)

    async def text(self, *, system, prompt):
        data = json.loads(prompt)
        self.revisions.append(data["target_paragraph"]["paragraph_id"])
        return data["correct_facts"][0]


class EmptyCompany:
    async def ask(self, question, *, identity):
        return []


class EmptySearch:
    async def web(self, question, *, limit=5):
        return []


def article_state(second_content: str):
    section_id = str(uuid4())
    paragraphs = [{"paragraph_id": str(uuid4()), "section_id": section_id,
                   "section_order": 1, "paragraph_order": order,
                   "fact_ids": [], "content": content}
                  for order, content in enumerate(("事实一。", second_content), start=1)]
    facts = [{"fact_id": str(uuid4()), "version": 1, "claim": claim,
              "origin": "user", "marked": True, "required_for_article": True}
             for claim in ("事实一", "事实二")]
    for paragraph, fact in zip(paragraphs, facts):
        paragraph["fact_ids"] = [fact["fact_id"]]
    outline = [{"section_id": section_id, "order": 1, "title": "正文", "purpose": "说明事实",
                "main_points": ["事实"], "fact_ids": [item["fact_id"] for item in facts],
                "paragraphs": [{"paragraph_id": paragraph["paragraph_id"], "order": paragraph["paragraph_order"],
                                "purpose": "说明", "main_points": ["事实"],
                                "fact_ids": paragraph["fact_ids"], "required_fact_ids": paragraph["fact_ids"]}
                               for paragraph in paragraphs]}]
    return {"identity": Identity(user_id=uuid4()).model_dump(mode="json"),
            "facts": facts, "baseline_facts": facts, "outline": outline,
            "draft_paragraphs": paragraphs, "draft": "\n\n".join(item["content"] for item in paragraphs),
            "critic_failures": 0, "issue_history": [], "fact_version": 1}


@pytest.mark.asyncio
async def test_missing_confirmed_fact_is_revised_only_in_its_outline_paragraph():
    model = FactFirstModel()
    nodes = WorkflowNodes(model=model, researcher=None, company=EmptyCompany(), search=EmptySearch())
    state = article_state("空白段落。")
    first = await nodes.critic(state)
    assert first["critic_status"] == "fail"
    assert model.omission_rechecks == 1
    assert [item["decision"] for item in first["fact_checks"] if item["check_type"] == "baseline"] == ["accept", "missing"]
    issue = first["critic_issues"][0]
    assert issue["fact_id"] == state["facts"][1]["fact_id"]
    assert issue["paragraph_id"] == state["draft_paragraphs"][1]["paragraph_id"]
    revised = await nodes.revise({**state, **first})
    assert model.revisions == [state["draft_paragraphs"][1]["paragraph_id"]]
    assert revised["draft_paragraphs"][0]["content"] == "事实一。"
    assert revised["draft_paragraphs"][1]["content"] == "事实二"
    second = await nodes.critic({**state, **first, **revised})
    assert second["critic_status"] == "pass"


@pytest.mark.asyncio
async def test_conflicting_user_fact_is_sent_to_user_with_paragraph_location():
    state = article_state("事实二错误。")
    nodes = WorkflowNodes(model=FactFirstModel(), researcher=None,
                          company=EmptyCompany(), search=EmptySearch())
    result = await nodes.critic(state)
    assert result["critic_status"] == "needs_user"
    assert result["pending_conflicts"][0]["expected_fact"] == "事实二"
    assert result["pending_conflicts"][0]["paragraph_id"] == state["draft_paragraphs"][1]["paragraph_id"]


@pytest.mark.asyncio
async def test_keeping_user_fact_does_not_accept_wrong_generated_sentence():
    state = article_state("事实二错误。")
    fact = state["facts"][1]
    state["user_decisions"] = [{"fact_id": fact["fact_id"],
                                "fact_version": fact["version"], "action": "keep"}]
    nodes = WorkflowNodes(model=FactFirstModel(), researcher=None,
                          company=EmptyCompany(), search=EmptySearch())
    result = await nodes.critic(state)
    assert result["critic_status"] == "fail"
    assert result["critic_issues"][0]["rule_code"] == "baseline_refuse"
    assert result["critic_issues"][0]["requires_user"] is False


@pytest.mark.asyncio
async def test_third_distinct_failed_review_stops_revision_loop():
    nodes = WorkflowNodes(model=FactFirstModel(), researcher=None,
                          company=EmptyCompany(), search=EmptySearch())
    state = article_state("空白段落 0。")
    for attempt in range(1, 4):
        state["draft_paragraphs"][1]["content"] = f"空白段落 {attempt}。"
        state["draft"] = "\n\n".join(item["content"] for item in state["draft_paragraphs"])
        result = await nodes.critic(state)
        assert result["critic_failures"] == attempt
        assert result["critic_status"] == ("stopped" if attempt == 3 else "fail")
        state = {**state, **result}


@pytest.mark.asyncio
async def test_repeated_unfixed_issue_stops_after_recheck():
    nodes = WorkflowNodes(model=FactFirstModel(), researcher=None,
                          company=EmptyCompany(), search=EmptySearch())
    state = article_state("空白段落。")
    first = await nodes.critic(state)
    second = await nodes.critic({**state, **first})
    assert first["critic_status"] == "fail"
    assert second["critic_status"] == "stopped"
    assert "重复" in second["stop_reason"]
