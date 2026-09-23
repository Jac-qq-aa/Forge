"""Redis 原子计数实现的租户提交速率限制。"""

from __future__ import annotations

import os

from forge.storage.redis_client import get_redis_client


class TenantRateLimitExceeded(RuntimeError):
    def __init__(self, retry_after: int):
        super().__init__("租户任务提交频率超过限制")
        self.retry_after = retry_after


class QuotaStoreUnavailable(RuntimeError):
    """Redis 不可用时拒绝继续接收任务，避免绕过全局配额。"""


RATE_LIMIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""


async def _consume_limit(key: str, limit: int) -> None:
    if limit <= 0:
        raise RuntimeError("提交频率限制必须大于 0")
    try:
        client = await get_redis_client()
        current, ttl = await client.eval(
            RATE_LIMIT_SCRIPT,
            1,
            key,
            60,
        )
    except Exception as exc:
        raise QuotaStoreUnavailable("租户配额存储不可用") from exc
    if int(current) > limit:
        raise TenantRateLimitExceeded(max(1, int(ttl)))


async def enforce_submission_rate(tenant_id: str, actor_id: str) -> None:
    await _consume_limit(
        f"quota:submit:tenant:{tenant_id}",
        int(os.getenv("TENANT_SUBMISSIONS_PER_MINUTE", "60")),
    )
    await _consume_limit(
        f"quota:submit:user:{tenant_id}:{actor_id}",
        int(os.getenv("USER_SUBMISSIONS_PER_MINUTE", "20")),
    )
