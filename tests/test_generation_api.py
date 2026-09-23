"""文章生成任务 HTTP 路由的输入、身份与返回契约。"""

import importlib
import json
from uuid import uuid4

import pytest

from forge.async_jobs import generation_repository, generation_service
from forge.security.auth import Principal


gateway_app = importlib.import_module("forge.gateway.app")


@pytest.mark.asyncio
async def test_create_generation_task_returns_202_and_stable_thread(monkeypatch):
    task_id = str(uuid4())
    thread_id = str(uuid4())
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return {
            "id": task_id,
            "thread_id": thread_id,
            "status": "queued",
        }, True

    monkeypatch.setattr(generation_service, "create_generation_task", fake_create)
    request = gateway_app.GenerationTaskRequest(
        article_id="https://example.com/article",
        source_article={"title": "标题", "text": "正文"},
        user_input="改写为知乎文章",
    )

    response = await gateway_app.create_generation_task_route(
        request,
        "generation-request-1",
        Principal("user-1", "tenant-1", frozenset()),
    )
    body = json.loads(response.body)

    assert response.status_code == 202
    assert body["task_id"] == task_id
    assert body["thread_id"] == thread_id
    assert body["status_url"] == f"/generation-tasks/{task_id}"
    assert captured["tenant_id"] == "tenant-1"
    assert captured["actor_id"] == "user-1"
    assert captured["idempotency_key"] == "generation-request-1"


@pytest.mark.asyncio
async def test_get_generation_task_scopes_query_to_authenticated_user(monkeypatch):
    task_id = str(uuid4())
    thread_id = str(uuid4())
    captured = {}

    async def fake_get(_self, requested_id, tenant_id, actor_id):
        captured.update(
            task_id=requested_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
        )
        return {
            "id": requested_id,
            "thread_id": thread_id,
            "status": "waiting_approval",
            "current_stage": "waiting_outline",
            "pending_interrupt_id": "interrupt-1",
            "content_version": "content-1",
            "pending_approval": {"type": "outline_approval"},
        }

    monkeypatch.setattr(
        generation_repository.GenerationTaskRepository,
        "get_task",
        fake_get,
    )
    result = await gateway_app.get_generation_task_route(
        task_id,
        Principal("user-1", "tenant-1", frozenset()),
    )

    assert captured == {
        "task_id": task_id,
        "tenant_id": "tenant-1",
        "actor_id": "user-1",
    }
    assert result["status"] == "waiting_approval"
    assert result["interrupt_id"] == "interrupt-1"
    assert "execution_id" not in result
