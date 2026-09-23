"""Gateway 异步提交接口契约。"""

import json
import importlib
from uuid import uuid4

import pytest

from forge.async_jobs import service
from forge.security.auth import Principal

gateway_app = importlib.import_module("forge.gateway.app")


@pytest.mark.asyncio
async def test_create_session_returns_accepted_job_and_forwards_idempotency(monkeypatch):
    job_id = str(uuid4())
    captured = {}

    async def fake_submit_job(**kwargs):
        captured.update(kwargs)
        return (
            {
                "id": job_id,
                "operation": kwargs["operation"],
                "status": "queued",
                "created_at": "2026-09-17T08:00:00+00:00",
            },
            True,
        )

    monkeypatch.setattr(service, "submit_job", fake_submit_job)
    request = gateway_app.CreateSessionRequest(
        article_id="article-1",
        source_article={"title": "标题", "text": "正文"},
        user_input="改写",
    )

    response = await gateway_app.create_session(
        request,
        "request-123",
        Principal("user-1", "tenant-1", frozenset()),
    )
    body = json.loads(response.body)

    assert response.status_code == 202
    assert body["job_id"] == job_id
    assert body["deduplicated"] is False
    assert captured["operation"] == "create_session"
    assert captured["idempotency_key"] == "request-123"
    assert captured["tenant_id"] == "tenant-1"
    assert captured["actor_id"] == "user-1"
    assert captured["payload"]["article_id"] == "article-1"
    assert captured["payload"]["memory_owner_id"] == "user-1"
