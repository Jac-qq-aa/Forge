from __future__ import annotations

import asyncio
import json
import logging
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from forge.domain.models import Identity
from forge.domain.models import Evidence
from forge.infrastructure.telemetry import (
    JsonFormatter, emit, observed_node, span, trace_context, traced,
)
from forge.knowledge.text2sql import SkillResourceRouter
from forge.knowledge.text2sql import Text2SqlSkill, SkillLoader, QueryPolicy, ReadOnlySqlGuard
from forge.workflow.graph import build_workflow
from forge.workflow.nodes import WorkflowNodes
from forge.knowledge.research import EvidenceResearcher
from test_workflow import FakeModel, FakeSearch, FakeCompany


class Recorder:
    def __init__(self):
        self.events = []

    async def record(self, data, identity):
        self.events.append(dict(data))


@pytest.mark.asyncio
async def test_concurrent_trace_contexts_are_isolated_and_link_child_spans():
    recorder = Recorder()
    async def execute(trace):
        with trace_context(recorder=recorder, trace_id=trace, conversation_id=str(uuid4()),
                           identity=Identity(user_id=uuid4()).model_dump(mode="json")):
            async with span("parent"):
                await asyncio.sleep(0)
                async with span("child"):
                    await emit("test.event")
    first, second = str(uuid4()), str(uuid4())
    await asyncio.gather(execute(first), execute(second))
    assert {item["trace_id"] for item in recorder.events} == {first, second}
    for trace in (first, second):
        events = [item for item in recorder.events if item["trace_id"] == trace]
        parent = next(item for item in events if item.get("name") == "parent")
        child = next(item for item in events if item.get("name") == "child")
        assert child["parent_span_id"] == parent["span_id"]
        assert "identity" not in child


@pytest.mark.asyncio
async def test_node_failure_has_safe_stack_without_secret_message():
    recorder = Recorder()
    async def broken(state):
        raise RuntimeError("secret-api-key 原文 SQL SELECT private")
    node = observed_node(broken, recorder)
    with pytest.raises(RuntimeError):
        await node({"conversation_id": str(uuid4()), "identity": Identity(user_id=uuid4()).model_dump(mode="json")},
                   {"configurable": {"forge_trace_id": str(uuid4()), "forge_job_id": str(uuid4())}})
    failure = recorder.events[-1]
    assert failure["status"] == "failed"
    assert failure["error_type"] == "RuntimeError"
    assert failure["stack"]
    assert "secret-api-key" not in json.dumps(recorder.events)


@pytest.mark.asyncio
async def test_recorder_failure_does_not_break_business_result():
    class BrokenRecorder:
        async def record(self, data, identity):
            raise ConnectionError("secret DSN")
    async def success(state):
        return {"stage": "ready"}
    result = await observed_node(success, BrokenRecorder())(
        {"conversation_id": str(uuid4()), "identity": Identity(user_id=uuid4()).model_dump(mode="json")}, {})
    assert result == {"stage": "ready"}


@pytest.mark.asyncio
async def test_automatic_graph_retains_trace_and_records_approval():
    recorder = Recorder()
    model, search, company = FakeModel(), FakeSearch(), FakeCompany()
    nodes = WorkflowNodes(model=model, search=search, company=company,
                          researcher=EvidenceResearcher(model, search, company))
    graph = build_workflow(nodes, recorder=recorder).compile(checkpointer=InMemorySaver())
    conversation, first_job, second_job = str(uuid4()), str(uuid4()), str(uuid4())
    first_trace, second_trace = str(uuid4()), str(uuid4())
    config = {"configurable": {"thread_id": conversation, "forge_job_id": first_job, "forge_trace_id": first_trace}}
    first = await graph.ainvoke({"conversation_id": conversation,
        "identity": Identity(user_id=uuid4()).model_dump(mode="json"),
        "source_text": "锐博成立于2002年。", "user_message": "生成", "requested_operation": "generate"}, config)
    assert first["stage"] == "ready"
    assert not first.get("__interrupt__")
    assert first["trace_id"] == first_trace and first["job_id"] == first_job
    generator = next(item for item in recorder.events if item.get("name") == "generator")
    assert generator["job_id"] == first_job and generator["trace_id"] == first_trace
    assert any(item["event"] == "plan.approved" for item in recorder.events)
    assert not any(item["status"] == "failed" for item in recorder.events)


def test_json_formatter_does_not_expose_exception_message():
    try:
        raise ValueError("secret credential")
    except ValueError as error:
        record = logging.LogRecord("forge", logging.ERROR, __file__, 1, "operation failed", (),
                                   (ValueError, error, error.__traceback__))
    rendered = JsonFormatter().format(record)
    assert "secret credential" not in rendered
    assert json.loads(rendered)["error_type"] == "ValueError"


@pytest.mark.asyncio
async def test_skill_router_failure_records_explicit_fallback():
    class BrokenModel:
        async def structured(self, **kwargs):
            raise RuntimeError("secret input")
    recorder = Recorder()
    router = SkillResourceRouter()
    with trace_context(recorder=recorder, conversation_id=str(uuid4()),
                       identity=Identity(user_id=uuid4()).model_dump(mode="json")):
        selected = await router.select_with_model("未知内容", BrokenModel())
    assert selected == router.all_resources
    fallback = next(item for item in recorder.events if item["event"] == "fallback.used")
    assert fallback["reason"] == "router_model_failed"
    assert "secret input" not in json.dumps(recorder.events)


@pytest.mark.asyncio
async def test_sql_execution_failure_records_unverified_md_fallback(tmp_path):
    class Model:
        async def structured(self, **kwargs):
            return {"sql": "SELECT legal_name FROM organizations"}
    async def failing_execute(sql, identity):
        raise ConnectionError("secret database data")
    for resource in ("company_relations", "schema_company"):
        (tmp_path / f"{resource}.md").write_text("# 公司关系\n\n锐博子公司通过组织关系表查询。", encoding="utf-8")
    skill = Text2SqlSkill(model=Model(), loader=SkillLoader(tmp_path),
        guard=ReadOnlySqlGuard(QueryPolicy(tables={"organizations": frozenset({"legal_name"})})),
        execute=failing_execute)
    recorder = Recorder()
    identity = Identity(user_id=uuid4())
    with trace_context(recorder=recorder, conversation_id=str(uuid4()), identity=identity.model_dump(mode="json")):
        evidence = await skill.ask("锐博子公司有哪些？", identity=identity)
    assert evidence and all(item.source_kind == "skill_md" for item in evidence)
    assert evidence[0].metadata["verification"] == "unverified"
    fallback = next(item for item in recorder.events if item["event"] == "fallback.used")
    assert fallback["failed_phase"] == "sql_execution"
    assert fallback["strategy"] == "skill_md"
    assert "secret database data" not in json.dumps(recorder.events)


@pytest.mark.asyncio
async def test_source_urls_strip_credentials_and_query_secrets():
    recorder = Recorder()
    @traced("test.search")
    async def search():
        return [Evidence(source_kind="web", quote="private article content",
                         url="https://user:password@example.com/source?api_key=secret#private")]
    with trace_context(recorder=recorder, conversation_id=str(uuid4()), identity=Identity(user_id=uuid4()).model_dump(mode="json")):
        await search()
    reference = recorder.events[-1]["evidence_refs"][0]
    assert reference["url"] == "https://example.com/source"
    assert "password" not in json.dumps(recorder.events)
    assert "private article content" not in json.dumps(recorder.events)
