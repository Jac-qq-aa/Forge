"""第一阶段高并发加固契约。"""

import asyncio
import time
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from httpx import ASGITransport, AsyncClient

from forge.async_jobs import worker
from forge.async_jobs.events import JobEventHub, close_job_event_hub, stream_job_events
from forge.async_jobs.repository import JobClaim
from forge.async_jobs import repository as repository_module
from forge.security import auth
from forge.security import quota


def _request(token: str | None = None) -> Request:
    headers = []
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


@pytest.mark.asyncio
async def test_signed_token_supplies_tenant_identity(monkeypatch):
    secret = "a" * 32
    monkeypatch.setenv("FORGE_JWT_SECRET", secret)
    monkeypatch.setenv("FORGE_JWT_ISSUER", "forge-auth")
    monkeypatch.setenv("FORGE_JWT_AUDIENCE", "forge-api")
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "user-1",
            "tenant_id": "tenant-1",
            "scope": "jobs admin",
            "iat": now,
            "exp": now + 60,
            "iss": "forge-auth",
            "aud": "forge-api",
        },
        secret,
        algorithm="HS256",
    )

    principal = await auth.require_principal(_request(token))

    assert principal.subject == "user-1"
    assert principal.tenant_id == "tenant-1"
    assert principal.has_scope("admin")


@pytest.mark.asyncio
async def test_request_without_token_is_rejected():
    with pytest.raises(HTTPException) as error:
        await auth.require_principal(_request())
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_web_api_middleware_rejects_anonymous_requests():
    from forge.web.app import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/bloggers")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_tenant_rate_limit_is_atomic_and_returns_retry_after(monkeypatch):
    class FakeRedis:
        async def eval(self, *_args):
            return [61, 37]

    async def fake_client():
        return FakeRedis()

    monkeypatch.setenv("TENANT_SUBMISSIONS_PER_MINUTE", "60")
    monkeypatch.setattr(quota, "get_redis_client", fake_client)

    with pytest.raises(quota.TenantRateLimitExceeded) as error:
        await quota.enforce_submission_rate("tenant-1", "user-1")
    assert error.value.retry_after == 37


@pytest.mark.asyncio
async def test_user_rate_limit_is_checked_after_tenant_limit(monkeypatch):
    keys = []

    class FakeRedis:
        async def eval(self, _script, _key_count, key, _window):
            keys.append(key)
            return [1, 59] if len(keys) == 1 else [21, 42]

    async def fake_client():
        return FakeRedis()

    monkeypatch.setenv("TENANT_SUBMISSIONS_PER_MINUTE", "60")
    monkeypatch.setenv("USER_SUBMISSIONS_PER_MINUTE", "20")
    monkeypatch.setattr(quota, "get_redis_client", fake_client)

    with pytest.raises(quota.TenantRateLimitExceeded) as error:
        await quota.enforce_submission_rate("tenant-1", "user-1")
    assert error.value.retry_after == 42
    assert keys == [
        "quota:submit:tenant:tenant-1",
        "quota:submit:user:tenant-1:user-1",
    ]


@pytest.mark.asyncio
async def test_event_hub_reuses_one_redis_subscription(monkeypatch):
    pubsub_calls = 0

    class FakePubSub:
        async def psubscribe(self, _pattern):
            return None

        async def get_message(self, **_kwargs):
            await asyncio.sleep(1)
            return None

        async def aclose(self):
            return None

    class FakeRedis:
        def pubsub(self):
            nonlocal pubsub_calls
            pubsub_calls += 1
            return FakePubSub()

    async def fake_client():
        return FakeRedis()

    monkeypatch.setattr("forge.async_jobs.events.get_redis_client", fake_client)
    hub = JobEventHub()
    async with hub.subscribe("job-1"):
        async with hub.subscribe("job-2"):
            assert pubsub_calls == 1
    await hub.close()


@pytest.mark.asyncio
async def test_consumer_never_exceeds_bounded_in_flight(monkeypatch):
    stop = asyncio.Event()
    active = 0
    maximum_active = 0
    total = 8

    class FakeConsumer:
        def __init__(self, *_args):
            self.sent = 0

        async def start(self):
            return None

        async def receive(self, batch_size, _invisible_seconds):
            assert batch_size <= 2
            if self.sent >= total:
                stop.set()
                return []
            count = min(batch_size, total - self.sent)
            messages = []
            for _ in range(count):
                self.sent += 1
                messages.append(SimpleNamespace(job_id=str(uuid4())))
            return messages

        @staticmethod
        def decode(message):
            return {"job_id": message.job_id}

        async def acknowledge(self, _message):
            return None

        async def extend_visibility(self, *_args):
            return None

        async def close(self):
            return None

    class FakeRepository:
        async def claim_job(self, job_id, _worker_id, _lease_seconds):
            return JobClaim(
                "claimed",
                {
                    "id": job_id,
                    "tenant_id": "tenant-1",
                    "actor_id": "user-1",
                    "payload": {},
                },
                uuid4(),
            )

        async def complete_job(self, *_args):
            return True

        async def renew_job_lease(self, *_args):
            return True

    async def execute(_job):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"ok": True}

    async def no_event(*_args):
        return None

    monkeypatch.setattr(worker, "RocketMQConsumer", FakeConsumer)
    monkeypatch.setattr(worker, "JobRepository", FakeRepository)
    monkeypatch.setattr(worker, "publish_job_event", no_event)

    await worker.run_consumer(
        stop,
        concurrency=2,
        prefetch=0,
        executor=execute,
    )

    assert maximum_active == 2


@pytest.mark.asyncio
async def test_sse_returns_terminal_initial_state_without_polling(monkeypatch):
    job_id = str(uuid4())

    class FakePubSub:
        async def psubscribe(self, _channel):
            return None

        async def get_message(self, **_kwargs):
            await asyncio.sleep(1)
            return None

        async def aclose(self):
            return None

    class FakeRedis:
        def pubsub(self):
            return FakePubSub()

    class FakeRepository:
        async def get_job(self, requested_job_id, tenant_id, actor_id):
            return {
                "id": requested_job_id,
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "status": "succeeded",
                "result": {"ok": True},
            }

    async def fake_client():
        return FakeRedis()

    monkeypatch.setattr("forge.async_jobs.events.get_redis_client", fake_client)
    events = []
    async for event in stream_job_events(
        job_id,
        "tenant-1",
        "user-1",
        FakeRepository(),
    ):
        events.append(event)
    await close_job_event_hub()

    assert len(events) == 1
    assert "event: job" in events[0]
    assert '"succeeded"' in events[0]


@pytest.mark.asyncio
async def test_session_advisory_lock_prevents_simultaneous_claim(monkeypatch):
    job_id = uuid4()
    session_id = uuid4()

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Connection:
        def transaction(self):
            return Transaction()

        async def fetchrow(self, query, *_args):
            assert "FOR UPDATE" in query
            return {
                "id": job_id,
                "tenant_id": "tenant-1",
                "status": "queued",
                "attempt_count": 0,
                "max_attempts": 3,
                "session_id": session_id,
                "lease_expires_at": None,
                "payload": {},
                "result": None,
                "lease_token": None,
            }

        async def fetchval(self, query, *_args):
            assert "pg_try_advisory_xact_lock" in query
            return False

    class Acquire:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, *_args):
            return None

    class Pool:
        def acquire(self):
            return Acquire()

    async def fake_pool():
        return Pool()

    monkeypatch.setattr(repository_module, "get_pg_pool", fake_pool)
    claim = await repository_module.JobRepository().claim_job(
        str(job_id),
        "worker-1",
        60,
    )

    assert claim.action == "busy"


@pytest.mark.asyncio
async def test_outbox_batch_is_published_concurrently(monkeypatch):
    stop = asyncio.Event()
    active = 0
    maximum_active = 0
    published = []
    rows = [
        {"id": index, "topic": "jobs", "payload": {"job_id": index}, "message_key": str(index)}
        for index in range(8)
    ]

    class FakePublisher:
        def __init__(self, _topics):
            return None

        async def start(self):
            return None

        async def publish(self, _topic, payload, _key):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.01)
            published.append(payload["job_id"])
            active -= 1

        async def close(self):
            return None

    class FakeRepository:
        async def cleanup(self, *_args):
            return None

        async def claim_outbox(self, _owner, _limit, _lock_seconds):
            return rows

        async def mark_outbox_published(self, _outbox_id, _owner):
            if len(published) == len(rows):
                stop.set()

        async def mark_outbox_failed(self, *_args):
            raise AssertionError("发布成功时不应进入失败路径")

    monkeypatch.setattr(worker, "RocketMQPublisher", FakePublisher)
    monkeypatch.setattr(worker, "JobRepository", FakeRepository)

    await worker.run_outbox(stop)

    assert sorted(published) == list(range(8))
    assert maximum_active > 1
    assert maximum_active <= worker.OUTBOX_PUBLISH_CONCURRENCY
