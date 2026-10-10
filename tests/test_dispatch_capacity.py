from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from forge.application.orchestration import WorkflowCommandHandler
from forge.domain.jobs import WorkflowCommand
from forge.domain.models import Identity


@asynccontextmanager
async def exclusive(job_id):
    yield True


@pytest.mark.asyncio
async def test_delivery_only_persists_admission_without_running_graph():
    identity = Identity(user_id=uuid4())
    command = WorkflowCommand(job_id=uuid4(), conversation_id=uuid4(), actor_user_id=identity.user_id,
                              kind="start", payload={"identity": identity.model_dump(mode="json")})
    jobs = SimpleNamespace(admit=AsyncMock())
    executor = SimpleNamespace(start=AsyncMock())
    inbox = SimpleNamespace(begin=AsyncMock(return_value=True), complete=AsyncMock())
    handler = WorkflowCommandHandler(SimpleNamespace(jobs=jobs, executor=executor), inbox)
    handler._resolve_identity = AsyncMock(return_value=identity)
    await handler._handle(command, {"conversation_id": command.conversation_id, "kind": "start", "payload": command.payload})
    jobs.admit.assert_awaited_once_with(command.job_id)
    inbox.complete.assert_awaited_once()
    executor.start.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_submission_is_looked_up_without_resubmission():
    identity = Identity(user_id=uuid4())
    job = {"id": uuid4(), "trace_id": uuid4(), "conversation_id": uuid4(), "kind": "start",
           "status": "submission_unknown", "payload": {"identity": identity.model_dump(mode="json"),
           "_execution_id": str(uuid4())}, "attempts": 3}
    jobs = SimpleNamespace(exclusive=exclusive, get=AsyncMock(return_value=job), defer_submission=AsyncMock())
    executor = SimpleNamespace(find_submission=AsyncMock(return_value=None), start=AsyncMock())
    runtime = SimpleNamespace(jobs=jobs, executor=executor, telemetry=None)
    handler = WorkflowCommandHandler(runtime, None)
    handler._resolve_identity = AsyncMock(return_value=identity)
    await handler.dispatch(job)
    executor.start.assert_not_called()
    executor.find_submission.assert_awaited_once()
    jobs.defer_submission.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovered_submission_reuses_remote_run():
    identity = Identity(user_id=uuid4())
    job = {"id": uuid4(), "trace_id": uuid4(), "conversation_id": uuid4(), "kind": "start",
           "status": "submission_unknown", "payload": {"identity": identity.model_dump(mode="json"),
           "_execution_id": str(uuid4())}, "attempts": 3}
    jobs = SimpleNamespace(exclusive=exclusive, get=AsyncMock(return_value=job), mark_submitted=AsyncMock())
    executor = SimpleNamespace(find_submission=AsyncMock(return_value={"thread_id": "thread", "run_id": "existing"}), start=AsyncMock())
    handler = WorkflowCommandHandler(SimpleNamespace(jobs=jobs, executor=executor, telemetry=None), None)
    handler._resolve_identity = AsyncMock(return_value=identity)
    await handler.dispatch(job)
    executor.start.assert_not_called()
    jobs.mark_submitted.assert_awaited_once_with(job_id=job["id"], remote_thread_id="thread",
        remote_run_id="existing", langsmith_trace_id="", execution_id=job["payload"]["_execution_id"])
