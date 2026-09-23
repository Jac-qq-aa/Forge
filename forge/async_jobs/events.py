"""通过 Redis Pub/Sub 向 SSE 客户端广播任务状态变化。"""

from __future__ import annotations

import json
import logging
import asyncio
from contextlib import asynccontextmanager, suppress
from collections.abc import AsyncIterator
from typing import Any

from forge.storage.redis_client import get_redis_client

from .repository import JobRepository

logger = logging.getLogger(__name__)
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def _channel(job_id: str) -> str:
    return f"job-events:{job_id}"


class JobEventHub:
    """每个进程复用一个 Redis 模式订阅，并在进程内分发给 SSE。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._closed = False

    async def start(self) -> None:
        async with self._lock:
            if self._closed:
                raise RuntimeError("任务事件中心已关闭")
            if self._task is None or self._task.done():
                self._ready.clear()
                self._task = asyncio.create_task(self._run())
        await asyncio.wait_for(self._ready.wait(), timeout=5.0)

    async def _run(self) -> None:
        while not self._closed:
            pubsub = None
            try:
                client = await get_redis_client()
                pubsub = client.pubsub()
                await pubsub.psubscribe("job-events:*")
                self._ready.set()
                self._broadcast_resync()
                while not self._closed:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=1.0,
                    )
                    if message is None:
                        continue
                    channel = str(message["channel"])
                    job_id = channel.rsplit(":", 1)[-1]
                    event = json.loads(message["data"])
                    for queue in tuple(self._subscribers.get(job_id, ())):
                        self._put_latest(queue, event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("任务事件中心 Redis 订阅中断，准备重连")
                self._ready.clear()
                await asyncio.sleep(1)
            finally:
                if pubsub is not None:
                    with suppress(Exception):
                        await pubsub.aclose()

    @staticmethod
    def _put_latest(
        queue: asyncio.Queue[dict[str, Any]],
        event: dict[str, Any],
    ) -> None:
        if queue.full():
            with suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        queue.put_nowait(event)

    def _broadcast_resync(self) -> None:
        for subscribers in tuple(self._subscribers.values()):
            for queue in tuple(subscribers):
                self._put_latest(queue, {"resync": True})

    @asynccontextmanager
    async def subscribe(self, job_id: str):
        await self.start()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=4)
        self._subscribers.setdefault(job_id, set()).add(queue)
        try:
            yield queue
        finally:
            subscribers = self._subscribers.get(job_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(job_id, None)

    async def close(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None


_event_hub = JobEventHub()


async def close_job_event_hub() -> None:
    global _event_hub
    hub = _event_hub
    _event_hub = JobEventHub()
    await hub.close()


async def publish_job_event(
    job_id: str,
    tenant_id: str,
    actor_id: str,
    status: str,
) -> None:
    """发布轻量通知；任务真值仍从 PostgreSQL 读取。"""
    try:
        client = await get_redis_client()
        await client.publish(
            _channel(job_id),
            json.dumps(
                {
                    "job_id": job_id,
                    "tenant_id": tenant_id,
                    "actor_id": actor_id,
                    "status": status,
                },
                ensure_ascii=False,
            ),
        )
    except Exception:
        logger.exception("发布任务状态事件失败 job_id=%s status=%s", job_id, status)


def _sse_job(job: dict[str, Any]) -> str:
    return f"event: job\ndata: {json.dumps(job, ensure_ascii=False)}\n\n"


async def stream_job_events(
    job_id: str,
    tenant_id: str,
    actor_id: str,
    repository: JobRepository | None = None,
) -> AsyncIterator[str]:
    """订阅后读取初始状态，避免状态变化发生在订阅窗口之外。"""
    repository = repository or JobRepository()
    async with _event_hub.subscribe(job_id) as event_queue:
        job = await repository.get_job(job_id, tenant_id, actor_id)
        if job is None:
            yield "event: error\ndata: {\"detail\": \"Job not found\"}\n\n"
            return
        yield _sse_job(job)
        if job["status"] in TERMINAL_STATUSES:
            return

        while True:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if not event.get("resync") and (
                event.get("tenant_id") != tenant_id
                or event.get("actor_id") != actor_id
            ):
                continue
            job = await repository.get_job(job_id, tenant_id, actor_id)
            if job is None:
                yield "event: error\ndata: {\"detail\": \"Job not found\"}\n\n"
                return
            yield _sse_job(job)
            if job["status"] in TERMINAL_STATUSES:
                return
