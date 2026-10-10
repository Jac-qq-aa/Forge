from __future__ import annotations

from typing import Any

import httpx


class RocketMqBridgePublisher:
    """使用内部 HTTP Bridge 调用官方 RocketMQ Java 客户端。"""

    def __init__(self, *, base_url: str, token: str, timeout: float = 10.0) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout
        )

    async def publish(self, message: dict[str, Any]) -> str:
        response = await self._client.post("/v1/messages", json=message)
        response.raise_for_status()
        payload = response.json()
        message_id = payload.get("message_id")
        if not message_id:
            raise RuntimeError("RocketMQ Bridge 未返回 message_id")
        return str(message_id)

    async def close(self) -> None:
        await self._client.aclose()
