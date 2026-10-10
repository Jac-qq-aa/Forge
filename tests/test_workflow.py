from __future__ import annotations

from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from forge.domain.models import Evidence, Identity
from forge.knowledge.research import EvidenceResearcher
from forge.workflow.graph import build_workflow
from forge.workflow.nodes import WorkflowNodes


class FakeModel:
    def __init__(self, *, conflict: bool = False):
        self.conflict = conflict

    async def structured(self, *, system, prompt, schema):
        if "抽取原子事实" in system:
            return {"facts": [{"claim": "锐博成立于2002年", "subject": "锐博", "predicate": "成立于", "value": "2002", "unit": "年", "quote": "锐博成立于2002年"}]}
        if "逐段大纲" in system:
            import json
            facts = json.loads(prompt)["facts"]
            return {"sections": [{"title": "起点", "purpose": "介绍背景", "main_points": ["成立时间"], "fact_ids": [facts[0]["fact_id"]]}]}
        if "检查正文" in system:
            return {"issues": [], "new_facts": []}
        if "比较用户事实" in system:
            return {"conflict": self.conflict, "reason": "数据库记录与用户事实不一致"}
        return {"intent": "question"}

    async def text(self, *, system, prompt):
        if "公司知识问题" in system:
            return "根据公司资料：锐博成立于2002年。"
        return "# 起点\n\n锐博成立于2002年。"


class FakeSearch:
    async def web(self, query, *, limit=5):
        return [Evidence(source_kind="web", quote="公开资料补充", url="https://example.com")]

    async def semantic(self, query, *, identity, limit=8):
        return [Evidence(source_kind="company_document", quote="历史文档补充")]


class FakeCompany:
    async def ask(self, question, *, identity):
        return [Evidence(source_kind="database", quote="数据库事实")]


def make_graph(*, conflict: bool = False):
    model = FakeModel(conflict=conflict)
    search = FakeSearch()
    company = FakeCompany()
    nodes = WorkflowNodes(
        model=model,
        researcher=EvidenceResearcher(model, search, company),
        search=search,
        company=company,
    )
    return build_workflow(nodes).compile(checkpointer=InMemorySaver())


@pytest.mark.asyncio
async def test_generation_automatically_approves_versioned_plan_and_finishes() -> None:
    graph = make_graph()
    conversation_id = str(uuid4())
    identity = Identity(user_id=uuid4()).model_dump(mode="json")
    config = {"configurable": {"thread_id": conversation_id}}
    first = await graph.ainvoke(
        {
            "conversation_id": conversation_id,
            "article_id": str(uuid4()),
            "identity": identity,
            "source_text": "锐博成立于2002年。",
            "direction": "沿用原文主题与方向",
            "user_message": "生成文章",
            "requested_operation": "generate",
            "critic_failures": 0,
            "issue_history": [],
        },
        config,
    )
    assert first["stage"] == "ready"
    assert not first.get("__interrupt__")
    assert first["approval"]["actor_kind"] == "system"
    assert first["approval"]["fact_version"] == 1
    assert "锐博成立于2002年" in first["draft"]
    assert first["facts_changed"] is True


@pytest.mark.asyncio
async def test_question_branch_does_not_change_article_draft() -> None:
    graph = make_graph()
    conversation_id = str(uuid4())
    result = await graph.ainvoke(
        {
            "conversation_id": conversation_id,
            "identity": Identity(user_id=uuid4()).model_dump(mode="json"),
            "user_message": "锐博是什么时候成立的？",
            "draft": "已有文章",
        },
        {"configurable": {"thread_id": conversation_id}},
    )
    assert result["stage"] == "answered"
    assert result["draft"] == "已有文章"
    assert result["draft_changed"] is False


@pytest.mark.asyncio
async def test_user_conflict_is_kept_without_falsely_verifying():
    graph = make_graph(conflict=True)
    fact_id = str(uuid4())
    result = await graph.ainvoke({
        "conversation_id": str(uuid4()), "identity": Identity(user_id=uuid4()).model_dump(mode="json"),
        "requested_operation": "modify", "fact_version": 2,
        "facts": [{"fact_id": fact_id, "version": 1, "claim": "锐博成立于2003年", "origin": "user",
                   "marked": True, "mark_reasons": ["user_edited"]}],
    }, {"configurable": {"thread_id": str(uuid4())}})
    assert result["stage"] == "ready"
    assert result["facts"][0]["claim"] == "锐博成立于2003年"
    assert result["facts"][0]["verification"] == "conflicted"
    assert result["system_decisions"][0]["actor_kind"] == "system"
    assert not result.get("__interrupt__")


@pytest.mark.asyncio
@pytest.mark.parametrize("facts,outline", [([], []), ([{"claim": "事实"}], [])])
async def test_empty_plan_never_auto_approves(facts, outline):
    nodes = WorkflowNodes(model=FakeModel(), researcher=None, search=FakeSearch(), company=FakeCompany())
    with pytest.raises(ValueError):
        await nodes.approval({"facts": facts, "outline": outline, "fact_version": 1, "outline_version": 1})


@pytest.mark.asyncio
async def test_repeated_nonmarked_issue_stops_after_revision():
    class RepeatingModel(FakeModel):
        async def structured(self, **kwargs):
            if "检查正文" in kwargs["system"]:
                return {"issues": [{"kind": "style", "rule_code": "repeated", "message": "重复问题", "requires_user": True}], "new_facts": []}
            return await super().structured(**kwargs)
    model, search, company = RepeatingModel(), FakeSearch(), FakeCompany()
    graph = build_workflow(WorkflowNodes(model=model, researcher=EvidenceResearcher(model,search,company), search=search, company=company)).compile()
    result = await graph.ainvoke({"identity": Identity(user_id=uuid4()).model_dump(mode="json"),
        "requested_operation": "generate", "source_text": "锐博成立于2002年。"})
    assert result["critic_status"] == "stopped"
    assert result["critic_failures"] == 2
    assert "重复" in result["stop_reason"]


@pytest.mark.asyncio
async def test_distinct_failures_stop_at_three():
    class ChangingModel(FakeModel):
        reviews = 0
        async def structured(self, **kwargs):
            if "检查正文" in kwargs["system"]:
                self.reviews += 1
                return {"issues": [{"kind": "style", "rule_code": f"rule_{self.reviews}", "message": "问题"}], "new_facts": []}
            return await super().structured(**kwargs)
    model, search, company = ChangingModel(), FakeSearch(), FakeCompany()
    graph = build_workflow(WorkflowNodes(model=model, researcher=EvidenceResearcher(model,search,company), search=search, company=company)).compile()
    result = await graph.ainvoke({"identity": Identity(user_id=uuid4()).model_dump(mode="json"),
        "requested_operation": "generate", "source_text": "锐博成立于2002年。"})
    assert result["critic_status"] == "stopped"
    assert result["critic_failures"] == model.reviews == 3


@pytest.mark.asyncio
async def test_critic_marked_conflicts_auto_pass_with_audit_and_unmarked_issues_still_stop():
    from forge.domain.models import ArticleFact
    fact = ArticleFact(claim="用户事实", origin="user", marked=True)
    class ConflictModel(FakeModel):
        async def structured(self, **kwargs):
            if "检查正文" in kwargs["system"]:
                return {"issues": [
                    {"kind": "fact", "fact_id": str(fact.fact_id), "message": "冲突"},
                    {"kind": "style", "rule_code": "style", "message": "表达问题"},
                ], "new_facts": []}
            return await super().structured(**kwargs)
    model, search, company = ConflictModel(), FakeSearch(), FakeCompany()
    graph = build_workflow(WorkflowNodes(model=model, researcher=None, search=search, company=company)).compile()
    result = await graph.ainvoke({"identity": Identity(user_id=uuid4()).model_dump(mode="json"),
        "requested_operation": "modify", "facts": [fact.model_dump(mode="json")], "fact_version": 1})
    assert result["critic_status"] == "stopped"
    assert result["critic_failures"] == 2
    assert result["system_decisions"][0]["action"] == "keep"
    assert result["facts"][0]["verification"] == "conflicted"
    assert not result.get("__interrupt__")
