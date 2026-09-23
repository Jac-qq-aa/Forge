"""文章生成异步执行与 Checkpoint 核对契约。"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from forge.async_jobs.generation_repository import GenerationEventClaim
from forge.async_jobs.generation_worker import _handle_generation_message
from forge.async_jobs.langgraph_runner import LangGraphRunController
from forge.async_jobs.rocketmq import RocketMQPublisher
from forge.async_jobs.state_machine import (
    StaleApproval,
    classify_execution_error,
    public_task_view,
)


class FakeConsumer:
    def __init__(self, envelope):
        self.envelope = envelope
        self.acks = []
        self.visibility = []

    def decode(self, _message):
        return self.envelope

    async def acknowledge(self, message):
        self.acks.append(message)

    async def extend_visibility(self, message, seconds):
        self.visibility.append((message, seconds))


@pytest.mark.asyncio
async def test_completed_duplicate_event_is_acked_without_running_graph():
    event_id = str(uuid4())
    task_id = str(uuid4())
    message = SimpleNamespace()
    consumer = FakeConsumer({"event_id": event_id, "task_id": task_id})

    class Repository:
        async def claim_event(self, *_args):
            return GenerationEventClaim("terminal")

    class Controller:
        async def execute_segment(self, **_kwargs):
            raise AssertionError("完成事件不能再次推进 LangGraph")

    await _handle_generation_message(
        message,
        consumer,
        Repository(),
        Controller(),
        "worker-1",
        __import__("asyncio").Semaphore(1),
    )

    assert consumer.acks == [message]


@pytest.mark.asyncio
async def test_lease_lost_result_is_not_acked(monkeypatch):
    import forge.async_jobs.generation_worker as worker

    event_id = str(uuid4())
    task_id = str(uuid4())
    execution_id = uuid4()
    message = SimpleNamespace()
    consumer = FakeConsumer({"event_id": event_id, "task_id": task_id})
    task = {
        "id": task_id,
        "thread_id": str(uuid4()),
        "tenant_id": "tenant-1",
        "actor_id": "user-1",
        "payload": {},
        "execution_deadline_at": None,
    }
    event = {
        "id": event_id,
        "action": "start",
        "execution_id": str(execution_id),
    }

    class Repository:
        async def claim_event(self, *_args):
            return GenerationEventClaim("claimed", task, event, execution_id)

        async def set_active_run(self, *_args):
            return True

        async def renew_execution(self, *_args):
            return "renewed"

        async def complete_success(self, **_kwargs):
            return "lease_lost"

    class Controller:
        async def execute_segment(self, **_kwargs):
            return SimpleNamespace(
                kind="completed", stage="completed", result={"ok": True}, revision_round=0
            )

        async def cancel_run(self, *_args):
            return None

    async def no_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(worker, "publish_job_event", no_event)
    await _handle_generation_message(
        message,
        consumer,
        Repository(),
        Controller(),
        "worker-1",
        __import__("asyncio").Semaphore(1),
    )

    assert consumer.acks == []


class FakeThreads:
    def __init__(self, state):
        self.state = state
        self.created = []

    async def create(self, **kwargs):
        self.created.append(kwargs)
        return {"thread_id": kwargs["thread_id"]}

    async def get_state(self, _thread_id):
        return self.state


class FakeRuns:
    def __init__(self, threads=None, after_join_state=None):
        self.created = []
        self.joined = []
        self.runs = {}
        self.threads = threads
        self.after_join_state = after_join_state

    async def create(self, thread_id, assistant_id, **kwargs):
        run_id = str(uuid4())
        self.created.append((thread_id, assistant_id, kwargs))
        self.runs[run_id] = {"run_id": run_id, "status": "running"}
        return self.runs[run_id]

    async def join(self, thread_id, run_id):
        self.joined.append((thread_id, run_id))
        self.runs[run_id]["status"] = "interrupted"
        if self.threads is not None and self.after_join_state is not None:
            self.threads.state = self.after_join_state
        return {}

    async def get(self, _thread_id, run_id):
        return self.runs[run_id]

    async def cancel(self, *_args, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_controller_uses_stable_thread_and_reports_real_interrupt():
    interrupt_id = "interrupt-facts-1"
    state = {
        "values": {"stage": "waiting_facts", "research_result": {"facts": []}},
        "next": ["wait_fact_approval"],
        "checkpoint": {"checkpoint_id": "checkpoint-1"},
        "interrupts": [
            {
                "id": interrupt_id,
                "value": {"type": "fact_approval", "research_result": {"facts": []}},
            }
        ],
        "tasks": [],
    }
    initial_state = {
        "values": {},
        "next": [],
        "checkpoint": {"checkpoint_id": None},
        "interrupts": [],
        "tasks": [],
    }
    threads = FakeThreads(initial_state)
    client = SimpleNamespace(threads=threads, runs=FakeRuns(threads, state))
    controller = LangGraphRunController(client)
    task_id = str(uuid4())
    thread_id = str(uuid4())
    task = {
        "id": task_id,
        "thread_id": thread_id,
        "actor_id": "user-1",
        "payload": {"source_article": {"text": "正文"}},
        "active_run_id": None,
        "execution_deadline_at": None,
    }
    event = {
        "id": str(uuid4()),
        "action": "start",
        "execution_id": str(uuid4()),
    }
    recorded = []

    async def record(run_id):
        recorded.append(run_id)
        return True

    outcome = await controller.execute_segment(
        task=task, event=event, approval=None, record_run=record
    )

    assert client.threads.created[0]["thread_id"] == thread_id
    assert len(client.runs.created) == 1
    assert recorded == [client.runs.created and client.runs.joined[0][1]]
    assert outcome.kind == "waiting_approval"
    assert outcome.interrupt_id == interrupt_id
    assert len(outcome.content_version) == 64


@pytest.mark.asyncio
async def test_recovery_joins_active_run_instead_of_creating_another():
    state = {
        "values": {"stage": "completed", "final_draft": "完成"},
        "next": [],
        "checkpoint": {"checkpoint_id": "checkpoint-2"},
        "interrupts": [],
        "tasks": [],
    }
    runs = FakeRuns()
    run_id = str(uuid4())
    runs.runs[run_id] = {"run_id": run_id, "status": "running"}
    client = SimpleNamespace(threads=FakeThreads(state), runs=runs)
    controller = LangGraphRunController(client)
    task = {
        "id": str(uuid4()),
        "thread_id": str(uuid4()),
        "actor_id": "user-1",
        "payload": {},
        "active_run_id": run_id,
        "execution_deadline_at": None,
    }

    async def record(_run_id):
        raise AssertionError("恢复活动 run 时不应创建新 run")

    outcome = await controller.execute_segment(
        task=task,
        event={"id": str(uuid4()), "action": "recover", "execution_id": str(uuid4())},
        approval=None,
        record_run=record,
    )

    assert runs.created == []
    assert runs.joined == [(task["thread_id"], run_id)]
    assert outcome.kind == "completed"


@pytest.mark.asyncio
async def test_resume_rejects_checkpoint_content_version_change():
    state = {
        "values": {"stage": "waiting_outline", "outline": "新大纲"},
        "next": ["wait_outline_approval"],
        "checkpoint": {"checkpoint_id": "checkpoint-new"},
        "interrupts": [
            {
                "id": "interrupt-1",
                "value": {"type": "outline_approval", "outline": "新大纲"},
            }
        ],
        "tasks": [],
    }
    runs = FakeRuns()
    client = SimpleNamespace(threads=FakeThreads(state), runs=runs)
    controller = LangGraphRunController(client)
    task = {
        "id": str(uuid4()),
        "thread_id": str(uuid4()),
        "actor_id": "user-1",
        "payload": {},
        "active_run_id": None,
        "execution_deadline_at": None,
    }

    async def record(_run_id):
        return True

    with pytest.raises(StaleApproval):
        await controller.execute_segment(
            task=task,
            event={
                "id": str(uuid4()),
                "action": "resume",
                "execution_id": str(uuid4()),
                "interrupt_id": "interrupt-1",
                "content_version": "stale-content-version",
            },
            approval={"payload": {"decision": "approve"}},
            record_run=record,
        )

    assert runs.created == []


def test_public_view_hides_execution_fencing_fields():
    task = {
        "id": "task-1",
        "thread_id": "thread-1",
        "status": "running",
        "current_stage": "research",
        "execution_id": "secret-execution-token",
        "lease_token": "secret-lease-token",
    }

    view = public_task_view(task)

    assert view["task_id"] == "task-1"
    assert "execution_id" not in view
    assert "lease_token" not in view


def test_permission_error_is_not_retried():
    disposition = classify_execution_error(PermissionError("forbidden"))
    assert disposition.retryable is False
    assert disposition.code == "permission_denied"


@pytest.mark.asyncio
async def test_rocketmq_512_delay_timestamp_uses_unix_seconds(monkeypatch):
    import sys

    sent = []

    class Message:
        pass

    class Producer:
        def send(self, message):
            sent.append(message)

    monkeypatch.setitem(sys.modules, "rocketmq", SimpleNamespace(Message=Message))
    publisher = RocketMQPublisher(("retry-topic",))
    publisher._producer = Producer()

    await publisher.publish(
        "retry-topic",
        {"event_id": "event-1"},
        "event-1",
        delivery_timestamp_seconds=1_790_162_700,
    )

    assert sent[0].delivery_timestamp == 1_790_162_700
