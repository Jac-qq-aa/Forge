"""PostgreSQL 任务、租约和 Outbox 存储。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from forge.storage.pg_client import get_pg_pool
from forge.storage.job_result_store import JobResultStore


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


class JobStoreUnavailable(RuntimeError):
    """异步任务所需的 PostgreSQL 不可用。"""


class IdempotencyConflict(RuntimeError):
    """同一个幂等键被用于不同请求。"""


class TenantActiveJobLimitExceeded(RuntimeError):
    """租户同时处于 queued/running 的任务达到上限。"""


class ResourceOwnershipError(RuntimeError):
    """租户无权访问指定会话。"""


@dataclass(frozen=True)
class JobClaim:
    action: str
    job: dict[str, Any] | None = None
    lease_token: UUID | None = None


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _record_to_job(record: Any) -> dict[str, Any]:
    result = dict(record)
    for key in ("id", "session_id", "lease_token"):
        if result.get(key) is not None:
            result[key] = str(result[key])
    for key in ("payload", "result"):
        result[key] = _json_value(result.get(key))
    for key, value in list(result.items()):
        if isinstance(value, datetime):
            result[key] = value.isoformat()
    return result


class JobRepository:
    """将任务提交、幂等领取、租约和 Outbox 事务封装在一个模块中。"""

    def __init__(self, result_store: JobResultStore | None = None) -> None:
        self._result_store = result_store or JobResultStore()

    async def _pool(self):
        pool = await get_pg_pool()
        if pool is None:
            raise JobStoreUnavailable("PostgreSQL 不可用，无法提交异步任务")
        return pool

    async def create_job(
        self,
        *,
        operation: str,
        payload: dict[str, Any],
        tenant_id: str,
        actor_id: str,
        idempotency_key: str,
        request_hash: str,
        topic: str,
        max_attempts: int,
        max_active_jobs: int,
        max_user_active_jobs: int,
    ) -> tuple[dict[str, Any], bool]:
        pool = await self._pool()
        job_id = uuid4()
        session_id = payload.get("session_id")
        session_uuid = UUID(session_id) if session_id else None

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 1))",
                    tenant_id,
                )
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 2))",
                    f"{tenant_id}:{actor_id}",
                )
                row = await conn.fetchrow(
                    """
                    SELECT * FROM workflow_jobs
                    WHERE tenant_id = $1 AND idempotency_key = $2
                    """,
                    tenant_id,
                    idempotency_key,
                )
                if row is not None:
                    if row["request_hash"] != request_hash:
                        raise IdempotencyConflict(
                            "Idempotency-Key 已用于不同的请求内容"
                        )
                    return _record_to_job(row), False

                if session_uuid is not None:
                    owner = await conn.fetchrow(
                        """
                        SELECT tenant_id, owner_subject
                        FROM workflow_session_owners
                        WHERE session_id = $1
                        """,
                        session_uuid,
                    )
                    if (
                        owner is None
                        or owner["tenant_id"] != tenant_id
                        or owner["owner_subject"] != actor_id
                    ):
                        raise ResourceOwnershipError("会话不存在或不属于当前租户")

                active_jobs = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM workflow_jobs
                    WHERE tenant_id = $1 AND status IN ('queued', 'running')
                    """,
                    tenant_id,
                )
                if active_jobs >= max_active_jobs:
                    raise TenantActiveJobLimitExceeded(
                        f"租户活跃任务已达到上限 {max_active_jobs}"
                    )

                user_active_jobs = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM workflow_jobs
                    WHERE tenant_id = $1 AND actor_id = $2
                      AND status IN ('queued', 'running')
                    """,
                    tenant_id,
                    actor_id,
                )
                if user_active_jobs >= max_user_active_jobs:
                    raise TenantActiveJobLimitExceeded(
                        f"用户活跃任务已达到上限 {max_user_active_jobs}"
                    )

                row = await conn.fetchrow(
                    """
                    INSERT INTO workflow_jobs (
                        id, tenant_id, actor_id, idempotency_key, request_hash, operation, session_id,
                        status, payload, max_attempts
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, 'queued', $8::jsonb, $9)
                    RETURNING *
                    """,
                    job_id,
                    tenant_id,
                    actor_id,
                    idempotency_key,
                    request_hash,
                    operation,
                    session_uuid,
                    json.dumps(payload, ensure_ascii=False),
                    max_attempts,
                )
                await conn.execute(
                    """
                    INSERT INTO workflow_outbox (job_id, topic, message_key, payload)
                    VALUES ($1, $2, $3, $4::jsonb)
                    """,
                    job_id,
                    topic,
                    str(job_id),
                    json.dumps({"job_id": str(job_id)}),
                )
        return _record_to_job(row), True

    async def get_job(
        self,
        job_id: str,
        tenant_id: str,
        actor_id: str,
    ) -> dict[str, Any] | None:
        pool = await self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM workflow_jobs
                WHERE id = $1 AND tenant_id = $2 AND actor_id = $3
                """,
                UUID(job_id),
                tenant_id,
                actor_id,
            )
        if row is None:
            return None
        job = _record_to_job(row)
        job["result"] = await self._result_store.hydrate(job.get("result"))
        return job

    async def owns_session(
        self,
        session_id: str,
        tenant_id: str,
        actor_id: str,
    ) -> bool:
        pool = await self._pool()
        async with pool.acquire() as conn:
            owner = await conn.fetchrow(
                """
                SELECT tenant_id, owner_subject
                FROM workflow_session_owners
                WHERE session_id = $1
                """,
                UUID(session_id),
            )
        return bool(
            owner
            and owner["tenant_id"] == tenant_id
            and owner["owner_subject"] == actor_id
        )

    async def list_owned_session_ids(self, tenant_id: str, actor_id: str) -> set[str]:
        pool = await self._pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT session_id FROM workflow_session_owners
                WHERE tenant_id = $1 AND owner_subject = $2
                """,
                tenant_id,
                actor_id,
            )
        return {str(row["session_id"]) for row in rows}

    async def claim_outbox(
        self,
        owner: str,
        limit: int,
        lock_seconds: int,
    ) -> list[dict[str, Any]]:
        pool = await self._pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH candidates AS (
                    SELECT id
                    FROM workflow_outbox
                    WHERE published_at IS NULL
                      AND dead_lettered_at IS NULL
                      AND available_at <= NOW()
                      AND (locked_until IS NULL OR locked_until < NOW())
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                    LIMIT $2
                )
                UPDATE workflow_outbox AS outbox
                SET locked_by = $1,
                    locked_until = NOW() + ($3 * INTERVAL '1 second')
                FROM candidates
                WHERE outbox.id = candidates.id
                RETURNING outbox.*
                """,
                owner,
                limit,
                lock_seconds,
            )
        result = []
        for row in rows:
            item = dict(row)
            item["job_id"] = str(item["job_id"])
            item["payload"] = _json_value(item["payload"])
            result.append(item)
        return result

    async def mark_outbox_published(self, outbox_id: int, owner: str) -> None:
        pool = await self._pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE workflow_outbox
                SET published_at = NOW(), locked_by = NULL, locked_until = NULL
                WHERE id = $1 AND locked_by = $2
                """,
                outbox_id,
                owner,
            )

    async def mark_outbox_failed(
        self,
        outbox_id: int,
        owner: str,
        error: str,
        max_attempts: int,
    ) -> bool:
        pool = await self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE workflow_outbox
                SET attempts = attempts + 1,
                    last_error = $3,
                    dead_lettered_at = CASE
                        WHEN attempts + 1 >= $4 THEN NOW()
                        ELSE NULL
                    END,
                    available_at = NOW() + (
                        LEAST(60, (1 << LEAST(attempts, 6))) * INTERVAL '1 second'
                    ),
                    locked_by = NULL,
                    locked_until = NULL
                WHERE id = $1 AND locked_by = $2
                RETURNING dead_lettered_at
                """,
                outbox_id,
                owner,
                error[:4000],
                max_attempts,
            )
        return bool(row and row["dead_lettered_at"] is not None)

    async def list_dead_letters(self, limit: int = 100) -> list[dict[str, Any]]:
        pool = await self._pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT outbox.*, jobs.tenant_id, jobs.operation, jobs.status AS job_status
                FROM workflow_outbox AS outbox
                JOIN workflow_jobs AS jobs ON jobs.id = outbox.job_id
                WHERE outbox.dead_lettered_at IS NOT NULL
                ORDER BY outbox.dead_lettered_at DESC
                LIMIT $1
                """,
                limit,
            )
        result = []
        for row in rows:
            item = dict(row)
            item["job_id"] = str(item["job_id"])
            item["payload"] = _json_value(item["payload"])
            for key, value in list(item.items()):
                if isinstance(value, datetime):
                    item[key] = value.isoformat()
            result.append(item)
        return result

    async def replay_dead_letter(self, outbox_id: int) -> bool:
        pool = await self._pool()
        async with pool.acquire() as conn:
            status = await conn.execute(
                """
                UPDATE workflow_outbox
                SET attempts = 0,
                    last_error = NULL,
                    available_at = NOW(),
                    locked_by = NULL,
                    locked_until = NULL,
                    dead_lettered_at = NULL,
                    replayed_at = NOW()
                WHERE id = $1 AND dead_lettered_at IS NOT NULL
                """,
                outbox_id,
            )
        return status == "UPDATE 1"

    async def cleanup(self, job_retention_days: int, outbox_retention_hours: int) -> None:
        pool = await self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    DELETE FROM workflow_outbox
                    WHERE published_at < NOW() - ($1 * INTERVAL '1 hour')
                    """,
                    outbox_retention_hours,
                )
                await conn.execute(
                    """
                    DELETE FROM workflow_jobs
                    WHERE status IN ('succeeded', 'failed', 'cancelled')
                      AND finished_at < NOW() - ($1 * INTERVAL '1 day')
                      AND NOT EXISTS (
                          SELECT 1 FROM workflow_outbox
                          WHERE workflow_outbox.job_id = workflow_jobs.id
                            AND workflow_outbox.dead_lettered_at IS NOT NULL
                      )
                    """,
                    job_retention_days,
                )

    async def claim_job(self, job_id: str, worker_id: str, lease_seconds: int) -> JobClaim:
        pool = await self._pool()
        lease_token = uuid4()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT * FROM workflow_jobs WHERE id = $1 FOR UPDATE",
                    UUID(job_id),
                )
                if row is None:
                    return JobClaim("missing")
                if row["status"] in TERMINAL_STATUSES:
                    return JobClaim("terminal", _record_to_job(row))
                if row["attempt_count"] >= row["max_attempts"]:
                    await conn.execute(
                        """
                        UPDATE workflow_jobs
                        SET status = 'failed', error = COALESCE(error, '超过最大执行次数'),
                            finished_at = NOW(), lease_owner = NULL,
                            lease_token = NULL, lease_expires_at = NULL
                        WHERE id = $1
                        """,
                        UUID(job_id),
                    )
                    return JobClaim("terminal")
                if row["status"] == "running" and row["lease_expires_at"] is not None:
                    now = datetime.now(row["lease_expires_at"].tzinfo)
                    if row["lease_expires_at"] > now:
                        return JobClaim("busy")
                if row["session_id"] is not None:
                    session_lock_acquired = await conn.fetchval(
                        "SELECT pg_try_advisory_xact_lock(hashtextextended($1, 0))",
                        str(row["session_id"]),
                    )
                    if not session_lock_acquired:
                        return JobClaim("busy")
                    concurrent = await conn.fetchval(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM workflow_jobs
                            WHERE session_id = $1 AND id <> $2 AND status = 'running'
                              AND lease_expires_at > NOW()
                        )
                        """,
                        row["session_id"],
                        row["id"],
                    )
                    if concurrent:
                        return JobClaim("busy")
                claimed = await conn.fetchrow(
                    """
                    UPDATE workflow_jobs
                    SET status = 'running', attempt_count = attempt_count + 1,
                        lease_owner = $2, lease_token = $3,
                        lease_expires_at = NOW() + ($4 * INTERVAL '1 second'),
                        started_at = COALESCE(started_at, NOW()), error = NULL
                    WHERE id = $1
                    RETURNING *
                    """,
                    UUID(job_id),
                    worker_id,
                    lease_token,
                    lease_seconds,
                )
        return JobClaim("claimed", _record_to_job(claimed), lease_token)

    async def complete_job(self, job_id: str, lease_token: UUID, result: dict[str, Any]) -> bool:
        stored_result = await self._result_store.persist(job_id, result)
        pool = await self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    UPDATE workflow_jobs
                    SET status = 'succeeded', result = $3::jsonb, finished_at = NOW(),
                        lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL
                    WHERE id = $1 AND status = 'running' AND lease_token = $2
                    RETURNING tenant_id, actor_id, operation
                    """,
                    UUID(job_id),
                    lease_token,
                    json.dumps(stored_result, ensure_ascii=False),
                )
                if (
                    row is not None
                    and row["operation"] == "create_session"
                    and result.get("session_id")
                ):
                    await conn.execute(
                        """
                        INSERT INTO workflow_session_owners (
                            session_id, tenant_id, owner_subject
                        ) VALUES ($1, $2, $3)
                        ON CONFLICT (session_id) DO NOTHING
                        """,
                        UUID(result["session_id"]),
                        row["tenant_id"],
                        row["actor_id"],
                    )
        return row is not None

    async def renew_job_lease(
        self,
        job_id: str,
        lease_token: UUID,
        lease_seconds: int,
    ) -> bool:
        pool = await self._pool()
        async with pool.acquire() as conn:
            status = await conn.execute(
                """
                UPDATE workflow_jobs
                SET lease_expires_at = NOW() + ($3 * INTERVAL '1 second')
                WHERE id = $1 AND status = 'running' AND lease_token = $2
                """,
                UUID(job_id),
                lease_token,
                lease_seconds,
            )
        return status == "UPDATE 1"

    async def fail_job(self, job_id: str, lease_token: UUID, error: str) -> bool:
        pool = await self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE workflow_jobs
                SET status = CASE WHEN attempt_count >= max_attempts THEN 'failed' ELSE 'queued' END,
                    error = $3,
                    finished_at = CASE WHEN attempt_count >= max_attempts THEN NOW() ELSE NULL END,
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL
                WHERE id = $1 AND status = 'running' AND lease_token = $2
                RETURNING status
                """,
                UUID(job_id),
                lease_token,
                error[:8000],
            )
        return bool(row and row["status"] == "failed")
