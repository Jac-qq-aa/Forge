"""RocketMQ 文章生成消费者与过期执行核对器。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from .config import (
    GENERATION_RECONCILE_BATCH_SIZE,
    GENERATION_RECONCILE_INTERVAL_SECONDS,
    GENERATION_WORKER_CONCURRENCY,
    JOB_BUSY_RETRY_SECONDS,
    JOB_CONSUMER_BATCH_SIZE,
    JOB_CONSUMER_PREFETCH,
    JOB_LEASE_SECONDS,
    JOB_MESSAGE_INVISIBLE_SECONDS,
    ROCKETMQ_GENERATION_CONSUMER_GROUP,
    ROCKETMQ_GENERATION_RETRY_TOPIC,
    ROCKETMQ_GENERATION_TOPIC,
)
from .events import publish_job_event
from .generation_repository import GenerationEventClaim, GenerationTaskRepository
from .langgraph_runner import LangGraphRunController
from .rocketmq import RocketMQConsumer
from .state_machine import classify_execution_error
from forge.observability.metrics import GENERATION_EVENTS, GENERATION_RECOVERIES

logger = logging.getLogger(__name__)


async def _handle_generation_message(
    message: Any,
    consumer: RocketMQConsumer,
    repository: GenerationTaskRepository,
    controller: LangGraphRunController,
    worker_id: str,
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore:
        try:
            envelope = consumer.decode(message)
            event_id = str(envelope["event_id"])
            task_id = str(envelope["task_id"])
        except Exception:
            logger.exception("收到无法解析的生成任务消息")
            await consumer.acknowledge(message)
            return

        try:
            claim = await repository.claim_event(event_id, worker_id, JOB_LEASE_SECONDS)
        except Exception:
            logger.exception("领取生成事件失败 task_id=%s event_id=%s", task_id, event_id)
            return
        if claim.action in {"missing", "terminal"}:
            GENERATION_EVENTS.labels("duplicate", claim.action).inc()
            await _ack_safely(consumer, message, task_id, event_id)
            return
        if claim.action == "busy":
            with suppress(Exception):
                await consumer.extend_visibility(message, JOB_BUSY_RETRY_SECONDS)
            return

        task = claim.task or {}
        event = claim.event or {}
        execution_id = claim.execution_id
        run_holder = {"run_id": task.get("active_run_id")}
        stop_heartbeat = asyncio.Event()
        lease_lost = asyncio.Event()

        async def record_run(run_id: str) -> bool:
            recorded = await repository.set_active_run(
                task_id, event_id, execution_id, run_id
            )
            if recorded:
                run_holder["run_id"] = run_id
            return recorded

        heartbeat = asyncio.create_task(
            _generation_heartbeat(
                consumer=consumer,
                message=message,
                repository=repository,
                controller=controller,
                task=task,
                event_id=event_id,
                execution_id=execution_id,
                run_holder=run_holder,
                stop=stop_heartbeat,
                lease_lost=lease_lost,
            )
        )
        log_context = {
            "task_id": task_id,
            "thread_id": task.get("thread_id"),
            "event_id": event_id,
            "execution_id": execution_id,
            "attempt": event.get("attempt_count"),
        }
        GENERATION_EVENTS.labels(event.get("action", "unknown"), "started").inc()
        await publish_job_event(
            task_id,
            task["tenant_id"],
            task["actor_id"],
            "running",
        )
        try:
            if event["action"] == "cancel":
                await controller.cancel_run(str(task["thread_id"]), task.get("active_run_id"))
                committed = await repository.complete_cancel_event(
                    task_id=task_id,
                    event_id=event_id,
                    execution_id=execution_id,
                )
            else:
                approval = None
                if event.get("approval_id"):
                    approval = await repository.load_approval_payload(event["approval_id"])
                outcome = await controller.execute_segment(
                    task=task,
                    event=event,
                    approval=approval,
                    record_run=record_run,
                )
                if lease_lost.is_set():
                    return
                if outcome.kind == "waiting_approval":
                    committed = await repository.complete_waiting(
                        task_id=task_id,
                        event_id=event_id,
                        execution_id=execution_id,
                        stage=outcome.stage,
                        interrupt_id=outcome.interrupt_id or "",
                        content_version=outcome.content_version or "",
                        pending_approval=outcome.pending_approval or {},
                        revision_round=outcome.revision_round,
                    )
                elif outcome.kind == "completed":
                    committed = await repository.complete_success(
                        task_id=task_id,
                        event_id=event_id,
                        execution_id=execution_id,
                        stage=outcome.stage,
                        result=outcome.result or {},
                        revision_round=outcome.revision_round,
                    )
                else:
                    raise RuntimeError(
                        f"LangGraph 执行结束后仍未完成或暂停: stage={outcome.stage}"
                    )
            if committed == "lease_lost":
                logger.warning(
                    "生成结果未提交，执行租约已失效 task_id=%s event_id=%s execution_id=%s",
                    task_id,
                    event_id,
                    execution_id,
                    extra=log_context,
                )
                return
            await publish_job_event(
                task_id,
                task["tenant_id"],
                task["actor_id"],
                committed,
            )
            await _ack_safely(consumer, message, task_id, event_id)
            GENERATION_EVENTS.labels(event.get("action", "unknown"), committed).inc()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            disposition = classify_execution_error(exc)
            logger.exception(
                "生成事件执行失败 task_id=%s event_id=%s execution_id=%s code=%s",
                task_id,
                event_id,
                execution_id,
                disposition.code,
                extra=log_context,
            )
            status = await repository.fail_event(
                task_id=task_id,
                event_id=event_id,
                execution_id=execution_id,
                retryable=disposition.retryable,
                error_code=disposition.code,
                error=disposition.message,
            )
            if status != "lease_lost":
                await publish_job_event(
                    task_id,
                    task["tenant_id"],
                    task["actor_id"],
                    status,
                )
                await _ack_safely(consumer, message, task_id, event_id)
                GENERATION_EVENTS.labels(event.get("action", "unknown"), status).inc()
        finally:
            stop_heartbeat.set()
            await heartbeat


async def _generation_heartbeat(
    *,
    consumer: RocketMQConsumer,
    message: Any,
    repository: GenerationTaskRepository,
    controller: LangGraphRunController,
    task: dict[str, Any],
    event_id: str,
    execution_id,
    run_holder: dict[str, str | None],
    stop: asyncio.Event,
    lease_lost: asyncio.Event,
) -> None:
    interval = max(5, JOB_LEASE_SECONDS // 3)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except asyncio.TimeoutError:
            pass
        try:
            status = await repository.renew_execution(
                task["id"], event_id, execution_id, JOB_LEASE_SECONDS
            )
            if status == "renewed":
                await consumer.extend_visibility(message, JOB_MESSAGE_INVISIBLE_SECONDS)
                continue
            if status == "cancel_requested":
                await controller.cancel_run(
                    str(task["thread_id"]), run_holder.get("run_id")
                )
                return
            lease_lost.set()
            await controller.cancel_run(str(task["thread_id"]), run_holder.get("run_id"))
            return
        except Exception:
            logger.exception(
                "生成事件续租失败 task_id=%s event_id=%s execution_id=%s",
                task["id"],
                event_id,
                execution_id,
            )


async def _ack_safely(
    consumer: RocketMQConsumer,
    message: Any,
    task_id: str,
    event_id: str,
) -> None:
    try:
        await consumer.acknowledge(message)
    except Exception:
        logger.exception("生成事件 ACK 失败 task_id=%s event_id=%s", task_id, event_id)


async def run_generation_consumer(stop: asyncio.Event, worker_id: str) -> None:
    if GENERATION_WORKER_CONCURRENCY <= 0:
        raise ValueError("文章生成 Worker 并发数必须大于 0")
    if JOB_CONSUMER_BATCH_SIZE <= 0 or JOB_CONSUMER_PREFETCH < 0:
        raise ValueError("消费批量必须大于 0，预取数不能小于 0")
    repository = GenerationTaskRepository()
    controller = LangGraphRunController()
    consumer = RocketMQConsumer(
        (ROCKETMQ_GENERATION_TOPIC, ROCKETMQ_GENERATION_RETRY_TOPIC),
        ROCKETMQ_GENERATION_CONSUMER_GROUP,
    )
    semaphore = asyncio.Semaphore(GENERATION_WORKER_CONCURRENCY)
    maximum_in_flight = GENERATION_WORKER_CONCURRENCY + JOB_CONSUMER_PREFETCH
    running: set[asyncio.Task] = set()
    await consumer.start()
    logger.info("文章生成消费者已启动 worker=%s", worker_id)
    try:
        while not stop.is_set():
            while len(running) >= maximum_in_flight:
                await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            capacity = maximum_in_flight - len(running)
            messages = await consumer.receive(
                min(JOB_CONSUMER_BATCH_SIZE, capacity),
                JOB_MESSAGE_INVISIBLE_SECONDS,
            )
            for message in messages:
                task = asyncio.create_task(
                    _handle_generation_message(
                        message,
                        consumer,
                        repository,
                        controller,
                        worker_id,
                        semaphore,
                    )
                )
                running.add(task)
                task.add_done_callback(running.discard)
        if running:
            await asyncio.gather(*running, return_exceptions=True)
    finally:
        await consumer.close()


async def run_generation_reconciler(stop: asyncio.Event) -> None:
    if GENERATION_RECONCILE_INTERVAL_SECONDS <= 0:
        raise ValueError("过期执行核对间隔必须大于 0")
    if GENERATION_RECONCILE_BATCH_SIZE <= 0:
        raise ValueError("过期执行核对批量必须大于 0")
    repository = GenerationTaskRepository()
    while not stop.is_set():
        try:
            created = await repository.enqueue_expired_recoveries(
                GENERATION_RECONCILE_BATCH_SIZE
            )
            if created:
                GENERATION_RECOVERIES.inc(created)
                logger.warning("已为 %s 个过期执行创建恢复事件", created)
        except Exception:
            logger.exception("过期生成任务核对失败")
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                stop.wait(), timeout=GENERATION_RECONCILE_INTERVAL_SECONDS
            )
