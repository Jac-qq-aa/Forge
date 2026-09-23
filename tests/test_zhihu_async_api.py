"""Web 层知乎采集任务提交契约。"""

import importlib
import json
from uuid import uuid4

import pytest
from starlette.requests import Request

from forge.async_jobs import config, service
from forge.security.auth import Principal

web_app = importlib.import_module("forge.web.app")


@pytest.mark.asyncio
async def test_zhihu_search_is_submitted_to_dedicated_topic(monkeypatch):
    captured = {}
    job_id = str(uuid4())

    async def fake_submit_job(**kwargs):
        captured.update(kwargs)
        return (
            {
                "id": job_id,
                "operation": kwargs["operation"],
                "status": "queued",
            },
            True,
        )

    monkeypatch.setattr(service, "submit_job", fake_submit_job)
    async def fake_principal(_request):
        return Principal("user-1", "tenant-1", frozenset())

    monkeypatch.setattr(web_app, "require_principal", fake_principal)
    request = web_app.SearchRequest(
        source="人力资源",
        source_platform="zhihu",
        max_results=5,
        search_mode="keyword",
    )
    http_request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/search",
            "headers": [],
        }
    )

    response = await web_app.search_articles(request, http_request)
    body = json.loads(response.body)

    assert response.status_code == 202
    assert body["job_id"] == job_id
    assert captured["operation"] == "zhihu_search"
    assert captured["topic"] == config.ROCKETMQ_ZHIHU_TOPIC
    assert captured["tenant_id"] == "tenant-1"
    assert captured["actor_id"] == "user-1"
