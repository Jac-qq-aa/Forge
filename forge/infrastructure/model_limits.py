"""跨进程共享的应用预算；不是供应商账号配额。Redis 不可用时禁止绕过预算。"""
from __future__ import annotations

import asyncio
import hashlib
import random
import time
from dataclasses import dataclass
from uuid import uuid4

from redis.asyncio import Redis
from openai import APIConnectionError

from forge.infrastructure.telemetry import emit


ACQUIRE = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local expired = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', now - 60)
for _, id in ipairs(expired) do redis.call('HDEL', KEYS[2], id) end
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - 60)
redis.call('ZREMRANGEBYSCORE', KEYS[3], '-inf', now)
local total = 0
for _, value in ipairs(redis.call('HVALS', KEYS[2])) do total = total + tonumber(value) end
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then return 0 end
if total + tonumber(ARGV[4]) > tonumber(ARGV[3]) then return 0 end
if redis.call('ZCARD', KEYS[3]) >= tonumber(ARGV[5]) then return 0 end
redis.call('ZADD', KEYS[1], now, ARGV[1])
redis.call('HSET', KEYS[2], ARGV[1], ARGV[4])
redis.call('ZADD', KEYS[3], now + tonumber(ARGV[6]), ARGV[1])
for _, key in ipairs(KEYS) do redis.call('EXPIRE', key, math.ceil(tonumber(ARGV[6]) + 120)) end
return 1
"""

RELEASE = """
redis.call('ZREM', KEYS[3], ARGV[1])
if tonumber(ARGV[2]) >= 0 and redis.call('HEXISTS', KEYS[2], ARGV[1]) == 1 then
    redis.call('HSET', KEYS[2], ARGV[1], ARGV[2])
end
return 1
"""


@dataclass(frozen=True)
class Budget:
    rpm: int
    tpm: int
    concurrency: int


class SharedModelLimiter:
    def __init__(self, redis: Redis, *, provider: str, model: str, budget: Budget,
                 wait_seconds: float = 180, timeout_seconds: float = 180):
        self.redis, self.model, self.budget = redis, model, budget
        self.wait_seconds, self.timeout_seconds = wait_seconds, timeout_seconds
        digest = hashlib.sha256(f"{provider.rstrip('/')}|{model}".encode()).hexdigest()[:24]
        self.keys = [f"forge:model:{{{digest}}}:{suffix}" for suffix in ("requests", "tokens", "active")]

    async def acquire(self, tokens: int) -> str:
        if tokens > self.budget.tpm:
            raise ValueError("单次请求的保守 token 估算超过应用 TPM 预算，请缩短输入或调整预算")
        request_id, started = str(uuid4()), time.monotonic()
        waiting = False
        while True:
            granted = await self.redis.eval(
                ACQUIRE, len(self.keys), *self.keys, request_id, self.budget.rpm,
                self.budget.tpm, tokens, self.budget.concurrency, self.timeout_seconds + 15,
            )
            if granted:
                if waiting:
                    await emit("model.limit_acquired", model=self.model,
                               wait_seconds=round(time.monotonic() - started, 3))
                return request_id
            if not waiting:
                await emit("model.limit_wait", model=self.model)
                waiting = True
            if time.monotonic() - started >= self.wait_seconds:
                raise TimeoutError("等待模型应用预算超时")
            await asyncio.sleep(min(1 + random.random(), self.wait_seconds))

    async def release(self, request_id: str, actual_tokens: int | None) -> None:
        await self.redis.eval(RELEASE, len(self.keys), *self.keys, request_id,
                              actual_tokens if actual_tokens is not None else -1)

    async def call(self, operation, *, estimated_tokens: int):
        for attempt in range(3):
            request_id = await self.acquire(estimated_tokens)
            actual = None
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    response = await operation()
                usage = getattr(response, "usage", None)
                actual = usage.total_tokens if usage else None
                return response
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                retryable = status in (408, 429, 500, 502, 503, 504) or isinstance(exc, (TimeoutError, APIConnectionError))
                if not retryable or attempt == 2:
                    raise
                delay = min(30.0, 2 ** (attempt + 1) + random.random())
                headers = getattr(getattr(exc, "response", None), "headers", {})
                try:
                    delay = min(60.0, max(delay, float(headers.get("retry-after", 0))))
                except (ValueError, TypeError):
                    pass
                await emit("model.retry", model=self.model, status_code=status,
                           attempt=attempt + 1, delay_seconds=delay)
            finally:
                await asyncio.shield(self.release(request_id, actual))
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")


def estimate_tokens(*texts: str, output_tokens: int = 0) -> int:
    # UTF-8 字节上界为保守预占，成功后按供应商返回 usage 结算。
    return sum(len(text.encode("utf-8")) for text in texts) + output_tokens + 128
