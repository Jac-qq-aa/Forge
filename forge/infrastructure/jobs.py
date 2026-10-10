from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Sequence
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from forge.domain.jobs import WorkflowCommand
from forge.infrastructure.postgres import Database
from forge.infrastructure.telemetry import PostgresTraceRecorder, current_trace_id, emit, trace_context
from forge.infrastructure.langsmith_trace import current_headers


class JobRepository:
    """业务任务、Outbox 与远程 run 映射的唯一持久化入口。"""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def enqueue(
        self,
        *,
        job_id: UUID,
        conversation_id: UUID,
        kind: str,
        payload: dict[str, Any],
        stage: str | None = None,
        actor_id: UUID | None = None,
        trace_id: UUID | None = None,
    ) -> bool:
        identity = payload.get("identity") or {}
        resolved_actor = actor_id or identity.get("user_id")
        if resolved_actor is None:
            raise ValueError("创建任务必须提供调用者")
        actor_uuid = UUID(str(resolved_actor))
        tenant_value = identity.get("tenant_id")
        command_data: dict[str, Any] = {
            "job_id": job_id,
            "conversation_id": conversation_id,
            "tenant_id": UUID(str(tenant_value)) if tenant_value else None,
            "actor_user_id": actor_uuid,
            "kind": kind,
            "payload": payload,
            "trace_context": current_headers(),
        }
        if trace_id is not None:
            command_data["trace_id"] = trace_id
        elif payload.get("trace_id"):
            command_data["trace_id"] = UUID(str(payload["trace_id"]))
        elif current_trace_id() is not None:
            command_data["trace_id"] = current_trace_id()
        command = WorkflowCommand.model_validate(command_data)
        payload = {**payload, "_trace_context": command.trace_context,
                   "_execution_id": str(uuid4())}
        command.payload = payload
        async with self._database.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 31))", str(conversation_id))
                if await connection.fetchval("SELECT 1 FROM workflow_jobs WHERE id=$1", job_id):
                    return False
                if await connection.fetchval(
                    """SELECT 1 FROM workflow_jobs WHERE conversation_id=$1 AND kind<>'audit_job'
                       AND status IN ('pending','queued','dispatching','submission_unknown','submitted','running','waiting') LIMIT 1""",
                    conversation_id,
                ):
                    raise RuntimeError("当前对话有执行中的任务，请等待完成后再操作")
                inserted = await connection.fetchval(
                    """
                    INSERT INTO workflow_jobs (
                        id, conversation_id, kind, payload, status, trace_id
                    ) VALUES ($1,$2,$3,$4::jsonb,'pending',$5)
                    ON CONFLICT (id) DO NOTHING RETURNING id
                    """,
                    job_id,
                    conversation_id,
                    kind,
                    payload,
                    command.trace_id,
                )
                if inserted is None:
                    return False
                if stage is not None:
                    await connection.execute(
                        "SELECT set_config('app.user_id', $1, true)", str(actor_uuid)
                    )
                    updated = await connection.fetchval(
                        """
                        UPDATE conversations SET stage=$2::article_stage, updated_at=now()
                        WHERE id=$1 AND owner_id=$3 RETURNING id
                        """,
                        conversation_id,
                        stage,
                        actor_uuid,
                    )
                    if updated is None:
                        raise LookupError("对话不存在或无权更新")
                await connection.execute(
                    """
                    INSERT INTO job_outbox (id, job_id, topic, message_key, payload)
                    VALUES ($1,$2,$3,$4,$5::jsonb)
                    """,
                    command.message_id,
                    job_id,
                    command.topic,
                    str(conversation_id),
                    command.transport_payload(),
                )
                await connection.execute(
                    """
                    INSERT INTO workflow_events (conversation_id, job_id, event_type, payload)
                    VALUES ($1,$2,'queued',$3::jsonb)
                    """,
                    conversation_id,
                    job_id,
                    {"trace_id": str(command.trace_id), "topic": command.topic},
                )
        with trace_context(recorder=PostgresTraceRecorder(self._database), trace_id=str(command.trace_id),
                           job_id=str(job_id), message_id=str(command.message_id), conversation_id=str(conversation_id),
                           identity={**identity, "user_id": str(actor_uuid)}):
            await emit("job.queued", category="dispatch", kind=kind, topic=command.topic)
        return True

    async def get(self, job_id: UUID) -> dict[str, Any] | None:
        async with self._database.pool.acquire() as connection:
            row = await connection.fetchrow("SELECT * FROM workflow_jobs WHERE id=$1", job_id)
            return dict(row) if row else None

    async def resume_waiting(self, *, job_id: UUID, identity: Any, decision: dict[str, Any]) -> bool:
        async with self._database.authorized(identity) as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """SELECT j.* FROM workflow_jobs j JOIN conversations c ON c.id=j.conversation_id
                       WHERE j.id=$1 AND j.conversation_id=$2 AND c.owner_id=$3
                       FOR UPDATE OF j""", job_id, UUID(str(decision["conversation_id"])), identity.user_id)
                if row is None or row["status"] != "waiting":
                    raise RuntimeError("没有可恢复的人工暂停任务")
                if str((row["payload"].get("identity") or {}).get("user_id")) != str(identity.user_id):
                    raise PermissionError("任务调用者与当前用户不一致")
                pause = (row["result"] or {}).get("pause_request")
                if not isinstance(pause, dict):
                    raise RuntimeError("任务当前没有等待人工处理的节点")
                if decision["decision"].get("fact_version") != pause.get("fact_version"):
                    raise RuntimeError("暂停节点事实版本不匹配，请刷新工作区")
                expected_kinds = {"facts": {"confirm"}, "outline": {"confirm"},
                                  "plan_conflicts": {"decisions"}, "critic_conflicts": {"decisions"}}
                pause_kind = pause.get("kind")
                marker = "action" if pause_kind in {"facts", "outline"} else "decisions"
                if pause_kind not in expected_kinds or marker not in decision["decision"]:
                    raise RuntimeError("裁决类型与当前暂停节点不匹配")
                await connection.execute(
                    """INSERT INTO workflow_user_actions
                       (job_id,conversation_id,actor_user_id,pause_kind,fact_version,decision)
                       VALUES ($1,$2,$3,$4,$5,$6::jsonb)""",
                    job_id, row["conversation_id"], identity.user_id, pause_kind,
                    int(pause["fact_version"]), decision["decision"])
                payload = dict(row["payload"])
                payload["resume_decision"] = decision["decision"]
                payload["_trace_context"] = current_headers()
                payload["_execution_id"] = str(uuid4())
                await connection.execute(
                    "UPDATE workflow_jobs SET status='pending',payload=$2::jsonb,result=NULL,error=NULL,updated_at=now() WHERE id=$1",
                    job_id, payload)
                await connection.execute("UPDATE conversations SET stage='planning',updated_at=now() WHERE id=$1",
                                         row["conversation_id"])
                await connection.execute(
                    """INSERT INTO workflow_events(conversation_id,job_id,event_type,payload)
                       VALUES ($1,$2,'user_decision_submitted',$3::jsonb)""",
                    row["conversation_id"], job_id,
                    {"pause_kind": pause_kind, "fact_version": pause["fact_version"]})
                from forge.domain.jobs import WorkflowCommand
                command = WorkflowCommand.model_validate({"job_id": job_id, "conversation_id": row["conversation_id"],
                    "tenant_id": identity.tenant_id, "actor_user_id": identity.user_id,
                    "kind": row["kind"], "payload": payload, "trace_id": row["trace_id"],
                    "trace_context": payload["_trace_context"]})
                await connection.execute(
                    """INSERT INTO job_outbox(id,job_id,topic,message_key,payload)
                       VALUES ($1,$2,$3,$4,$5::jsonb)""",
                    command.message_id, job_id, command.topic, str(row["conversation_id"]), command.transport_payload())
                return True

    async def mark_submitted(
        self,
        *,
        job_id: UUID,
        remote_thread_id: str,
        remote_run_id: str,
        langsmith_trace_id: str = "",
        execution_id: str = "",
    ) -> bool:
        async with self._database.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    UPDATE workflow_jobs
                    SET status='submitted',
                        remote_thread_id=$2, remote_run_id=$3,
                        submitted_at=now(), error=NULL, updated_at=now()
                    WHERE id=$1 AND status IN ('dispatching','submission_unknown')
                    RETURNING conversation_id, attempts
                    """,
                    job_id,
                    remote_thread_id,
                    remote_run_id,
                )
                if row is None:
                    return False
                await connection.execute(
                    """
                    INSERT INTO workflow_events (conversation_id, job_id, event_type, payload)
                    VALUES ($1,$2,'submitted',$3::jsonb)
                    """,
                    row["conversation_id"],
                    job_id,
                    {
                        "attempt": row["attempts"],
                        "remote_thread_id": remote_thread_id,
                        "remote_run_id": remote_run_id,
                        "langsmith_trace_id": langsmith_trace_id,
                        "execution_id": execution_id,
                    },
                )
                return True

    async def finish(
        self,
        *,
        job_id: UUID,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        if status not in {"waiting", "completed", "failed", "cancelled"}:
            raise ValueError(f"不支持的任务终态: {status}")
        async with self._database.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    UPDATE workflow_jobs
                    SET status=$2, result=$3::jsonb, error=$4, updated_at=now()
                    WHERE id=$1 AND status IN ('pending','queued','dispatching','submission_unknown','submitted','running','waiting')
                    RETURNING conversation_id,kind,payload,trace_id,remote_run_id
                    """,
                    job_id,
                    status,
                    result,
                    error,
                )
                if row is None:
                    return False
                if status in {"failed", "cancelled"} and row["kind"] == "start":
                    actor = await connection.fetchval("SELECT payload->'identity'->>'user_id' FROM workflow_jobs WHERE id=$1", job_id)
                    await connection.execute("SELECT set_config('app.user_id', $1, true)", actor or "")
                    await connection.execute(
                        "UPDATE conversations SET stage=$2::article_stage, updated_at=now() WHERE id=$1",
                        row["conversation_id"], status,
                    )
                await connection.execute(
                    """
                    INSERT INTO workflow_events (conversation_id, job_id, event_type, payload)
                    VALUES ($1,$2,$3,$4::jsonb)
                    """,
                    row["conversation_id"],
                    job_id,
                    status,
                    {"error": error} if error else {},
                )
                if status != "waiting" and row["kind"] != "audit_job":
                    await self._enqueue_audit(connection, job_id, dict(row), status)
                return True

    async def _enqueue_audit(self, connection: Any, source_job_id: UUID,
                             source: dict[str, Any], status: str) -> None:
        identity = source["payload"]["identity"]
        execution_id = source["payload"]["_execution_id"]
        audit_id = uuid5(NAMESPACE_URL, f"forge:audit:{source_job_id}:{execution_id}:{status}")
        payload = {"identity": identity, "conversation_id": str(source["conversation_id"]),
                   "source_job_id": str(source_job_id), "source_execution_id": execution_id,
                   "source_run_id": source["remote_run_id"], "source_status": status,
                   "check_type": "terminal_integrity", "check_version": 1,
                   "_execution_id": str(audit_id), "_trace_context": current_headers()}
        inserted = await connection.fetchval(
            """INSERT INTO workflow_jobs(id,conversation_id,kind,payload,status,trace_id)
               VALUES ($1,$2,'audit_job',$3::jsonb,'pending',$4)
               ON CONFLICT (id) DO NOTHING RETURNING id""",
            audit_id, source["conversation_id"], payload, source["trace_id"])
        if inserted:
            command = WorkflowCommand(job_id=audit_id, conversation_id=source["conversation_id"],
                actor_user_id=identity["user_id"], tenant_id=identity.get("tenant_id"),
                kind="audit_job", payload=payload, trace_id=source["trace_id"],
                trace_context=payload["_trace_context"])
            await connection.execute(
                """INSERT INTO job_outbox(id,job_id,topic,message_key,payload)
                   VALUES ($1,$2,$3,$4,$5::jsonb)""", command.message_id, audit_id,
                command.topic, str(source["conversation_id"]), command.transport_payload())

    async def admit(self, job_id: UUID) -> None:
        async with self._database.pool.acquire() as connection:
            await connection.execute(
                "UPDATE workflow_jobs SET status='queued', updated_at=now() WHERE id=$1 AND status='pending'", job_id)

    @asynccontextmanager
    async def exclusive(self, job_id: UUID):
        # 会话级锁覆盖远程提交/投影；进程退出由 PostgreSQL 自动释放。
        async with self._database.pool.acquire() as connection:
            locked = await connection.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1, 17))", str(job_id))
            try:
                yield bool(locked)
            finally:
                if locked:
                    await connection.execute("SELECT pg_advisory_unlock(hashtextextended($1, 17))", str(job_id))

    async def claim_capacity(self, capacity: int, *, kind: str) -> list[dict[str, Any]]:
        if kind not in {"start", "index_document", "audit_job"} or capacity < 1:
            raise ValueError("无效的 Worker 类型或容量")
        async with self._database.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock(734618201)")
                for job_kind, limit in ((kind, capacity),):
                    active = await connection.fetchval(
                        """SELECT count(*) FROM workflow_jobs WHERE kind=$1
                           AND status IN ('dispatching','submission_unknown','submitted','running')""", job_kind)
                    for _ in range(max(0, limit - active)):
                        row = await connection.fetchrow(
                            """SELECT j.id FROM workflow_jobs j WHERE j.status='queued' AND j.kind=$1
                               AND NOT EXISTS (SELECT 1 FROM workflow_jobs other
                                  WHERE other.conversation_id=j.conversation_id AND other.id<>j.id AND other.kind=j.kind
                                  AND other.status IN ('dispatching','submission_unknown','submitted','running'))
                               ORDER BY j.created_at FOR UPDATE SKIP LOCKED LIMIT 1""", job_kind)
                        if row is None:
                            break
                        await connection.execute("UPDATE workflow_jobs SET status='dispatching', updated_at=now() WHERE id=$1", row["id"])
                rows = await connection.fetch(
                    """SELECT * FROM workflow_jobs WHERE status IN ('dispatching','submission_unknown')
                       AND kind=$2 AND retry_at<=now() ORDER BY created_at LIMIT $1""", capacity, kind)
                return [dict(row) for row in rows]

    async def defer_submission(self, job_id: UUID, error: str, max_attempts: int) -> None:
        async with self._database.pool.acquire() as connection:
            await connection.execute(
                """UPDATE workflow_jobs SET attempts=attempts+1, error=$2,
                   status=CASE WHEN attempts+1 >= $3 THEN 'submission_unknown' ELSE 'dispatching' END,
                   retry_at=now()+interval '30 seconds', updated_at=now()
                   WHERE id=$1 AND status IN ('dispatching','submission_unknown')""", job_id, error, max_attempts)

    async def mark_running(self, job_id: UUID) -> None:
        async with self._database.pool.acquire() as connection:
            await connection.execute("UPDATE workflow_jobs SET status='running', updated_at=now() WHERE id=$1 AND status='submitted'", job_id)

    async def reconcilable(self, *, limit: int = 100) -> Sequence[dict[str, Any]]:
        async with self._database.pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT * FROM workflow_jobs
                WHERE status IN ('submitted','running') AND remote_run_id IS NOT NULL
                ORDER BY submitted_at LIMIT $1
                """,
                min(max(limit, 1), 500),
            )
            return [dict(row) for row in rows]


class OutboxRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def claim(self, *, limit: int = 50) -> list[dict[str, Any]]:
        async with self._database.pool.acquire() as connection:
            async with connection.transaction():
                rows = await connection.fetch(
                    """
                    WITH selected AS (
                        SELECT id FROM job_outbox
                        WHERE (status='pending' AND available_at <= now())
                           OR (status='publishing' AND locked_at < now() - interval '5 minutes')
                        ORDER BY created_at
                        FOR UPDATE SKIP LOCKED LIMIT $1
                    )
                    UPDATE job_outbox o
                    SET status='publishing', attempts=attempts+1,
                        locked_at=now(), updated_at=now()
                    FROM selected s WHERE o.id=s.id
                    RETURNING o.*
                    """,
                    min(max(limit, 1), 200),
                )
                return [dict(row) for row in rows]

    async def published(self, outbox_id: UUID, transport_message_id: str) -> None:
        async with self._database.pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE job_outbox
                SET status='published', transport_message_id=$2,
                    published_at=now(), locked_at=NULL, error=NULL, updated_at=now()
                WHERE id=$1 AND status='publishing'
                """,
                outbox_id,
                transport_message_id,
            )

    async def retry(
        self,
        outbox_id: UUID,
        *,
        error: str,
        attempts: int,
        max_attempts: int,
    ) -> None:
        terminal = attempts >= max_attempts
        delay = min(60, 2 ** min(attempts, 6))
        async with self._database.pool.acquire() as connection:
            async with connection.transaction():
                job_id = await connection.fetchval(
                    """UPDATE job_outbox SET status=$2, error=$3, locked_at=NULL,
                       available_at=now() + $4::interval, updated_at=now()
                       WHERE id=$1 AND status='publishing' RETURNING job_id""",
                    outbox_id, "failed" if terminal else "pending", error, timedelta(seconds=delay))
                if terminal and job_id:
                    row = await connection.fetchrow(
                        """UPDATE workflow_jobs SET status='failed', error=$2, updated_at=now()
                           WHERE id=$1 AND status='pending' RETURNING conversation_id,kind,payload,trace_id,remote_run_id""", job_id, error)
                    if row:
                        await connection.execute("SELECT set_config('app.user_id',$1,true)", row["payload"]["identity"]["user_id"])
                        if row["kind"] == "start":
                            await connection.execute("UPDATE conversations SET stage='failed',updated_at=now() WHERE id=$1", row["conversation_id"])
                        await connection.execute(
                            """INSERT INTO workflow_events(conversation_id,job_id,event_type,payload)
                               VALUES ($1,$2,'failed',$3::jsonb)""", row["conversation_id"], job_id,
                            {"error": "消息投递达到重试上限，请检查消息服务后重新发起"})
                        if row["kind"] != "audit_job":
                            await JobRepository(self._database)._enqueue_audit(connection, job_id, dict(row), "failed")


class InboxRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def begin(self, *, consumer_group: str, message_id: UUID, job_id: UUID) -> bool:
        async with self._database.pool.acquire() as connection:
            inserted = await connection.fetchval(
                """
                INSERT INTO message_inbox (consumer_group, message_id, job_id)
                VALUES ($1,$2,$3)
                ON CONFLICT DO NOTHING RETURNING message_id
                """,
                consumer_group,
                message_id,
                job_id,
            )
            if inserted is not None:
                return True
            status = await connection.fetchval(
                """
                SELECT status FROM message_inbox
                WHERE consumer_group=$1 AND message_id=$2
                """,
                consumer_group,
                message_id,
            )
            return status != "processed"

    async def complete(self, *, consumer_group: str, message_id: UUID) -> None:
        async with self._database.pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE message_inbox
                SET status='processed', processed_at=now()
                WHERE consumer_group=$1 AND message_id=$2
                """,
                consumer_group,
                message_id,
            )
