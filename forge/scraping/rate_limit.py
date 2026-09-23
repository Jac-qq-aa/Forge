"""Redis 分布式令牌桶；Redis 故障时退化为进程内限速。"""

from __future__ import annotations

import asyncio
import logging
import time

from forge.storage.redis_client import get_redis_client

logger = logging.getLogger(__name__)


TOKEN_BUCKET_SCRIPT = """
local time = redis.call('TIME')
local now = tonumber(time[1]) * 1000 + math.floor(tonumber(time[2]) / 1000)
local values = redis.call('HMGET', KEYS[1], 'tokens', 'updated_at')
local tokens = tonumber(values[1])
local updated_at = tonumber(values[2])
if tokens == nil then tokens = tonumber(ARGV[2]) end
if updated_at == nil then updated_at = now end
local elapsed = math.max(0, now - updated_at) / 1000
tokens = math.min(tonumber(ARGV[2]), tokens + elapsed * tonumber(ARGV[1]))
local allowed = 0
local wait_ms = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
else
    wait_ms = math.ceil((1 - tokens) / tonumber(ARGV[1]) * 1000)
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'updated_at', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(tonumber(ARGV[2]) / tonumber(ARGV[1]) * 2000))
return {allowed, wait_ms}
"""


class DistributedTokenBucket:
    def __init__(self, key: str, rate: float, burst: int):
        if rate <= 0 or burst <= 0:
            raise ValueError("限速 rate 和 burst 必须大于 0")
        self.key = key
        self.rate = rate
        self.burst = burst
        self._local_lock = asyncio.Lock()
        self._local_next_at = 0.0
        self._redis_warning_logged = False

    async def acquire(self) -> None:
        try:
            client = await get_redis_client()
            while True:
                allowed, wait_ms = await client.eval(
                    TOKEN_BUCKET_SCRIPT,
                    1,
                    self.key,
                    self.rate,
                    self.burst,
                )
                if int(allowed) == 1:
                    return
                await asyncio.sleep(max(0.05, int(wait_ms) / 1000))
        except Exception as exc:
            if not self._redis_warning_logged:
                logger.warning("Redis 分布式限速不可用，使用进程内限速: %s", exc)
                self._redis_warning_logged = True
            await self._acquire_local()

    async def _acquire_local(self) -> None:
        async with self._local_lock:
            now = time.monotonic()
            wait_seconds = max(0.0, self._local_next_at - now)
            if wait_seconds:
                await asyncio.sleep(wait_seconds)
            self._local_next_at = max(now, self._local_next_at) + 1 / self.rate
