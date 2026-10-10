from __future__ import annotations

import asyncio
import logging

from forge.bootstrap import build_runtime
from forge.infrastructure.jobs import OutboxRepository
from forge.infrastructure.rocketmq import RocketMqBridgePublisher
from forge.platform import run_async
from forge.infrastructure.telemetry import emit, error_fields, span, trace_context
from forge.infrastructure.langsmith_trace import continue_trace

logger = logging.getLogger(__name__)


async def run() -> None:
    runtime = await build_runtime()
    outbox = OutboxRepository(runtime.database)
    publisher = RocketMqBridgePublisher(
        base_url=runtime.settings.rocketmq_bridge_url,
        token=runtime.settings.rocketmq_bridge_token,
        timeout=runtime.settings.rocketmq_publish_timeout_seconds,
    )
    try:
        while True:
            messages = await outbox.claim(limit=runtime.settings.outbox_batch_size)
            if not messages:
                await asyncio.sleep(runtime.settings.outbox_poll_seconds)
                continue
            for item in messages:
                body = item["payload"]
                with continue_trace(body.get("trace_context")), trace_context(recorder=runtime.telemetry, trace_id=body["trace_id"],
                                   job_id=str(item["job_id"]), message_id=str(item["id"]),
                                   conversation_id=body["conversation_id"],
                                   identity={"user_id": body["actor_user_id"], "tenant_id": body.get("tenant_id")}):
                    try:
                        async with span("rocketmq.publish", category="dispatch", attempt=item["attempts"], topic=item["topic"]):
                            transport_id = await publisher.publish({
                                "topic": item["topic"], "key": item["message_key"],
                                "message_id": str(item["id"]), "body": body})
                            await outbox.published(item["id"], transport_id)
                            await emit("message.published", transport_message_id=transport_id)
                    except Exception as exc:
                        await outbox.retry(item["id"], error=f"{type(exc).__name__}: {exc}",
                                           attempts=item["attempts"], max_attempts=runtime.settings.outbox_max_attempts)
                        await emit("message.retry", status="failed" if item["attempts"] >= runtime.settings.outbox_max_attempts else "retrying",
                                   attempt=item["attempts"], **error_fields(exc))
    finally:
        await publisher.close()
        await runtime.close()


if __name__ == "__main__":
    try:
        run_async(run())
    except KeyboardInterrupt:
        pass
