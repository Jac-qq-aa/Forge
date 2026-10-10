"""显式开启后验收 Docker 与真实模型；不会修改现有用户的对话。"""
import asyncio
import os
import time
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest

from forge.api.auth import issue_token
from forge.config import get_settings


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("FORGE_LIVE_TRACE_SMOKE") != "1", reason="需要显式开启真实模型验收")
@pytest.mark.asyncio
async def test_live_generation_has_correlated_trace_and_automatically_finishes():
    settings = get_settings()
    owner = await asyncpg.connect(os.environ["FORGE_INTEGRATION_OWNER_DSN"])
    actor = await owner.fetchval(
        "INSERT INTO users(email,password_hash) VALUES($1,'smoke-disabled-password') RETURNING id",
        f"trace-smoke-{uuid4().hex}@example.com")
    token = issue_token(user_id=actor, secret=settings.jwt_secret, issuer=settings.jwt_issuer, audience=settings.jwt_audience)
    conversation = None
    async with httpx.AsyncClient(timeout=20, headers={"Authorization": f"Bearer {token}"}) as client:
        try:
            response = await client.post("http://127.0.0.1:8000/v1/conversations")
            response.raise_for_status()
            conversation = response.json()["conversation_id"]
            response = await client.post(f"http://127.0.0.1:8000/v1/conversations/{conversation}/messages", json={
                "content": "保持原文方向，整理成公司业务介绍，先给事实清单和大纲。",
                "source_text": "锐博集团为企业提供人力资源服务。"})
            response.raise_for_status()
            job, trace = response.json()["job_id"], response.headers["x-trace-id"]
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                row = await owner.fetchrow("SELECT status FROM workflow_jobs WHERE id=$1", UUID(job))
                if row["status"] in {"waiting", "completed", "failed"}:
                    break
                await asyncio.sleep(2)
            response = await client.get(f"http://127.0.0.1:8000/v1/conversations/{conversation}/jobs/{job}/review")
            response.raise_for_status()
            review = response.json()
            payloads = [item["payload"] for item in review["events"]]
            nodes = [item for item in payloads if item.get("category") == "node"]
            print({"status": review["job"]["status"], "event_count": review["summary"]["event_count"],
                   "failure_count": review["summary"]["failure_count"],
                   "degradation_count": review["summary"]["degradation_count"],
                   "node_run_ids": sorted({item.get("run_id", "") for item in nodes})})
            assert review["job"]["status"] == "completed"
            assert all(item["trace_id"] == trace for item in payloads)
            assert nodes and all(item.get("run_id") for item in nodes)
            assert not any(item["status"] == "paused" for item in payloads)
            assert {"intent", "planner", "outline", "approval", "generator", "critic"}.issubset({item["node"] for item in nodes})
            unrelated = await client.get(f"http://127.0.0.1:8000/v1/conversations/{uuid4()}/jobs/{job}/review")
            assert unrelated.status_code == 404
        finally:
            if conversation:
                await client.delete(f"http://127.0.0.1:8123/threads/{conversation}")
                await owner.execute("DELETE FROM conversations WHERE id=$1", UUID(conversation))
            await owner.execute("DELETE FROM users WHERE id=$1", actor)
            await owner.close()
