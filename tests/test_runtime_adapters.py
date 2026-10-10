from __future__ import annotations

from uuid import uuid4

import pytest

from forge.application.orchestration import WorkflowCommandHandler, WorkflowRunReconciler
from types import SimpleNamespace
from forge.domain.jobs import WorkflowCommand
from forge.workflow.executor import RemoteLangGraphExecutor


class FakeThreads:
    def __init__(self) -> None:
        self.created: list[dict] = []

    async def create(self, **kwargs):
        self.created.append(kwargs)
        return {"thread_id": kwargs["thread_id"]}

    async def get_state(self, thread_id):
        return {
            "values": {"conversation_id": thread_id, "stage": "ready"},
            "interrupts": [],
            "next": [],
        }


class FakeRuns:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self.created: list[dict] = []

    async def list(self, thread_id, *, limit):
        return self.items

    async def create(self, thread_id, assistant_id, **kwargs):
        self.created.append({"thread_id": thread_id, "assistant_id": assistant_id, **kwargs})
        run = {
            "run_id": str(uuid4()),
            "thread_id": thread_id,
            "status": "pending",
            "metadata": kwargs.get("metadata") or {},
        }
        self.items.append(run)
        return run

    async def get(self, thread_id, run_id):
        return {"thread_id": thread_id, "run_id": run_id, "status": "success"}

    async def cancel(self, thread_id, run_id, **kwargs):
        return None


class FakeClient:
    def __init__(self) -> None:
        self.threads = FakeThreads()
        self.runs = FakeRuns()
        self.closed = False

    async def aclose(self):
        self.closed = True


def test_workflow_command_routes_indexing_to_a_separate_topic() -> None:
    actor = uuid4()
    conversation = uuid4()
    command = WorkflowCommand(
        job_id=uuid4(),
        conversation_id=conversation,
        actor_user_id=actor,
        kind="index_document",
        payload={"identity": {"user_id": str(actor)}},
    )

    assert command.topic == "forge-index-commands"
    assert command.transport_payload()["conversation_id"] == str(conversation)


@pytest.mark.asyncio
async def test_consumer_acknowledges_a_stale_command_without_writing_inbox() -> None:
    class MissingJobs:
        async def get(self, job_id):
            return None

    class Runtime:
        jobs = MissingJobs()

    class Inbox:
        async def begin(self, **kwargs):
            raise AssertionError("陈旧消息不应写入 Inbox")

    actor = uuid4()
    command = WorkflowCommand(
        job_id=uuid4(),
        conversation_id=uuid4(),
        actor_user_id=actor,
        kind="index_document",
        payload={"identity": {"user_id": str(actor)}},
    )

    await WorkflowCommandHandler(Runtime(), Inbox()).handle(command)


@pytest.mark.asyncio
async def test_remote_executor_reuses_job_submission_after_uncertain_delivery() -> None:
    client = FakeClient()
    executor = RemoteLangGraphExecutor(
        server_url="http://unused", assistant_id="forge", client=client
    )
    job_id = uuid4()
    conversation_id = uuid4()
    trace_id = str(uuid4())
    payload = {"conversation_id": str(conversation_id), "user_message": "生成文章", "trace_id": trace_id,
               "_execution_id": str(uuid4())}

    first = await executor.start(job_id=job_id, payload=payload)
    second = await executor.start(job_id=job_id, payload=payload)

    assert first == second
    assert len(client.runs.created) == 1
    assert client.runs.created[0]["metadata"]["job_id"] == str(job_id)
    assert client.runs.created[0]["config"]["configurable"] == {
        "forge_job_id": str(job_id), "forge_trace_id": trace_id, "forge_trace_context": {}}


@pytest.mark.asyncio
async def test_remote_executor_reads_thread_state():
    executor = RemoteLangGraphExecutor(server_url="http://unused", assistant_id="forge", client=FakeClient())
    state = await executor.state(thread_id=str(uuid4()))
    assert state["values"]["stage"] == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("next_nodes,expected", [(["approval"], "waiting"), ([], "completed")])
async def test_reconciler_distinguishes_paused_success_from_completed_run(next_nodes, expected):
    class Executor:
        async def status(self, **kwargs):
            return {"status": "success"}
        async def state(self, **kwargs):
            return {"values": {"stage": "awaiting_plan_approval" if next_nodes else "ready"},
                    "next": next_nodes, "interrupts": []}
    class Projection:
        async def project(self, result):
            pass
    class Jobs:
        async def finish(self, **kwargs):
            self.finished = kwargs
    jobs = Jobs()
    runtime = SimpleNamespace(executor=Executor(), projection=Projection(), jobs=jobs)
    reconciler = WorkflowRunReconciler(runtime)
    assert await reconciler._reconcile_job({"id": uuid4(), "remote_thread_id": "thread", "remote_run_id": "run"}) == 1
    assert jobs.finished["status"] == expected
