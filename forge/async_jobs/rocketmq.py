"""RocketMQ 5.x gRPC SDK 的窄适配层。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .config import (
    ROCKETMQ_ACCESS_KEY,
    ROCKETMQ_ENDPOINTS,
    ROCKETMQ_NAMESPACE,
    ROCKETMQ_SECRET_KEY,
)


def _configuration():
    from rocketmq import ClientConfiguration, Credentials

    credentials = Credentials(ROCKETMQ_ACCESS_KEY, ROCKETMQ_SECRET_KEY) if ROCKETMQ_ACCESS_KEY else Credentials()
    if ROCKETMQ_NAMESPACE:
        return ClientConfiguration(ROCKETMQ_ENDPOINTS, credentials, ROCKETMQ_NAMESPACE)
    return ClientConfiguration(ROCKETMQ_ENDPOINTS, credentials)


class RocketMQPublisher:
    def __init__(self, topics: tuple[str, ...]):
        self.topics = topics
        self._producer = None

    async def start(self) -> None:
        from rocketmq import Producer

        self._producer = Producer(_configuration(), self.topics)
        await asyncio.to_thread(self._producer.startup)

    async def publish(
        self,
        topic: str,
        payload: dict[str, Any],
        message_key: str,
        *,
        delivery_timestamp_seconds: int | None = None,
        tag: str = "job",
    ) -> None:
        from rocketmq import Message

        if self._producer is None:
            raise RuntimeError("RocketMQ producer 尚未启动")
        message = Message()
        message.topic = topic
        message.body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        message.tag = tag
        message.keys = message_key
        if delivery_timestamp_seconds is not None:
            # rocketmq-python-client 5.1.2 的编码器直接写 protobuf
            # Timestamp.seconds；其属性文档仍误写为毫秒，必须按实测传秒。
            message.delivery_timestamp = delivery_timestamp_seconds
        await asyncio.to_thread(self._producer.send, message)

    async def close(self) -> None:
        if self._producer is not None:
            await asyncio.to_thread(self._producer.shutdown)
            self._producer = None


class RocketMQConsumer:
    def __init__(self, topic: str | tuple[str, ...], consumer_group: str):
        self.topics = (topic,) if isinstance(topic, str) else topic
        self.consumer_group = consumer_group
        self._consumer = None

    async def start(self) -> None:
        from rocketmq import FilterExpression, SimpleConsumer

        self._consumer = SimpleConsumer(
            _configuration(),
            self.consumer_group,
            {topic: FilterExpression() for topic in self.topics},
        )
        await asyncio.to_thread(self._consumer.startup)

    async def receive(self, batch_size: int, invisible_seconds: int) -> list[Any]:
        if self._consumer is None:
            raise RuntimeError("RocketMQ consumer 尚未启动")
        messages = await asyncio.to_thread(
            self._consumer.receive, batch_size, invisible_seconds
        )
        return list(messages or [])

    async def acknowledge(self, message: Any) -> None:
        if self._consumer is None:
            raise RuntimeError("RocketMQ consumer 尚未启动")
        await asyncio.to_thread(self._consumer.ack, message)

    async def extend_visibility(self, message: Any, invisible_seconds: int) -> None:
        if self._consumer is None:
            raise RuntimeError("RocketMQ consumer 尚未启动")
        await asyncio.to_thread(
            self._consumer.change_invisible_duration,
            message,
            invisible_seconds,
        )

    @staticmethod
    def decode(message: Any) -> dict[str, Any]:
        body = message.body
        if isinstance(body, str):
            return json.loads(body)
        return json.loads(bytes(body).decode("utf-8"))

    async def close(self) -> None:
        if self._consumer is not None:
            await asyncio.to_thread(self._consumer.shutdown)
            self._consumer = None
