"""异步工作流消费者的 ACK 与重试契约。"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from forge.async_jobs import worker
from forge.async_jobs.repository import JobClaim


@pytest.fixture(autouse=True)
def disable_job_events(monkeypatch):
    async def no_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(worker, "publish_job_event", no_event)


class FakeConsumer:
    def __init__(self):
        self.acknowledged = []
        self.visibility_changes = []

    def decode(self, message):
        return {"job_id": message.job_id}

    async def acknowledge(self, message):
        self.acknowledged.append(message.job_id)

    async def extend_visibility(self, message, invisible_seconds):
        self.visibility_changes.append((message.job_id, invisible_seconds))


class FakeRepository:
    def __init__(self, claim, *, committed=True, terminal=False):
        self.claim = claim
        self.committed = committed
        self.terminal = terminal
        self.completed = []
        self.failed = []

    async def claim_job(self, job_id, worker_id, lease_seconds):
        return self.claim

    async def complete_job(self, job_id, lease_token, result):
        self.completed.append((job_id, lease_token, result))
        return self.committed

    async def fail_job(self, job_id, lease_token, error):
        self.failed.append((job_id, lease_token, error))
        return self.terminal

    async def renew_job_lease(self, job_id, lease_token, lease_seconds):
        return True


@pytest.mark.asyncio
async def test_successful_job_is_acknowledged_after_result_commit(monkeypatch):
    job_id = str(uuid4())
    lease_token = uuid4()
    claim = JobClaim(
        "claimed",
        {"id": job_id, "tenant_id": "tenant-1", "actor_id": "user-1", "operation": "create_session", "payload": {}},
        lease_token,
    )
    repository = FakeRepository(claim)
    consumer = FakeConsumer()
    monkeypatch.setattr(worker, "execute_workflow_job", lambda job: _async_value({"ok": True}))

    await worker._handle_message(
        SimpleNamespace(job_id=job_id),
        consumer,
        repository,
        "worker-1",
        worker.asyncio.Semaphore(1),
    )

    assert consumer.acknowledged == [job_id]
    assert repository.completed[0][2] == {"ok": True}


@pytest.mark.asyncio
async def test_retryable_failure_is_not_acknowledged(monkeypatch):
    job_id = str(uuid4())
    claim = JobClaim(
        "claimed",
        {"id": job_id, "tenant_id": "tenant-1", "actor_id": "user-1", "operation": "outline_action", "payload": {}},
        uuid4(),
    )
    repository = FakeRepository(claim, terminal=False)
    consumer = FakeConsumer()

    async def fail(_job):
        raise RuntimeError("temporary")

    monkeypatch.setattr(worker, "execute_workflow_job", fail)
    await worker._handle_message(
        SimpleNamespace(job_id=job_id),
        consumer,
        repository,
        "worker-1",
        worker.asyncio.Semaphore(1),
    )

    assert consumer.acknowledged == []
    assert "temporary" in repository.failed[0][2]
    assert consumer.visibility_changes == [(job_id, worker.JOB_FAILURE_RETRY_SECONDS)]


@pytest.mark.asyncio
async def test_terminal_duplicate_is_acknowledged_without_execution(monkeypatch):
    job_id = str(uuid4())
    repository = FakeRepository(JobClaim("terminal"))
    consumer = FakeConsumer()

    async def should_not_run(_job):
        raise AssertionError("终态重复消息不应再次执行")

    monkeypatch.setattr(worker, "execute_workflow_job", should_not_run)
    await worker._handle_message(
        SimpleNamespace(job_id=job_id),
        consumer,
        repository,
        "worker-1",
        worker.asyncio.Semaphore(1),
    )

    assert consumer.acknowledged == [job_id]


@pytest.mark.asyncio
async def test_busy_session_is_retried_soon_without_acknowledgement():
    job_id = str(uuid4())
    repository = FakeRepository(JobClaim("busy"))
    consumer = FakeConsumer()

    await worker._handle_message(
        SimpleNamespace(job_id=job_id),
        consumer,
        repository,
        "worker-1",
        worker.asyncio.Semaphore(1),
    )

    assert consumer.acknowledged == []
    assert consumer.visibility_changes == [(job_id, worker.JOB_BUSY_RETRY_SECONDS)]


async def _async_value(value):
    return value
