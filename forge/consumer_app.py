from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from forge.application.orchestration import WorkflowCommandHandler, WorkflowRunReconciler
from forge.bootstrap import build_runtime
from forge.domain.jobs import COMMAND_ROUTES, WorkflowCommand
from forge.config import get_settings
from forge.infrastructure.jobs import InboxRepository
from forge.infrastructure.langsmith_trace import continue_trace


class RocketMqDelivery(BaseModel):
    message_id: str
    topic: str
    consumer_group: str
    body: dict[str, Any]
    retry_count: int = 0
    trace_context: dict[str, str] = Field(default_factory=dict)


async def _reconcile_loop(app: FastAPI) -> None:
    while True:
        try:
            await app.state.reconciler.reconcile_once()
        except Exception:
            app.state.logger.exception("LangGraph run reconcile failed")
        await asyncio.sleep(app.state.runtime.settings.langgraph_reconcile_seconds)


async def _dispatch_loop(app: FastAPI) -> None:
    tasks = {}
    try:
        while True:
            for key, task in list(tasks.items()):
                if task.done():
                    try:
                        task.result()
                    except Exception:
                        app.state.logger.exception("任务提交异常")
                    del tasks[key]
            try:
                settings = app.state.runtime.settings
                capacity = {"start": settings.graph_max_concurrency,
                            "index_document": settings.index_max_concurrency,
                            "audit_job": settings.audit_max_concurrency}[settings.consumer_kind]
                jobs = await app.state.runtime.jobs.claim_capacity(capacity, kind=settings.consumer_kind)
                for job in jobs:
                    if job["id"] not in tasks:
                        tasks[job["id"]] = asyncio.create_task(app.state.handler.dispatch(job))
            except Exception:
                app.state.logger.exception("任务调度异常")
            await asyncio.sleep(1)
    finally:
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging

    runtime = await build_runtime(with_embeddings=get_settings().consumer_kind == "index_document")
    app.state.runtime = runtime
    app.state.logger = logging.getLogger(__name__)
    app.state.handler = WorkflowCommandHandler(runtime, InboxRepository(runtime.database),
        consumer_group=COMMAND_ROUTES[runtime.settings.consumer_kind][1])
    app.state.reconciler = WorkflowRunReconciler(runtime)
    reconcile_task = (asyncio.create_task(_reconcile_loop(app))
                      if runtime.settings.consumer_kind == "start" else None)
    dispatch_task = asyncio.create_task(_dispatch_loop(app))
    try:
        yield
    finally:
        if reconcile_task:
            reconcile_task.cancel()
        dispatch_task.cancel()
        with suppress(asyncio.CancelledError):
            await dispatch_task
        with suppress(asyncio.CancelledError):
            if reconcile_task:
                await reconcile_task
        await runtime.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Forge Runtime Consumer", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/internal/rocketmq")
    async def consume(
        delivery: RocketMqDelivery,
        request: Request,
        x_forge_consumer_secret: str = Header(default=""),
    ) -> dict[str, str]:
        expected = request.app.state.runtime.settings.rocketmq_consumer_secret
        if not expected or x_forge_consumer_secret != expected:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效的消费者凭据")
        command = WorkflowCommand.model_validate(delivery.body)
        settings = request.app.state.runtime.settings
        topic, group = COMMAND_ROUTES[settings.consumer_kind]
        if command.kind != settings.consumer_kind or delivery.topic != topic or delivery.consumer_group != group:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "消息不属于该 Worker 的任务类型、主题或消费组")
        if str(command.message_id) != delivery.message_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "消息 ID 不一致")
        with continue_trace(delivery.trace_context):
            await request.app.state.handler.handle(command)
        return {"status": "accepted"}

    return app


app = create_app()
