import json
import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest

from forge.consumer_app import create_app
from forge.domain.jobs import COMMAND_ROUTES, WorkflowCommand
from forge.domain.models import Identity
from forge.infrastructure.audit import TaskAuditor
from forge.infrastructure.jobs import JobRepository


def test_bridge_routes_match_worker_contract():
    routes = json.loads((Path(__file__).parents[1] / "deploy/rocketmq-bridge/routes.json").read_text())
    assert {(r["topic"], r["consumer_group"]) for r in routes} == set(COMMAND_ROUTES.values())
    assert len({r["target_url"] for r in routes}) == 3
    assert len({r["secret_env"] for r in routes}) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", [None, "topic", "consumer_group", "kind", "secret"])
async def test_worker_rejects_misrouted_messages(wrong):
    app = create_app()
    handler = SimpleNamespace(handle=AsyncMock())
    app.state.handler = handler
    app.state.runtime = SimpleNamespace(settings=SimpleNamespace(consumer_kind="audit_job", rocketmq_consumer_secret="secret"))
    command = WorkflowCommand(job_id=uuid4(), conversation_id=uuid4(), actor_user_id=uuid4(),
                              kind="start" if wrong == "kind" else "audit_job", payload={})
    topic, group = COMMAND_ROUTES["audit_job"]
    delivery = {"topic": topic, "consumer_group": group, "message_id": str(command.message_id), "body": command.transport_payload()}
    if wrong in {"topic", "consumer_group"}:
        delivery[wrong] = "wrong"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/internal/rocketmq", json=delivery,
                                     headers={"X-Forge-Consumer-Secret": "wrong" if wrong == "secret" else "secret"})
    assert response.status_code == (200 if wrong is None else 401 if wrong == "secret" else 422)
    assert handler.handle.await_count == (1 if wrong is None else 0)


@pytest.mark.asyncio
async def test_stale_execution_is_reported_without_reading_current_article():
    identity = Identity(user_id=uuid4())
    connection = SimpleNamespace(fetchrow=AsyncMock(side_effect=[None, {"payload": {"_execution_id": str(uuid4())}}]),
                                 execute=AsyncMock(), fetchval=AsyncMock())

    @asynccontextmanager
    async def transaction():
        yield

    @asynccontextmanager
    async def authorized(actor):
        assert actor == identity
        yield connection

    connection.transaction = transaction
    payload = {"source_job_id": str(uuid4()), "conversation_id": str(uuid4()),
               "source_execution_id": str(uuid4()), "source_run_id": "run", "check_version": 1}
    report = await TaskAuditor(SimpleNamespace(authorized=authorized)).check(uuid4(), payload, identity)
    assert report["status"] == "attention"
    assert report["checks"][0]["code"] == "execution_current"
    connection.fetchval.assert_not_called()
    connection.execute.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["start", "index_document", "audit_job"])
async def test_capacity_claim_reads_only_its_kind(kind):
    connection = SimpleNamespace(execute=AsyncMock(), fetchval=AsyncMock(return_value=1), fetch=AsyncMock(return_value=[]))

    @asynccontextmanager
    async def context():
        yield connection

    connection.transaction = context
    repository = JobRepository(SimpleNamespace(pool=SimpleNamespace(acquire=context)))
    assert await repository.claim_capacity(1, kind=kind) == []
    assert connection.fetchval.await_args.args[1] == kind
    assert connection.fetch.await_args.args[1:] == (1, kind)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,status,enqueue", [
    ("start", "completed", True), ("index_document", "failed", True),
    ("audit_job", "completed", False), ("start", "waiting", False)])
async def test_terminal_audit_is_enqueued_in_same_transaction_without_recursion(kind, status, enqueue):
    source = {"conversation_id": uuid4(), "kind": kind}
    connection = SimpleNamespace(fetchrow=AsyncMock(return_value=source), execute=AsyncMock())
    in_transaction = False

    @asynccontextmanager
    async def acquire():
        yield connection

    @asynccontextmanager
    async def transaction():
        nonlocal in_transaction
        in_transaction = True
        yield
        in_transaction = False

    connection.transaction = transaction
    repository = JobRepository(SimpleNamespace(pool=SimpleNamespace(acquire=acquire)))

    async def audit(*args):
        assert in_transaction

    repository._enqueue_audit = AsyncMock(side_effect=audit)
    assert await repository.finish(job_id=uuid4(), status=status)
    assert repository._enqueue_audit.await_count == int(enqueue)
    assert all("UPDATE conversations" not in call.args[0] for call in connection.execute.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("source_status", ["completed", "failed"])
async def test_audit_reports_terminal_failure_and_success(source_status):
    identity = Identity(user_id=uuid4())
    execution_id = str(uuid4())
    source = {"payload": {"_execution_id": execution_id}, "status": source_status,
              "kind": "start", "result": {}}
    connection = SimpleNamespace(fetchrow=AsyncMock(side_effect=[None, source]),
                                 execute=AsyncMock(), fetchval=AsyncMock(return_value=1))

    @asynccontextmanager
    async def transaction():
        yield

    @asynccontextmanager
    async def authorized(actor):
        yield connection

    connection.transaction = transaction
    payload = {"source_job_id": str(uuid4()), "conversation_id": str(uuid4()),
               "source_execution_id": execution_id, "source_run_id": "run", "check_version": 1,
               "source_status": source_status}
    report = await TaskAuditor(SimpleNamespace(authorized=authorized)).check(uuid4(), payload, identity)
    assert report["status"] == ("pass" if source_status == "completed" else "attention")
    assert all("UPDATE" not in call.args[0] for call in connection.execute.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("tampered", [False, True])
async def test_audit_detects_changed_paragraph_fact_links(tampered):
    identity = Identity(user_id=uuid4())
    execution_id, snapshot_id, paragraph_id, section_id = [str(uuid4()) for _ in range(4)]
    paragraph = {"paragraph_id": paragraph_id, "section_id": section_id, "content": "测试正文"}
    refs = [{"fact_id": str(uuid4()), "version": 1}]
    snapshot = {"snapshot_id": snapshot_id, "paragraphs": [paragraph],
                "fact_refs": {paragraph_id: refs}, "issue_ids": {paragraph_id: []}}
    source = {"payload": {"_execution_id": execution_id}, "status": "completed",
              "kind": "start", "result": {"draft_history": [snapshot]}}
    provenance = {"paragraph_id": UUID(paragraph_id), "section_id": UUID(section_id),
                  "content_fingerprint": hashlib.sha256(paragraph["content"].encode()).hexdigest(),
                  "fact_refs": [] if tampered else refs, "issue_ids": []}
    connection = SimpleNamespace(fetchrow=AsyncMock(side_effect=[None, source,
                                 {"article_id": uuid4(), "version": 1, "paragraphs": [paragraph]}]),
                                 execute=AsyncMock(), fetchval=AsyncMock(return_value=1),
                                 fetch=AsyncMock(return_value=[provenance]))

    @asynccontextmanager
    async def transaction():
        yield

    @asynccontextmanager
    async def authorized(actor):
        yield connection

    connection.transaction = transaction
    payload = {"source_job_id": str(uuid4()), "conversation_id": str(uuid4()),
               "source_execution_id": execution_id, "source_run_id": "run", "check_version": 1,
               "source_status": "completed"}
    report = await TaskAuditor(SimpleNamespace(authorized=authorized)).check(uuid4(), payload, identity)
    assert report["status"] == ("attention" if tampered else "pass")
