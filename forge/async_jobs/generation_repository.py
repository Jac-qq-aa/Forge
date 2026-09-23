"""文章生成任务的 PostgreSQL 事务仓储。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from forge.storage.job_result_store import JobResultStore
from forge.storage.pg_client import get_pg_pool

from .config import (
    GENERATION_EVENT_MAX_ATTEMPTS,
    GENERATION_MAX_REVISION_ROUNDS,
    GENERATION_RETRY_BASE_SECONDS,
    GENERATION_RETRY_MAX_SECONDS,
    GENERATION_TASK_TIMEOUT_SECONDS,
    GENERATION_TOKEN_BUDGET,
    ROCKETMQ_GENERATION_RETRY_TOPIC,
    ROCKETMQ_GENERATION_TOPIC,
    TENANT_MAX_ACTIVE_JOBS,
    USER_MAX_ACTIVE_JOBS,
)
from .repository import (
    IdempotencyConflict,
    JobStoreUnavailable,
    TenantActiveJobLimitExceeded,
)
from .state_machine import ApprovalConflict, StaleApproval, TaskNotFound


@dataclass(frozen=True)
class GenerationEventClaim:
    action: str
    task: dict[str, Any] | None = None
    event: dict[str, Any] | None = None
    execution_id: UUID | None = None


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _record(record: Any) -> dict[str, Any]:
    item = dict(record)
    for key in (
        "id",
        "session_id",
        "thread_id",
        "lease_token",
        "execution_id",
        "approval_id",
        "event_id",
        "task_id",
    ):
        if item.get(key) is not None:
            item[key] = str(item[key])
    for key in ("payload", "result", "pending_approval"):
        if key in item:
            item[key] = _json(item.get(key))
    for key, value in tuple(item.items()):
        if isinstance(value, datetime):
            item[key] = value.isoformat()
    return item


class GenerationTaskRepository:
    """封装生成任务、事件、审批、租约和 Outbox 的事务规则。"""

    def __init__(self, result_store: JobResultStore | None = None) -> None:
        self._result_store = result_store or JobResultStore()

    async def _pool(self):
        pool = await get_pg_pool()
        if pool is None:
            raise JobStoreUnavailable("PostgreSQL 不可用，无法处理生成任务")
        return pool

    @staticmethod
    async def _insert_outbox(
        conn,
        *,
        task_id: UUID,
        event_id: UUID,
        thread_id: UUID,
        action: str,
        expected_version: int,
        topic: str,
        approval_id: UUID | None = None,
        interrupt_id: str | None = None,
        delivery_timestamp: datetime | None = None,
    ) -> None:
        payload = {
            "event_id": str(event_id),
            "task_id": str(task_id),
            "thread_id": str(thread_id),
            "action": action,
            "expected_version": expected_version,
        }
        if approval_id is not None:
            payload["approval_id"] = str(approval_id)
        if interrupt_id:
            payload["interrupt_id"] = interrupt_id
        await conn.execute(
            """
            INSERT INTO workflow_outbox (
                job_id, event_id, topic, message_key, payload,
                action, expected_version, delivery_timestamp
            ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8)
            """,
            task_id,
            event_id,
            topic,
            str(event_id),
            json.dumps(payload, ensure_ascii=False),
            action,
            expected_version,
            delivery_timestamp,
        )

    async def create_task(
        self,
        *,
        payload: dict[str, Any],
        tenant_id: str,
        actor_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[dict[str, Any], bool]:
        pool = await self._pool()
        task_id = uuid4()
        thread_id = uuid4()
        event_id = uuid4()
        token_budget = GENERATION_TOKEN_BUDGET or None
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 7))",
                    f"{tenant_id}:{actor_id}:{idempotency_key}",
                )
                existing = await conn.fetchrow(
                    """
                    SELECT * FROM workflow_jobs
                    WHERE tenant_id = $1 AND idempotency_key = $2
                    """,
                    tenant_id,
                    idempotency_key,
                )
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        raise IdempotencyConflict("Idempotency-Key 已用于不同的请求内容")
                    return _record(existing), False

                tenant_active = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM workflow_jobs
                    WHERE tenant_id = $1
                      AND status IN ('queued', 'running', 'waiting_approval', 'retry_wait', 'cancelling')
                    """,
                    tenant_id,
                )
                user_active = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM workflow_jobs
                    WHERE tenant_id = $1 AND actor_id = $2
                      AND status IN ('queued', 'running', 'waiting_approval', 'retry_wait', 'cancelling')
                    """,
                    tenant_id,
                    actor_id,
                )
                if tenant_active >= TENANT_MAX_ACTIVE_JOBS:
                    raise TenantActiveJobLimitExceeded(
                        f"租户活跃任务已达到上限 {TENANT_MAX_ACTIVE_JOBS}"
                    )
                if user_active >= USER_MAX_ACTIVE_JOBS:
                    raise TenantActiveJobLimitExceeded(
                        f"用户活跃任务已达到上限 {USER_MAX_ACTIVE_JOBS}"
                    )

                task = await conn.fetchrow(
                    """
                    INSERT INTO workflow_jobs (
                        id, tenant_id, actor_id, idempotency_key, request_hash,
                        operation, session_id, thread_id, status, current_stage,
                        payload, max_attempts, max_revision_rounds, token_budget,
                        execution_deadline_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, 'generation', $6, $6,
                        'queued', 'queued', $7::jsonb, $8, $9, $10,
                        NOW() + ($11 * INTERVAL '1 second')
                    )
                    RETURNING *
                    """,
                    task_id,
                    tenant_id,
                    actor_id,
                    idempotency_key,
                    request_hash,
                    thread_id,
                    json.dumps(payload, ensure_ascii=False),
                    GENERATION_EVENT_MAX_ATTEMPTS,
                    GENERATION_MAX_REVISION_ROUNDS,
                    token_budget,
                    GENERATION_TASK_TIMEOUT_SECONDS,
                )
                await conn.execute(
                    """
                    INSERT INTO workflow_session_owners (
                        session_id, tenant_id, owner_subject
                    ) VALUES ($1, $2, $3)
                    ON CONFLICT (session_id) DO NOTHING
                    """,
                    thread_id,
                    tenant_id,
                    actor_id,
                )
                await conn.execute(
                    """
                    INSERT INTO workflow_events (
                        id, task_id, action, operation_key, expected_version,
                        max_attempts
                    ) VALUES ($1, $2, 'start', $3, 0, $4)
                    """,
                    event_id,
                    task_id,
                    f"{task_id}:start:v0",
                    GENERATION_EVENT_MAX_ATTEMPTS,
                )
                await self._insert_outbox(
                    conn,
                    task_id=task_id,
                    event_id=event_id,
                    thread_id=thread_id,
                    action="start",
                    expected_version=0,
                    topic=ROCKETMQ_GENERATION_TOPIC,
                )
        return _record(task), True

    async def get_task(
        self,
        task_id: str,
        tenant_id: str,
        actor_id: str,
    ) -> dict[str, Any] | None:
        pool = await self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM workflow_jobs
                WHERE id = $1 AND tenant_id = $2 AND actor_id = $3
                  AND operation = 'generation'
                """,
                UUID(task_id),
                tenant_id,
                actor_id,
            )
        if row is None:
            return None
        task = _record(row)
        task["result"] = await self._result_store.hydrate(task.get("result"))
        return task

    async def submit_approval(
        self,
        *,
        task_id: str,
        tenant_id: str,
        actor_id: str,
        idempotency_key: str,
        request_hash: str,
        interrupt_id: str,
        content_version: str,
        decision: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        pool = await self._pool()
        task_uuid = UUID(task_id)
        async with pool.acquire() as conn:
            async with conn.transaction():
                task = await conn.fetchrow(
                    """
                    SELECT * FROM workflow_jobs
                    WHERE id = $1 AND tenant_id = $2 AND actor_id = $3
                      AND operation = 'generation'
                    FOR UPDATE
                    """,
                    task_uuid,
                    tenant_id,
                    actor_id,
                )
                if task is None:
                    raise TaskNotFound("生成任务不存在")
                existing = await conn.fetchrow(
                    """
                    SELECT * FROM workflow_approvals
                    WHERE task_id = $1 AND idempotency_key = $2
                    """,
                    task_uuid,
                    idempotency_key,
                )
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        raise ApprovalConflict("审批幂等键已用于不同内容")
                    return _record(existing), False

                same_target = await conn.fetchrow(
                    """
                    SELECT * FROM workflow_approvals
                    WHERE task_id = $1 AND interrupt_id = $2 AND content_version = $3
                    """,
                    task_uuid,
                    interrupt_id,
                    content_version,
                )
                if same_target is not None:
                    if same_target["request_hash"] != request_hash:
                        raise ApprovalConflict("该审批点已经提交了不同的审批")
                    return _record(same_target), False

                if task["status"] != "waiting_approval":
                    raise StaleApproval("任务当前不在等待审批状态")
                if (
                    task["pending_interrupt_id"] != interrupt_id
                    or task["content_version"] != content_version
                ):
                    raise StaleApproval("审批点或内容版本已经过期")

                approval_id = uuid4()
                event_id = uuid4()
                next_version = int(task["version"]) + 1
                approval = await conn.fetchrow(
                    """
                    INSERT INTO workflow_approvals (
                        id, task_id, actor_id, idempotency_key, request_hash,
                        interrupt_id, content_version, decision, payload
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                    RETURNING *
                    """,
                    approval_id,
                    task_uuid,
                    actor_id,
                    idempotency_key,
                    request_hash,
                    interrupt_id,
                    content_version,
                    decision,
                    json.dumps(payload, ensure_ascii=False),
                )
                await conn.execute(
                    """
                    INSERT INTO workflow_events (
                        id, task_id, action, operation_key, expected_version,
                        approval_id, interrupt_id, content_version, max_attempts
                    ) VALUES ($1, $2, 'resume', $3, $4, $5, $6, $7, $8)
                    """,
                    event_id,
                    task_uuid,
                    f"{task_uuid}:resume:{interrupt_id}:{content_version}",
                    next_version,
                    approval_id,
                    interrupt_id,
                    content_version,
                    GENERATION_EVENT_MAX_ATTEMPTS,
                )
                await conn.execute(
                    "UPDATE workflow_approvals SET event_id = $2 WHERE id = $1",
                    approval_id,
                    event_id,
                )
                await conn.execute(
                    """
                    UPDATE workflow_jobs
                    SET status = 'queued', version = $2, current_stage = 'approval_queued',
                        pending_approval = NULL, retry_at = NULL
                    WHERE id = $1
                    """,
                    task_uuid,
                    next_version,
                )
                await self._insert_outbox(
                    conn,
                    task_id=task_uuid,
                    event_id=event_id,
                    thread_id=task["thread_id"],
                    action="resume",
                    expected_version=next_version,
                    topic=ROCKETMQ_GENERATION_TOPIC,
                    approval_id=approval_id,
                    interrupt_id=interrupt_id,
                )
        return _record(approval), True

    async def request_cancel(
        self,
        *,
        task_id: str,
        tenant_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        pool = await self._pool()
        task_uuid = UUID(task_id)
        async with pool.acquire() as conn:
            async with conn.transaction():
                task = await conn.fetchrow(
                    """
                    SELECT * FROM workflow_jobs
                    WHERE id = $1 AND tenant_id = $2 AND actor_id = $3
                      AND operation = 'generation'
                    FOR UPDATE
                    """,
                    task_uuid,
                    tenant_id,
                    actor_id,
                )
                if task is None:
                    raise TaskNotFound("生成任务不存在")
                if task["status"] in {"succeeded", "failed", "cancelled"}:
                    return _record(task)
                next_version = int(task["version"]) + 1
                if task["status"] in {"queued", "waiting_approval", "retry_wait"}:
                    updated = await conn.fetchrow(
                        """
                        UPDATE workflow_jobs
                        SET status = 'cancelled', cancel_requested = TRUE,
                            current_stage = 'cancelled', version = $2,
                            finished_at = NOW(), lease_owner = NULL,
                            lease_token = NULL, lease_expires_at = NULL,
                            execution_id = NULL, active_run_id = NULL
                        WHERE id = $1 RETURNING *
                        """,
                        task_uuid,
                        next_version,
                    )
                    return _record(updated)

                event_id = uuid4()
                updated = await conn.fetchrow(
                    """
                    UPDATE workflow_jobs
                    SET status = 'cancelling', cancel_requested = TRUE,
                        current_stage = 'cancelling', version = $2
                    WHERE id = $1 RETURNING *
                    """,
                    task_uuid,
                    next_version,
                )
                await conn.execute(
                    """
                    INSERT INTO workflow_events (
                        id, task_id, action, operation_key, expected_version,
                        max_attempts
                    ) VALUES ($1, $2, 'cancel', $3, $4, $5)
                    ON CONFLICT (task_id, operation_key) DO NOTHING
                    """,
                    event_id,
                    task_uuid,
                    f"{task_uuid}:cancel:v{next_version}",
                    next_version,
                    GENERATION_EVENT_MAX_ATTEMPTS,
                )
                await self._insert_outbox(
                    conn,
                    task_id=task_uuid,
                    event_id=event_id,
                    thread_id=task["thread_id"],
                    action="cancel",
                    expected_version=next_version,
                    topic=ROCKETMQ_GENERATION_TOPIC,
                )
        return _record(updated)

    async def claim_event(
        self,
        event_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> GenerationEventClaim:
        pool = await self._pool()
        event_uuid = UUID(event_id)
        execution_id = uuid4()
        async with pool.acquire() as conn:
            async with conn.transaction():
                event = await conn.fetchrow(
                    "SELECT * FROM workflow_events WHERE id = $1 FOR UPDATE",
                    event_uuid,
                )
                if event is None:
                    return GenerationEventClaim("missing")
                if event["status"] in {"completed", "stale", "dead_lettered"}:
                    return GenerationEventClaim("terminal", event=_record(event))
                task = await conn.fetchrow(
                    "SELECT * FROM workflow_jobs WHERE id = $1 FOR UPDATE",
                    event["task_id"],
                )
                if task is None:
                    return GenerationEventClaim("missing")
                if task["status"] in {"succeeded", "failed", "cancelled"}:
                    await conn.execute(
                        """
                        UPDATE workflow_events
                        SET status = 'stale', completed_at = NOW(),
                            last_error_code = 'task_terminal'
                        WHERE id = $1
                        """,
                        event_uuid,
                    )
                    return GenerationEventClaim("terminal", task=_record(task))
                if int(event["expected_version"]) != int(task["version"]):
                    await conn.execute(
                        """
                        UPDATE workflow_events
                        SET status = 'stale', completed_at = NOW(),
                            last_error_code = 'stale_version'
                        WHERE id = $1
                        """,
                        event_uuid,
                    )
                    return GenerationEventClaim("terminal", task=_record(task))
                if (
                    event["status"] == "processing"
                    and event["lease_expires_at"] is not None
                    and event["lease_expires_at"] > datetime.now(timezone.utc)
                ):
                    return GenerationEventClaim("busy")
                if event["attempt_count"] >= event["max_attempts"]:
                    await self._dead_letter_locked(
                        conn,
                        task=task,
                        event=event,
                        code="attempts_exhausted",
                        error="事件超过最大执行次数",
                    )
                    return GenerationEventClaim("terminal")

                if event["action"] != "cancel":
                    if (
                        task["status"] in {"running", "cancelling"}
                        and task["lease_expires_at"] is not None
                        and task["lease_expires_at"] > datetime.now(timezone.utc)
                    ):
                        return GenerationEventClaim("busy")
                    task = await conn.fetchrow(
                        """
                        UPDATE workflow_jobs
                        SET status = 'running', current_stage = 'running',
                            version = version + 1, attempt_count = attempt_count + 1,
                            lease_owner = $2, lease_token = $3, execution_id = $3,
                            lease_expires_at = NOW() + ($4 * INTERVAL '1 second'),
                            execution_deadline_at = NOW() + ($5 * INTERVAL '1 second'),
                            started_at = COALESCE(started_at, NOW()),
                            error = NULL, last_error_code = NULL, retry_at = NULL
                        WHERE id = $1
                        RETURNING *
                        """,
                        task["id"],
                        worker_id,
                        execution_id,
                        lease_seconds,
                        GENERATION_TASK_TIMEOUT_SECONDS,
                    )
                event = await conn.fetchrow(
                    """
                    UPDATE workflow_events
                    SET status = 'processing', attempt_count = attempt_count + 1,
                        execution_id = $2, lease_owner = $3,
                        lease_expires_at = NOW() + ($4 * INTERVAL '1 second'),
                        last_error = NULL, last_error_code = NULL
                    WHERE id = $1
                    RETURNING *
                    """,
                    event_uuid,
                    execution_id,
                    worker_id,
                    lease_seconds,
                )
        return GenerationEventClaim(
            "claimed", _record(task), _record(event), execution_id
        )

    async def load_approval_payload(self, approval_id: str) -> dict[str, Any]:
        pool = await self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM workflow_approvals WHERE id = $1",
                UUID(approval_id),
            )
        if row is None:
            raise StaleApproval("审批记录不存在")
        approval = _record(row)
        approval["payload"] = _json(row["payload"])
        return approval

    async def set_active_run(
        self,
        task_id: str,
        event_id: str,
        execution_id: UUID,
        run_id: str,
    ) -> bool:
        pool = await self._pool()
        async with pool.acquire() as conn:
            status = await conn.execute(
                """
                UPDATE workflow_jobs AS task
                SET active_run_id = $4
                FROM workflow_events AS event
                WHERE task.id = $1 AND event.id = $2
                  AND task.execution_id = $3 AND event.execution_id = $3
                  AND event.status = 'processing'
                """,
                UUID(task_id),
                UUID(event_id),
                execution_id,
                run_id,
            )
        return status == "UPDATE 1"

    async def renew_execution(
        self,
        task_id: str,
        event_id: str,
        execution_id: UUID,
        lease_seconds: int,
    ) -> str:
        pool = await self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                task = await conn.fetchrow(
                    """
                    UPDATE workflow_jobs
                    SET lease_expires_at = NOW() + ($3 * INTERVAL '1 second')
                    WHERE id = $1 AND execution_id = $2
                      AND status IN ('running', 'cancelling')
                    RETURNING cancel_requested
                    """,
                    UUID(task_id),
                    execution_id,
                    lease_seconds,
                )
                if task is None:
                    return "lost"
                event_status = await conn.execute(
                    """
                    UPDATE workflow_events
                    SET lease_expires_at = NOW() + ($3 * INTERVAL '1 second')
                    WHERE id = $1 AND execution_id = $2 AND status = 'processing'
                    """,
                    UUID(event_id),
                    execution_id,
                    lease_seconds,
                )
                if event_status != "UPDATE 1":
                    return "lost"
        return "cancel_requested" if task["cancel_requested"] else "renewed"

    async def complete_waiting(
        self,
        *,
        task_id: str,
        event_id: str,
        execution_id: UUID,
        stage: str,
        interrupt_id: str,
        content_version: str,
        pending_approval: dict[str, Any],
        revision_round: int,
    ) -> str:
        pool = await self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                task = await conn.fetchrow(
                    "SELECT * FROM workflow_jobs WHERE id = $1 FOR UPDATE",
                    UUID(task_id),
                )
                if task is None or task["execution_id"] != execution_id:
                    return "lease_lost"
                target = "cancelled" if task["cancel_requested"] else "waiting_approval"
                await conn.execute(
                    """
                    UPDATE workflow_jobs
                    SET status = $3::varchar, current_stage = $4, revision_round = $5,
                        pending_interrupt_id = CASE WHEN $3::varchar = 'waiting_approval' THEN $6 ELSE NULL END,
                        content_version = CASE WHEN $3::varchar = 'waiting_approval' THEN $7 ELSE NULL END,
                        pending_approval = CASE WHEN $3::varchar = 'waiting_approval' THEN $8::jsonb ELSE NULL END,
                        version = version + 1, active_run_id = NULL,
                        lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                        execution_id = NULL,
                        finished_at = CASE WHEN $3::varchar = 'cancelled' THEN NOW() ELSE NULL END
                    WHERE id = $1 AND execution_id = $2
                    """,
                    UUID(task_id),
                    execution_id,
                    target,
                    "cancelled" if target == "cancelled" else stage,
                    revision_round,
                    interrupt_id,
                    content_version,
                    json.dumps(pending_approval, ensure_ascii=False),
                )
                await self._complete_event_locked(conn, event_id, execution_id)
        return target

    async def complete_success(
        self,
        *,
        task_id: str,
        event_id: str,
        execution_id: UUID,
        stage: str,
        result: dict[str, Any],
        revision_round: int,
    ) -> str:
        stored_result = await self._result_store.persist(task_id, result)
        pool = await self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                task = await conn.fetchrow(
                    "SELECT * FROM workflow_jobs WHERE id = $1 FOR UPDATE",
                    UUID(task_id),
                )
                if task is None or task["execution_id"] != execution_id:
                    return "lease_lost"
                target = "cancelled" if task["cancel_requested"] else "succeeded"
                await conn.execute(
                    """
                    UPDATE workflow_jobs
                    SET status = $3::varchar, current_stage = $4, revision_round = $5,
                        result = CASE WHEN $3::varchar = 'succeeded' THEN $6::jsonb ELSE result END,
                        version = version + 1, active_run_id = NULL,
                        pending_interrupt_id = NULL, pending_approval = NULL,
                        lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                        execution_id = NULL, finished_at = NOW()
                    WHERE id = $1 AND execution_id = $2
                    """,
                    UUID(task_id),
                    execution_id,
                    target,
                    "cancelled" if target == "cancelled" else stage,
                    revision_round,
                    json.dumps(stored_result, ensure_ascii=False),
                )
                await self._complete_event_locked(conn, event_id, execution_id)
        return target

    async def fail_event(
        self,
        *,
        task_id: str,
        event_id: str,
        execution_id: UUID,
        retryable: bool,
        error_code: str,
        error: str,
    ) -> str:
        pool = await self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                task = await conn.fetchrow(
                    "SELECT * FROM workflow_jobs WHERE id = $1 FOR UPDATE",
                    UUID(task_id),
                )
                event = await conn.fetchrow(
                    "SELECT * FROM workflow_events WHERE id = $1 FOR UPDATE",
                    UUID(event_id),
                )
                if (
                    task is None
                    or event is None
                    or event["execution_id"] != execution_id
                    or (event["action"] != "cancel" and task["execution_id"] != execution_id)
                ):
                    return "lease_lost"
                if task["cancel_requested"]:
                    await self._cancel_locked(conn, task, event, execution_id)
                    return "cancelled"
                if retryable and task["attempt_count"] < task["max_attempts"]:
                    next_version = int(task["version"]) + 1
                    retry_event_id = uuid4()
                    delay = min(
                        GENERATION_RETRY_MAX_SECONDS,
                        GENERATION_RETRY_BASE_SECONDS
                        * (2 ** max(0, task["attempt_count"] - 1)),
                    )
                    deliver_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                    operation_key = (
                        f"{task['id']}:retry:r{task['revision_round']}:"
                        f"v{next_version}:a{task['attempt_count']}"
                    )
                    await conn.execute(
                        """
                        UPDATE workflow_jobs
                        SET status = 'retry_wait', current_stage = 'retry_wait',
                            version = $2, retry_at = $3, error = $4,
                            last_error_code = $5, active_run_id = NULL,
                            lease_owner = NULL, lease_token = NULL,
                            lease_expires_at = NULL, execution_id = NULL
                        WHERE id = $1
                        """,
                        task["id"],
                        next_version,
                        deliver_at,
                        error[:8000],
                        error_code,
                    )
                    await conn.execute(
                        """
                        UPDATE workflow_events
                        SET status = 'completed', completed_at = NOW(),
                            last_error_code = $3, last_error = $4,
                            lease_owner = NULL, lease_expires_at = NULL
                        WHERE id = $1 AND execution_id = $2
                        """,
                        event["id"],
                        execution_id,
                        error_code,
                        error[:8000],
                    )
                    await conn.execute(
                        """
                        INSERT INTO workflow_events (
                            id, task_id, action, operation_key, expected_version,
                            max_attempts, available_at
                        ) VALUES ($1, $2, 'retry', $3, $4, $5, $6)
                        """,
                        retry_event_id,
                        task["id"],
                        operation_key,
                        next_version,
                        GENERATION_EVENT_MAX_ATTEMPTS,
                        deliver_at,
                    )
                    await self._insert_outbox(
                        conn,
                        task_id=task["id"],
                        event_id=retry_event_id,
                        thread_id=task["thread_id"],
                        action="retry",
                        expected_version=next_version,
                        topic=ROCKETMQ_GENERATION_RETRY_TOPIC,
                        delivery_timestamp=deliver_at,
                    )
                    return "retry_wait"
                await self._dead_letter_locked(
                    conn,
                    task=task,
                    event=event,
                    code=error_code,
                    error=error,
                )
        return "failed"

    async def complete_cancel_event(
        self,
        *,
        task_id: str,
        event_id: str,
        execution_id: UUID,
    ) -> str:
        pool = await self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                task = await conn.fetchrow(
                    "SELECT * FROM workflow_jobs WHERE id = $1 FOR UPDATE",
                    UUID(task_id),
                )
                event = await conn.fetchrow(
                    "SELECT * FROM workflow_events WHERE id = $1 FOR UPDATE",
                    UUID(event_id),
                )
                if task is None or event is None or event["execution_id"] != execution_id:
                    return "lease_lost"
                await self._cancel_locked(conn, task, event, execution_id)
        return "cancelled"

    @staticmethod
    async def _complete_event_locked(conn, event_id: str, execution_id: UUID) -> None:
        event = await conn.fetchrow(
            """
            UPDATE workflow_events
            SET status = 'completed', completed_at = NOW(),
                lease_owner = NULL, lease_expires_at = NULL
            WHERE id = $1 AND execution_id = $2 AND status = 'processing'
            RETURNING approval_id
            """,
            UUID(event_id),
            execution_id,
        )
        if event and event["approval_id"]:
            await conn.execute(
                "UPDATE workflow_approvals SET applied_at = NOW() WHERE id = $1",
                event["approval_id"],
            )

    @staticmethod
    async def _cancel_locked(conn, task, event, execution_id: UUID) -> None:
        await conn.execute(
            """
            UPDATE workflow_jobs
            SET status = 'cancelled', current_stage = 'cancelled',
                cancel_requested = TRUE, version = version + 1,
                active_run_id = NULL, execution_id = NULL,
                lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                finished_at = NOW()
            WHERE id = $1
            """,
            task["id"],
        )
        await conn.execute(
            """
            UPDATE workflow_events
            SET status = 'completed', completed_at = NOW(),
                lease_owner = NULL, lease_expires_at = NULL
            WHERE id = $1 AND execution_id = $2
            """,
            event["id"],
            execution_id,
        )

    @staticmethod
    async def _dead_letter_locked(conn, *, task, event, code: str, error: str) -> None:
        await conn.execute(
            """
            UPDATE workflow_jobs
            SET status = 'failed', current_stage = 'failed',
                error = $2, last_error_code = $3, version = version + 1,
                active_run_id = NULL, execution_id = NULL,
                lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                finished_at = NOW()
            WHERE id = $1
            """,
            task["id"],
            error[:8000],
            code,
        )
        await conn.execute(
            """
            UPDATE workflow_events
            SET status = 'dead_lettered', completed_at = NOW(),
                last_error_code = $2, last_error = $3,
                lease_owner = NULL, lease_expires_at = NULL
            WHERE id = $1
            """,
            event["id"],
            code,
            error[:8000],
        )
        await conn.execute(
            """
            INSERT INTO workflow_dead_letters (
                task_id, event_id, error_code, error, payload
            ) VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT (event_id) DO NOTHING
            """,
            task["id"],
            event["id"],
            code,
            error[:8000],
            json.dumps(
                {
                    "action": event["action"],
                    "expected_version": event["expected_version"],
                    "attempt_count": event["attempt_count"],
                }
            ),
        )

    async def enqueue_expired_recoveries(self, limit: int) -> int:
        """只核对过期执行；正常任务仍由 RocketMQ 分发。"""
        pool = await self._pool()
        created = 0
        async with pool.acquire() as conn:
            async with conn.transaction():
                tasks = await conn.fetch(
                    """
                    SELECT * FROM workflow_jobs
                    WHERE operation = 'generation'
                      AND status IN ('running', 'cancelling')
                      AND lease_expires_at < NOW()
                    ORDER BY lease_expires_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT $1
                    """,
                    limit,
                )
                for task in tasks:
                    action = "cancel" if task["cancel_requested"] else "recover"
                    operation_key = f"{task['id']}:{action}:v{task['version']}"
                    event_id = uuid4()
                    inserted = await conn.fetchrow(
                        """
                        INSERT INTO workflow_events (
                            id, task_id, action, operation_key, expected_version,
                            max_attempts
                        ) VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT (task_id, operation_key) DO NOTHING
                        RETURNING id
                        """,
                        event_id,
                        task["id"],
                        action,
                        operation_key,
                        task["version"],
                        GENERATION_EVENT_MAX_ATTEMPTS,
                    )
                    if inserted is None:
                        continue
                    await self._insert_outbox(
                        conn,
                        task_id=task["id"],
                        event_id=event_id,
                        thread_id=task["thread_id"],
                        action=action,
                        expected_version=task["version"],
                        topic=ROCKETMQ_GENERATION_TOPIC,
                    )
                    created += 1
        return created

    async def list_dead_letters(self, limit: int = 100) -> list[dict[str, Any]]:
        pool = await self._pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT dead.*, task.tenant_id, task.actor_id, task.status AS task_status
                FROM workflow_dead_letters AS dead
                JOIN workflow_jobs AS task ON task.id = dead.task_id
                ORDER BY dead.created_at DESC
                LIMIT $1
                """,
                limit,
            )
        return [_record(row) for row in rows]

    async def replay_dead_letter(
        self, dead_letter_id: int | str, actor_id: str
    ) -> dict[str, Any] | None:
        dead_letter_id = int(dead_letter_id)
        pool = await self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                dead = await conn.fetchrow(
                    "SELECT * FROM workflow_dead_letters WHERE id = $1 FOR UPDATE",
                    dead_letter_id,
                )
                if dead is None or dead["replayed_at"] is not None:
                    return None
                task = await conn.fetchrow(
                    "SELECT * FROM workflow_jobs WHERE id = $1 FOR UPDATE",
                    dead["task_id"],
                )
                if task is None or task["status"] != "failed":
                    return None
                next_version = int(task["version"]) + 1
                event_id = uuid4()
                await conn.execute(
                    """
                    UPDATE workflow_jobs
                    SET status = 'queued', current_stage = 'replay_queued',
                        version = $2, error = NULL, last_error_code = NULL,
                        finished_at = NULL, cancel_requested = FALSE
                    WHERE id = $1
                    """,
                    task["id"],
                    next_version,
                )
                await conn.execute(
                    """
                    INSERT INTO workflow_events (
                        id, task_id, action, operation_key, expected_version,
                        max_attempts
                    ) VALUES ($1, $2, 'replay', $3, $4, $5)
                    """,
                    event_id,
                    task["id"],
                    f"{task['id']}:replay:{dead_letter_id}:v{next_version}",
                    next_version,
                    GENERATION_EVENT_MAX_ATTEMPTS,
                )
                await conn.execute(
                    """
                    UPDATE workflow_dead_letters
                    SET replayed_event_id = $2, replayed_by = $3, replayed_at = NOW()
                    WHERE id = $1
                    """,
                    dead_letter_id,
                    event_id,
                    actor_id,
                )
                await self._insert_outbox(
                    conn,
                    task_id=task["id"],
                    event_id=event_id,
                    thread_id=task["thread_id"],
                    action="replay",
                    expected_version=next_version,
                    topic=ROCKETMQ_GENERATION_TOPIC,
                )
        return {"task_id": str(task["id"]), "event_id": str(event_id), "status": "queued"}
