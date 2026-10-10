import asyncio
from types import SimpleNamespace

import fakeredis.aioredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from forge.infrastructure.model_limits import Budget, SharedModelLimiter


def limiter(redis, *, rpm=20, tpm=1000, concurrency=2, wait=.02):
    return SharedModelLimiter(redis, provider="test", model="shared", budget=Budget(rpm,tpm,concurrency),
                              wait_seconds=wait, timeout_seconds=1)


@pytest.mark.asyncio
async def test_separate_clients_share_atomic_concurrency_and_release():
    server = fakeredis.FakeServer()
    first = limiter(fakeredis.aioredis.FakeRedis(server=server), concurrency=1)
    second = limiter(fakeredis.aioredis.FakeRedis(server=server), concurrency=1)
    token = await first.acquire(50)
    with pytest.raises(TimeoutError):
        await second.acquire(50)
    await first.release(token, 20)
    other = await second.acquire(50)
    await second.release(other, 20)
    assert await first.redis.zcard(first.keys[2]) == 0
    assert await first.redis.zcard(first.keys[0]) == 2


@pytest.mark.asyncio
async def test_tpm_reconciles_usage_but_rpm_still_counts_completed_request():
    redis = fakeredis.aioredis.FakeRedis()
    budget = limiter(redis, rpm=2, tpm=100)
    token = await budget.acquire(90)
    await budget.release(token, 10)
    token = await budget.acquire(80)
    await budget.release(token, 10)
    with pytest.raises(TimeoutError):
        await budget.acquire(1)


@pytest.mark.asyncio
async def test_oversized_input_and_redis_outage_do_not_call_provider():
    server = fakeredis.FakeServer()
    budget = limiter(fakeredis.aioredis.FakeRedis(server=server), tpm=10)
    async def provider():
        raise AssertionError("不应调用模型")
    with pytest.raises(ValueError):
        await budget.call(provider, estimated_tokens=11)
    server.connected = False
    with pytest.raises(RedisConnectionError):
        await budget.call(provider, estimated_tokens=1)


@pytest.mark.asyncio
async def test_cancellation_releases_concurrency_and_keeps_reservation():
    budget = limiter(fakeredis.aioredis.FakeRedis())
    started = asyncio.Event()
    async def provider():
        started.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(budget.call(provider, estimated_tokens=50))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await budget.redis.zcard(budget.keys[2]) == 0
    assert await budget.redis.hlen(budget.keys[1]) == 1


@pytest.mark.asyncio
async def test_failed_provider_retries_are_bounded_and_each_counts_rpm(monkeypatch):
    budget = limiter(fakeredis.aioredis.FakeRedis())
    class Throttle(Exception):
        status_code = 429
    attempts = 0
    async def provider():
        nonlocal attempts
        attempts += 1
        raise Throttle()
    async def no_sleep(delay):
        pass
    monkeypatch.setattr("forge.infrastructure.model_limits.asyncio.sleep", no_sleep)
    with pytest.raises(Throttle):
        await budget.call(provider, estimated_tokens=50)
    assert attempts == 3
    assert await budget.redis.zcard(budget.keys[0]) == 3
    assert await budget.redis.zcard(budget.keys[2]) == 0
