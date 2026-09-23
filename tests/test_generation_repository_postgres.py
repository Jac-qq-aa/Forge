"""需要真实 PostgreSQL 的生成任务事务与故障窗口测试。"""

import asyncio
import os
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from forge.async_jobs import generation_repository as repository_module
from forge.async_jobs.generation_repository import GenerationTaskRepository
from forge.async_jobs.state_machine import StaleApproval


pytestmark = pytest.mark.skipif(
    not os.getenv("FORGE_TEST_POSTGRES_DSN"),
    reason="设置 FORGE_TEST_POSTGRES_DSN 后运行 PostgreSQL 集成测试",
)


@pytest_asyncio.fixture
async def repository(monkeypatch):
    dsn = os.environ["FORGE_TEST_POSTGRES_DSN"]
    connection = await asyncpg.connect(dsn)
    root = Path(__file__).resolve().parents[1]
    try:
        for name in (
            "011_async_workflow_jobs.sql",
            "012_async_job_phase_one.sql",
            "013_generation_task_reliability.sql",
        ):
            await connection.execute((root / "migrations" / name).read_text(encoding="utf-8"))
        await connection.execute(
            "TRUNCATE workflow_dead_letters, workflow_approvals, workflow_events, "
            "workflow_outbox, workflow_session_owners, workflow_jobs CASCADE"
        )
    finally:
        await connection.close()
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=6)

    async def fake_pool():
        return pool

    monkeypatch.setattr(repository_module, "get_pg_pool", fake_pool)
    yield GenerationTaskRepository()
    await pool.close()


@pytest.mark.asyncio
async def test_concurrent_duplicate_submission_creates_one_task(repository):
    async def submit():
        return await repository.create_task(
            payload={"source_article": {"text": "正文"}},
            tenant_id="tenant-1",
            actor_id="user-1",
            idempotency_key="same-key",
            request_hash="a" * 64,
        )

    results = await asyncio.gather(*(submit() for _ in range(8)))

    assert len({result[0]["id"] for result in results}) == 1
    assert sum(1 for _, created in results if created) == 1


@pytest.mark.asyncio
async def test_duplicate_approval_advances_only_once_and_stale_is_rejected(repository):
    task, _ = await repository.create_task(
        payload={"source_article": {"text": "正文"}},
        tenant_id="tenant-1",
        actor_id="user-1",
        idempotency_key="approval-task",
        request_hash="b" * 64,
    )
    pool = await repository._pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE workflow_jobs
            SET status='waiting_approval', version=1,
                pending_interrupt_id='interrupt-1', content_version='content-1'
            WHERE id=$1
            """,
            __import__("uuid").UUID(task["id"]),
        )

    kwargs = dict(
        task_id=task["id"],
        tenant_id="tenant-1",
        actor_id="user-1",
        idempotency_key="approval-key",
        request_hash="c" * 64,
        interrupt_id="interrupt-1",
        content_version="content-1",
        decision="approve",
        payload={"decision": "approve"},
    )
    first, first_created = await repository.submit_approval(**kwargs)
    second, second_created = await repository.submit_approval(**kwargs)

    assert first["id"] == second["id"]
    assert first_created is True
    assert second_created is False
    with pytest.raises(StaleApproval):
        await repository.submit_approval(
            **{
                **kwargs,
                "idempotency_key": "stale-key",
                "request_hash": "d" * 64,
                "content_version": "old-content",
            }
        )


@pytest.mark.asyncio
async def test_cancelled_task_rejects_late_success(repository):
    task, _ = await repository.create_task(
        payload={"source_article": {"text": "正文"}},
        tenant_id="tenant-1",
        actor_id="user-1",
        idempotency_key="cancel-task",
        request_hash="e" * 64,
    )
    pool = await repository._pool()
    async with pool.acquire() as conn:
        event_id = await conn.fetchval(
            "SELECT id FROM workflow_events WHERE task_id=$1",
            __import__("uuid").UUID(task["id"]),
        )
    claim = await repository.claim_event(str(event_id), "worker-1", 60)
    await repository.request_cancel(
        task_id=task["id"], tenant_id="tenant-1", actor_id="user-1"
    )

    result = await repository.complete_success(
        task_id=task["id"],
        event_id=str(event_id),
        execution_id=claim.execution_id,
        stage="completed",
        result={"final_draft": "迟到结果"},
        revision_round=0,
    )

    assert result == "cancelled"
    current = await repository.get_task(task["id"], "tenant-1", "user-1")
    assert current["status"] == "cancelled"
    assert current["result"] is None


@pytest.mark.asyncio
async def test_expired_lease_takeover_fences_old_worker(repository):
    task, _ = await repository.create_task(
        payload={"source_article": {"text": "正文"}},
        tenant_id="tenant-1",
        actor_id="user-1",
        idempotency_key="lease-task",
        request_hash="f" * 64,
    )
    pool = await repository._pool()
    async with pool.acquire() as conn:
        start_event = await conn.fetchval(
            "SELECT id FROM workflow_events WHERE task_id=$1",
            __import__("uuid").UUID(task["id"]),
        )
    old_claim = await repository.claim_event(str(start_event), "old-worker", 60)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE workflow_jobs SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE id=$1",
            __import__("uuid").UUID(task["id"]),
        )
        await conn.execute(
            "UPDATE workflow_events SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE id=$1",
            start_event,
        )

    assert await repository.enqueue_expired_recoveries(10) == 1
    async with pool.acquire() as conn:
        recover_event = await conn.fetchval(
            "SELECT id FROM workflow_events WHERE task_id=$1 AND action='recover'",
            __import__("uuid").UUID(task["id"]),
        )
    new_claim = await repository.claim_event(str(recover_event), "new-worker", 60)

    assert new_claim.action == "claimed"
    stale = await repository.complete_success(
        task_id=task["id"],
        event_id=str(start_event),
        execution_id=old_claim.execution_id,
        stage="completed",
        result={"final_draft": "旧 Worker"},
        revision_round=0,
    )
    assert stale == "lease_lost"
    waiting = await repository.complete_waiting(
        task_id=task["id"],
        event_id=str(recover_event),
        execution_id=new_claim.execution_id,
        stage="waiting_outline",
        interrupt_id="interrupt-new",
        content_version="content-new",
        pending_approval={"type": "outline_approval"},
        revision_round=0,
    )
    assert waiting == "waiting_approval"


@pytest.mark.asyncio
async def test_non_retryable_failure_is_trackable_and_controlled_replay_is_new_event(repository):
    task, _ = await repository.create_task(
        payload={"source_article": {"text": "正文"}},
        tenant_id="tenant-1",
        actor_id="user-1",
        idempotency_key="dead-task",
        request_hash="1" * 64,
    )
    pool = await repository._pool()
    async with pool.acquire() as conn:
        event_id = await conn.fetchval(
            "SELECT id FROM workflow_events WHERE task_id=$1",
            __import__("uuid").UUID(task["id"]),
        )
    claim = await repository.claim_event(str(event_id), "worker-1", 60)
    status = await repository.fail_event(
        task_id=task["id"],
        event_id=str(event_id),
        execution_id=claim.execution_id,
        retryable=False,
        error_code="invalid_request",
        error="参数错误",
    )

    assert status == "failed"
    dead_letters = await repository.list_dead_letters()
    assert dead_letters[0]["event_id"] == str(event_id)
    replay = await repository.replay_dead_letter(dead_letters[0]["id"], "admin-1")
    assert replay["task_id"] == task["id"]
    assert replay["event_id"] != str(event_id)
    current = await repository.get_task(task["id"], "tenant-1", "user-1")
    assert current["status"] == "queued"


@pytest.mark.asyncio
async def test_retryable_failure_stops_at_task_attempt_limit(repository):
    task, _ = await repository.create_task(
        payload={"source_article": {"text": "正文"}},
        tenant_id="tenant-1",
        actor_id="user-1",
        idempotency_key="retry-limit-task",
        request_hash="2" * 64,
    )
    pool = await repository._pool()
    async with pool.acquire() as conn:
        event_id = await conn.fetchval(
            "SELECT id FROM workflow_events WHERE task_id=$1",
            __import__("uuid").UUID(task["id"]),
        )

    statuses = []
    for attempt in range(1, 4):
        claim = await repository.claim_event(str(event_id), f"worker-{attempt}", 60)
        assert claim.action == "claimed"
        statuses.append(
            await repository.fail_event(
                task_id=task["id"],
                event_id=str(event_id),
                execution_id=claim.execution_id,
                retryable=True,
                error_code="transient_dependency",
                error="上游暂时不可用",
            )
        )
        if statuses[-1] == "retry_wait":
            async with pool.acquire() as conn:
                event_id = await conn.fetchval(
                    """
                    SELECT id FROM workflow_events
                    WHERE task_id=$1 AND status='pending'
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    __import__("uuid").UUID(task["id"]),
                )

    assert statuses == ["retry_wait", "retry_wait", "failed"]
    current = await repository.get_task(task["id"], "tenant-1", "user-1")
    assert current["attempt_count"] == 3
    assert current["status"] == "failed"
    dead_letters = await repository.list_dead_letters()
    assert dead_letters[0]["error_code"] == "transient_dependency"
