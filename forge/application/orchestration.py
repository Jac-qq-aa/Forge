from __future__ import annotations

from typing import Any
from uuid import UUID

from forge.domain.jobs import WorkflowCommand
from forge.domain.models import Identity
from forge.infrastructure.telemetry import PostgresTraceRecorder, emit, error_fields, span, trace_context
from forge.infrastructure.langsmith_trace import continue_trace


class WorkflowCommandHandler:
    def __init__(self, runtime: Any, inbox: Any, *, consumer_group: str = "forge-workflow-workers") -> None:
        self._runtime = runtime
        self._inbox = inbox
        self._consumer_group = consumer_group

    async def handle(self, command: WorkflowCommand) -> None:
        job = await self._runtime.jobs.get(command.job_id)
        if job is None:
            return
        with trace_context(recorder=PostgresTraceRecorder(self._runtime.database),
                           trace_id=str(job["trace_id"]), job_id=str(command.job_id),
                           message_id=str(command.message_id), conversation_id=str(command.conversation_id),
                           identity={"user_id": str(command.actor_user_id), "tenant_id": str(command.tenant_id) if command.tenant_id else None}):
            async with span("consumer.handle", category="dispatch", attempt=job["attempts"] + 1):
                await self._handle(command, job)

    async def _handle(self, command: WorkflowCommand, job: dict[str, Any]) -> None:
        should_process = await self._inbox.begin(
            consumer_group=self._consumer_group,
            message_id=command.message_id,
            job_id=command.job_id,
        )
        if not should_process:
            await emit("message.deduplicated", reason="inbox_processed")
            return
        if job["conversation_id"] != command.conversation_id or job["kind"] != command.kind:
            raise ValueError("消息内容与任务记录不一致")
        if str(command.actor_user_id) != str(job["payload"].get("identity", {}).get("user_id")):
            raise PermissionError("消息调用者与任务记录不一致")
        if str(command.tenant_id) != str(job["payload"].get("identity", {}).get("tenant_id")):
            raise PermissionError("消息租户与任务记录不一致")
        if command.payload.get("_execution_id") != job["payload"].get("_execution_id"):
            await self._inbox.complete(consumer_group=self._consumer_group, message_id=command.message_id)
            await emit("message.deduplicated", reason="stale_execution")
            return
        await self._resolve_identity(command)
        await self._runtime.jobs.admit(command.job_id)
        await self._inbox.complete(consumer_group=self._consumer_group, message_id=command.message_id)
        await emit("job.admitted", category="dispatch", kind=command.kind)

    async def dispatch(self, job: dict[str, Any]) -> None:
        async with self._runtime.jobs.exclusive(job["id"]) as locked:
            if not locked:
                return
            job = await self._runtime.jobs.get(job["id"])
            if not job or job["status"] not in {"dispatching", "submission_unknown"}:
                return
            payload = dict(job["payload"])
            with continue_trace(payload.get("_trace_context")), trace_context(recorder=self._runtime.telemetry, trace_id=str(job["trace_id"]),
                               job_id=str(job["id"]), conversation_id=str(job["conversation_id"]),
                               identity=payload.get("identity")):
                try:
                    raw_identity = payload["identity"]
                    command = WorkflowCommand(job_id=job["id"], conversation_id=job["conversation_id"],
                        kind=job["kind"], payload=payload, actor_user_id=raw_identity["user_id"],
                        tenant_id=raw_identity.get("tenant_id"))
                    identity = await self._resolve_identity(command)
                    payload.update(identity=identity.model_dump(mode="json"), trace_id=str(job["trace_id"]), job_id=str(job["id"]))
                    if job["kind"] == "index_document":
                        result = await self._index_document(payload, identity)
                        await self._runtime.jobs.finish(job_id=job["id"], status="completed", result=result)
                    elif job["kind"] == "audit_job":
                        from forge.infrastructure.audit import TaskAuditor
                        result = await TaskAuditor(self._runtime.database).check(job["id"], payload, identity)
                        await self._runtime.jobs.finish(job_id=job["id"], status="completed", result=result)
                    else:
                        if payload.get("resume_decision") is not None and job["status"] == "submission_unknown":
                            submission = await self._runtime.executor.find_submission(
                                str(job["conversation_id"]), job["id"],
                                execution_id=payload["_execution_id"])
                            if submission is None:
                                await self._runtime.jobs.defer_submission(job["id"], "恢复提交状态待核对，已停止重复提交", 3)
                                return
                        elif job["status"] == "submission_unknown":
                            submission = await self._runtime.executor.find_submission(
                                str(job["conversation_id"]), job["id"],
                                execution_id=payload["_execution_id"])
                            if submission is None:
                                await self._runtime.jobs.defer_submission(job["id"], "提交状态待核对，已停止重复提交", 3)
                                return
                        elif payload.get("resume_decision") is not None:
                            submission = await self._runtime.executor.resume(job_id=job["id"], payload=payload)
                        else:
                            submission = await self._runtime.executor.start(job_id=job["id"], payload=payload)
                        await self._runtime.jobs.mark_submitted(job_id=job["id"],
                            remote_thread_id=submission["thread_id"], remote_run_id=submission["run_id"],
                            langsmith_trace_id=submission.get("langsmith_trace_id", ""),
                            execution_id=payload["_execution_id"])
                except PermissionError as exc:
                    await self._runtime.jobs.finish(job_id=job["id"], status="failed", error=str(exc))
                except Exception as exc:
                    await emit("submission.failed", status="failed", **error_fields(exc))
                    if job["kind"] != "start" and job["attempts"] + 1 >= self._runtime.settings.consumer_max_attempts:
                        await self._runtime.jobs.finish(job_id=job["id"], status="failed", error=type(exc).__name__)
                    else:
                        await self._runtime.jobs.defer_submission(job["id"], type(exc).__name__, self._runtime.settings.consumer_max_attempts)

    async def _resolve_identity(self, command: WorkflowCommand) -> Identity:
        async with self._runtime.database.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.user_id', $1, true), "
                    "set_config('app.tenant_id', $2, true), "
                    "set_config('app.space_ids', '', true)",
                    str(command.actor_user_id),
                    str(command.tenant_id or ""),
                )
                row = await connection.fetchrow(
                    """
                    SELECT u.id, u.account_type, c.tenant_id,
                           COALESCE(array_agg(DISTINCT m.role)
                               FILTER (WHERE m.active), '{}') AS roles,
                           COALESCE(array_agg(DISTINCT g.space_id)
                               FILTER (WHERE g.space_id IS NOT NULL), '{}') AS space_ids
                    FROM users u
                    JOIN conversations c ON c.owner_id=u.id AND c.id=$2
                    LEFT JOIN memberships m ON m.user_id=u.id
                        AND m.tenant_id=c.tenant_id AND m.active
                    LEFT JOIN resource_grants g ON g.user_id=u.id
                        AND g.permission IN ('read','manage')
                    WHERE u.id=$1
                    GROUP BY u.id, u.account_type, c.tenant_id
                    """,
                    command.actor_user_id,
                    command.conversation_id,
                )
        if row is None:
            raise PermissionError("用户已无权访问该对话")
        if command.tenant_id and command.tenant_id != row["tenant_id"]:
            raise PermissionError("任务租户与当前对话租户不一致")
        if row["tenant_id"] and not row["roles"]:
            raise PermissionError("用户的公司成员资格已失效")
        return Identity(
            user_id=row["id"],
            tenant_id=row["tenant_id"],
            account_type=row["account_type"],
            roles=frozenset(row["roles"]),
            knowledge_space_ids=frozenset(row["space_ids"]),
        )

    async def _index_document(
        self, payload: dict[str, Any], identity: Identity
    ) -> dict[str, Any]:
        if self._runtime.embeddings is None:
            raise RuntimeError("向量模型未初始化")
        indexed = 0
        for chunk in payload["chunks"]:
            vector = await self._runtime.embeddings.embed(chunk["content"])
            if len(vector) != self._runtime.settings.embedding_dimensions:
                raise RuntimeError(
                    f"向量维度 {len(vector)} 与数据库维度 "
                    f"{self._runtime.settings.embedding_dimensions} 不一致"
                )
            await self._runtime.store.save_embedding(
                chunk_id=UUID(chunk["chunk_id"]),
                vector=vector,
                model=self._runtime.embeddings.model,
                dimensions=len(vector),
                identity=identity,
            )
            indexed += 1
        return {"document_id": payload["document_id"], "indexed_chunks": indexed,
                "embedding_model": self._runtime.embeddings.model}


class WorkflowRunReconciler:
    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    async def reconcile_once(self) -> int:
        changed = 0
        for job in await self._runtime.jobs.reconcilable():
            with trace_context(recorder=self._runtime.telemetry, trace_id=str(job["trace_id"]),
                               job_id=str(job["id"]), conversation_id=str(job["conversation_id"]),
                               run_id=job["remote_run_id"], identity=job["payload"].get("identity")):
                try:
                    async with self._runtime.jobs.exclusive(job["id"]) as locked:
                        if locked:
                            current = await self._runtime.jobs.get(job["id"])
                            if current and current["status"] in {"submitted", "running"}:
                                changed += await self._reconcile_job(current)
                except Exception as exc:
                    await emit("reconcile.failed", status="failed", **error_fields(exc))
        return changed

    async def _reconcile_job(self, job: dict[str, Any]) -> int:
        run = await self._runtime.executor.status(
            thread_id=job["remote_thread_id"], run_id=job["remote_run_id"])
        status = run["status"]
        if status in {"pending", "running"}:
            if status == "running":
                await self._runtime.jobs.mark_running(job["id"])
            return 0
        async with span("langgraph.reconcile", category="dispatch", run_status=status):
            if status in {"success", "interrupted"}:
                state = await self._runtime.executor.state(thread_id=job["remote_thread_id"])
                result = dict(state["values"])
                paused = status == "interrupted" or bool(state["interrupts"] or state["next"])
                if state["interrupts"]:
                    interrupt_value = state["interrupts"][0].get("value", state["interrupts"][0])
                    result["pause_request"] = interrupt_value
                result["projection_run_id"] = str(job["remote_run_id"])
                async with span("workflow.project", category="persistence"):
                    await self._runtime.projection.project(result)
                await self._runtime.jobs.finish(
                    job_id=job["id"], status="waiting" if paused else "completed",
                    result={**result, "__interrupts__": state["interrupts"]})
                await emit("job.finished", status="paused" if paused else "succeeded",
                           stage=result.get("stage"), critic_status=result.get("critic_status"))
            else:
                state = await self._runtime.executor.state(thread_id=job["remote_thread_id"])
                result = state["values"]
                if result.get("job_id") == str(job["id"]):
                    await self._runtime.projection.project({**result, "stage": "needs_attention"})
                await self._runtime.jobs.finish(job_id=job["id"], status="failed", error=f"LangGraph run {status}")
                await emit("job.finished", status="failed", reason="langgraph_run_failed", run_status=status)
        return 1
