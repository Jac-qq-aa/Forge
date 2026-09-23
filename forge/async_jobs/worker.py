"""Outbox 发布器与 RocketMQ 工作流消费者进程。"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import time
from contextlib import suppress
from collections.abc import Awaitable, Callable
from typing import Any

from .config import (
    ASYNC_SERVICE_ROLE,
    JOB_BUSY_RETRY_SECONDS,
    JOB_CONSUMER_BATCH_SIZE,
    JOB_CONSUMER_CONCURRENCY,
    JOB_CONSUMER_PREFETCH,
    JOB_FAILURE_RETRY_SECONDS,
    JOB_LEASE_SECONDS,
    JOB_MESSAGE_INVISIBLE_SECONDS,
    JOB_RETENTION_DAYS,
    OUTBOX_BATCH_SIZE,
    OUTBOX_LOCK_SECONDS,
    OUTBOX_MAX_ATTEMPTS,
    OUTBOX_POLL_INTERVAL_SECONDS,
    OUTBOX_PUBLISH_CONCURRENCY,
    OUTBOX_RETENTION_HOURS,
    ROCKETMQ_WORKFLOW_CONSUMER_GROUP,
    ROCKETMQ_WORKFLOW_TOPIC,
    ROCKETMQ_GENERATION_RETRY_TOPIC,
    ROCKETMQ_GENERATION_TOPIC,
    ROCKETMQ_ZHIHU_CONSUMER_GROUP,
    ROCKETMQ_ZHIHU_TOPIC,
    WORKER_SHUTDOWN_GRACE_SECONDS,
    ZHIHU_JOB_CONCURRENCY,
)
from .repository import JobRepository
from .rocketmq import RocketMQConsumer, RocketMQPublisher
from .workflow_executor import execute_workflow_job
from .events import publish_job_event
from forge.observability.logging import configure_logging
from forge.observability.metrics import (
    JOB_DURATION,
    JOB_EXECUTIONS,
    OUTBOX_PUBLISH,
    WORKER_IN_FLIGHT,
    start_worker_metrics_server,
)

configure_logging(f"async-{ASYNC_SERVICE_ROLE}")
logger = logging.getLogger("forge.async_jobs.worker")


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def run_outbox(stop: asyncio.Event) -> None:
    if OUTBOX_PUBLISH_CONCURRENCY <= 0:
        raise ValueError("Outbox 发布并发数必须大于 0")
    if OUTBOX_LOCK_SECONDS <= 0 or OUTBOX_MAX_ATTEMPTS <= 0:
        raise ValueError("Outbox 锁时长和最大尝试次数必须大于 0")
    owner = _worker_id()
    repository = JobRepository()
    publisher = RocketMQPublisher(
        (
            ROCKETMQ_WORKFLOW_TOPIC,
            ROCKETMQ_ZHIHU_TOPIC,
            ROCKETMQ_GENERATION_TOPIC,
            ROCKETMQ_GENERATION_RETRY_TOPIC,
        )
    )
    await publisher.start()
    next_cleanup_at = time.monotonic()
    logger.info("Outbox 发布器已启动 owner=%s", owner)
    try:
        while not stop.is_set():
            if time.monotonic() >= next_cleanup_at:
                try:
                    await repository.cleanup(JOB_RETENTION_DAYS, OUTBOX_RETENTION_HOURS)
                except Exception:
                    logger.exception("清理历史异步任务失败")
                next_cleanup_at = time.monotonic() + 600
            rows = await repository.claim_outbox(
                owner,
                OUTBOX_BATCH_SIZE,
                OUTBOX_LOCK_SECONDS,
            )
            if not rows:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop.wait(), OUTBOX_POLL_INTERVAL_SECONDS)
                continue
            semaphore = asyncio.Semaphore(OUTBOX_PUBLISH_CONCURRENCY)

            async def publish(row: dict[str, Any]) -> None:
                async with semaphore:
                    try:
                        delivery_timestamp = row.get("delivery_timestamp")
                        if delivery_timestamp is None:
                            await publisher.publish(
                                row["topic"], row["payload"], row["message_key"]
                            )
                        else:
                            await publisher.publish(
                                row["topic"],
                                row["payload"],
                                row["message_key"],
                                delivery_timestamp_seconds=int(
                                    delivery_timestamp.timestamp()
                                ),
                            )
                        await repository.mark_outbox_published(row["id"], owner)
                        OUTBOX_PUBLISH.labels("succeeded").inc()
                    except Exception as exc:
                        OUTBOX_PUBLISH.labels("failed").inc()
                        logger.exception("Outbox 发布失败 id=%s", row["id"])
                        dead_lettered = await repository.mark_outbox_failed(
                            row["id"],
                            owner,
                            str(exc),
                            OUTBOX_MAX_ATTEMPTS,
                        )
                        if dead_lettered:
                            OUTBOX_PUBLISH.labels("dead_lettered").inc()
                            logger.error("Outbox 已进入死信 id=%s", row["id"])

            await asyncio.gather(*(publish(row) for row in rows))
    finally:
        await publisher.close()


async def _handle_message(
    message,
    consumer: RocketMQConsumer,
    repository: JobRepository,
    worker_id: str,
    semaphore: asyncio.Semaphore,
    executor: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    resource_name: str = "工作流",
) -> None:
    async with semaphore:
        executor = executor or execute_workflow_job
        try:
            envelope = consumer.decode(message)
            job_id = envelope["job_id"]
        except Exception:
            logger.exception("收到无法解析的 RocketMQ 消息")
            await consumer.acknowledge(message)
            return

        try:
            claim = await repository.claim_job(job_id, worker_id, JOB_LEASE_SECONDS)
        except Exception:
            logger.exception("领取%s任务失败 job_id=%s", resource_name, job_id)
            return
        if claim.action in {"missing", "terminal"}:
            try:
                await consumer.acknowledge(message)
            except Exception:
                logger.exception("终态消息 ACK 失败 job_id=%s", job_id)
            return
        if claim.action == "busy":
            try:
                await consumer.extend_visibility(message, JOB_BUSY_RETRY_SECONDS)
            except Exception:
                logger.exception("忙碌任务调整重试时间失败 job_id=%s", job_id)
            return

        await publish_job_event(
            job_id,
            claim.job["tenant_id"],
            claim.job["actor_id"],
            "running",
        )

        operation = str(claim.job.get("operation", "unknown"))
        started_at = time.perf_counter()
        WORKER_IN_FLIGHT.labels(resource_name).inc()
        heartbeat_stop = asyncio.Event()
        heartbeat = asyncio.create_task(
            _renew_lease(
                repository,
                job_id,
                claim.lease_token,
                consumer,
                message,
                heartbeat_stop,
            )
        )
        try:
            result = await executor(claim.job)
            committed = await repository.complete_job(job_id, claim.lease_token, result)
        except Exception as exc:
            JOB_EXECUTIONS.labels(resource_name, operation, "failed").inc()
            logger.exception("%s任务执行失败 job_id=%s", resource_name, job_id)
            terminal = await repository.fail_job(job_id, claim.lease_token, str(exc))
            await publish_job_event(
                job_id,
                claim.job["tenant_id"],
                claim.job["actor_id"],
                "failed" if terminal else "queued",
            )
            if terminal:
                try:
                    await consumer.acknowledge(message)
                except Exception:
                    logger.exception("失败终态消息 ACK 失败 job_id=%s", job_id)
            else:
                try:
                    await consumer.extend_visibility(
                        message,
                        JOB_FAILURE_RETRY_SECONDS,
                    )
                except Exception:
                    logger.exception("失败任务调整重试时间失败 job_id=%s", job_id)
        else:
            if not committed:
                JOB_EXECUTIONS.labels(resource_name, operation, "lease_lost").inc()
                logger.warning("任务结果未提交，租约已失效 job_id=%s", job_id)
            else:
                JOB_EXECUTIONS.labels(resource_name, operation, "succeeded").inc()
                await publish_job_event(
                    job_id,
                    claim.job["tenant_id"],
                    claim.job["actor_id"],
                    "succeeded",
                )
                try:
                    await consumer.acknowledge(message)
                except Exception:
                    logger.exception("成功终态消息 ACK 失败 job_id=%s", job_id)
        finally:
            JOB_DURATION.labels(resource_name, operation).observe(
                time.perf_counter() - started_at
            )
            WORKER_IN_FLIGHT.labels(resource_name).dec()
            heartbeat_stop.set()
            await heartbeat


async def _renew_lease(
    repository: JobRepository,
    job_id: str,
    lease_token,
    consumer: RocketMQConsumer,
    message,
    stop: asyncio.Event,
) -> None:
    interval = max(5, JOB_LEASE_SECONDS // 3)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), interval)
            return
        except asyncio.TimeoutError:
            try:
                renewed = await repository.renew_job_lease(
                    job_id,
                    lease_token,
                    JOB_LEASE_SECONDS,
                )
                if not renewed:
                    logger.warning("任务租约续期失败 job_id=%s", job_id)
                    return
                await consumer.extend_visibility(
                    message,
                    JOB_MESSAGE_INVISIBLE_SECONDS,
                )
            except Exception:
                logger.exception("任务租约续期异常 job_id=%s", job_id)


async def run_consumer(
    stop: asyncio.Event,
    *,
    topic: str | tuple[str, ...] = ROCKETMQ_WORKFLOW_TOPIC,
    consumer_group: str = ROCKETMQ_WORKFLOW_CONSUMER_GROUP,
    concurrency: int = JOB_CONSUMER_CONCURRENCY,
    prefetch: int = JOB_CONSUMER_PREFETCH,
    executor: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    resource_name: str = "工作流",
) -> None:
    if concurrency <= 0:
        raise ValueError("消费者并发数必须大于 0")
    if prefetch < 0:
        raise ValueError("消费者预取数不能小于 0")
    worker_id = _worker_id()
    executor = executor or execute_workflow_job
    repository = JobRepository()
    consumer = RocketMQConsumer(
        topic,
        consumer_group,
    )
    semaphore = asyncio.Semaphore(concurrency)
    max_in_flight = concurrency + prefetch
    running: set[asyncio.Task] = set()
    await consumer.start()
    logger.info("%s消费者已启动 worker=%s", resource_name, worker_id)
    try:
        while not stop.is_set():
            while len(running) >= max_in_flight:
                await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            capacity = max_in_flight - len(running)
            messages = await consumer.receive(
                min(JOB_CONSUMER_BATCH_SIZE, capacity),
                JOB_MESSAGE_INVISIBLE_SECONDS,
            )
            for message in messages:
                task = asyncio.create_task(
                    _handle_message(
                        message,
                        consumer,
                        repository,
                        worker_id,
                        semaphore,
                        executor,
                        resource_name,
                    )
                )
                running.add(task)
                task.add_done_callback(running.discard)
        if running:
            await asyncio.gather(*running, return_exceptions=True)
    finally:
        await consumer.close()


async def run_zhihu_consumer(stop: asyncio.Event) -> None:
    from forge.scraping.zhihu_executor import (
        close_zhihu_resources,
        execute_zhihu_job,
    )

    try:
        await run_consumer(
            stop,
            topic=ROCKETMQ_ZHIHU_TOPIC,
            consumer_group=ROCKETMQ_ZHIHU_CONSUMER_GROUP,
            concurrency=ZHIHU_JOB_CONCURRENCY,
            executor=execute_zhihu_job,
            resource_name="知乎采集",
        )
    finally:
        await close_zhihu_resources()


async def run_workflow_consumer(stop: asyncio.Event) -> None:
    from forge.scraping.zhihu_executor import close_zhihu_resources

    try:
        await run_consumer(stop)
    finally:
        await close_zhihu_resources()


async def run_generation_services(stop: asyncio.Event) -> None:
    from .generation_worker import run_generation_consumer, run_generation_reconciler

    worker_id = _worker_id()
    consumer = asyncio.create_task(run_generation_consumer(stop, worker_id))
    reconciler = asyncio.create_task(run_generation_reconciler(stop))
    try:
        done, _ = await asyncio.wait(
            (consumer, reconciler), return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            task.result()
    finally:
        consumer.cancel()
        reconciler.cancel()
        await asyncio.gather(consumer, reconciler, return_exceptions=True)


async def main() -> None:
    stop = asyncio.Event()
    metrics_port = start_worker_metrics_server()
    logger.info("Worker 指标服务已启动 port=%s", metrics_port)
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:
            pass
    roles = {
        "outbox": [run_outbox],
        "worker": [run_workflow_consumer],
        "generation-worker": [run_generation_services],
        "zhihu-worker": [run_zhihu_consumer],
        "all": [run_outbox, run_workflow_consumer, run_generation_services],
    }
    if ASYNC_SERVICE_ROLE not in roles:
        raise ValueError(f"ASYNC_SERVICE_ROLE 非法: {ASYNC_SERVICE_ROLE}")
    tasks = [asyncio.create_task(role(stop)) for role in roles[ASYNC_SERVICE_ROLE]]
    waiter = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait(
            [*tasks, waiter],
            return_when=asyncio.FIRST_COMPLETED,
        )
        completed_roles = [task for task in done if task is not waiter]
        if completed_roles:
            for task in completed_roles:
                task.result()
        else:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks),
                    timeout=WORKER_SHUTDOWN_GRACE_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Worker 优雅退出超时 seconds=%s",
                    WORKER_SHUTDOWN_GRACE_SECONDS,
                )
    finally:
        stop.set()
        waiter.cancel()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(waiter, *tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
