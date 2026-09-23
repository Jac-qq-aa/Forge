"""第二阶段对象存储与可观测性契约。"""

import io
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from forge.storage.job_result_store import (
    JobResultStore,
    JobResultStoreConfig,
    OBJECT_REF_FIELD,
    ResultObjectCorrupted,
    ResultObjectTooLarge,
    ResultStorageConfigurationError,
)
from forge.observability.metrics import LLM_TOKENS, install_http_metrics, record_llm_usage


class FakeS3:
    def __init__(self) -> None:
        self.objects = {}

    def put_object(self, *, Bucket, Key, Body, **_kwargs):
        self.objects[(Bucket, Key)] = bytes(Body)

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


def _config(*, bucket="results", inline=32, maximum=4096):
    return JobResultStoreConfig(
        bucket=bucket,
        prefix="jobs",
        inline_max_bytes=inline,
        max_read_bytes=maximum,
        endpoint_url=None,
        region="test-region",
    )


@pytest.mark.asyncio
async def test_small_result_remains_inline():
    result = {"ok": True}
    store = JobResultStore(_config(), FakeS3())

    assert await store.persist("job-1", result) is result


@pytest.mark.asyncio
async def test_large_result_round_trips_through_object_storage():
    result = {"article": "内容" * 100}
    client = FakeS3()
    store = JobResultStore(_config(), client)

    reference = await store.persist("job-1", result)

    assert set(reference) == {OBJECT_REF_FIELD}
    assert reference[OBJECT_REF_FIELD]["key"] == "jobs/job-1.json.gz"
    assert await store.hydrate(reference) == result


@pytest.mark.asyncio
async def test_large_result_requires_bucket_configuration():
    store = JobResultStore(_config(bucket=""), FakeS3())

    with pytest.raises(ResultStorageConfigurationError):
        await store.persist("job-1", {"article": "x" * 100})


@pytest.mark.asyncio
async def test_object_integrity_is_verified_before_json_decode():
    client = FakeS3()
    store = JobResultStore(_config(), client)
    reference = await store.persist("job-1", {"article": "x" * 100})
    reference[OBJECT_REF_FIELD]["sha256"] = "0" * 64

    with pytest.raises(ResultObjectCorrupted):
        await store.hydrate(reference)


@pytest.mark.asyncio
async def test_result_larger_than_read_limit_is_rejected_before_upload():
    client = FakeS3()
    store = JobResultStore(_config(inline=16, maximum=64), client)

    with pytest.raises(ResultObjectTooLarge):
        await store.persist("job-1", {"article": "x" * 100})
    assert client.objects == {}


@pytest.mark.asyncio
async def test_reserved_field_in_normal_result_is_not_treated_as_reference():
    result = {OBJECT_REF_FIELD: {"user": "value"}, "article": "short"}
    store = JobResultStore(_config(inline=1024), FakeS3())

    assert await store.hydrate(result) == result


@pytest.mark.asyncio
async def test_http_metrics_requires_configured_bearer_token(monkeypatch):
    monkeypatch.setenv("METRICS_BEARER_TOKEN", "monitor-secret")
    app = FastAPI()
    install_http_metrics(app, "test")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=True,
    ) as client:
        denied = await client.get("/metrics")
        allowed = await client.get(
            "/metrics",
            headers={"Authorization": "Bearer monitor-secret"},
        )

    assert denied.status_code == 401
    assert allowed.status_code == 200


def test_llm_usage_records_input_and_output_tokens():
    before_input = LLM_TOKENS.labels("test-model", "generation", "input")._value.get()
    before_output = LLM_TOKENS.labels("test-model", "generation", "output")._value.get()

    record_llm_usage(
        SimpleNamespace(usage_metadata={"input_tokens": 12, "output_tokens": 7}),
        model="test-model",
        role="generation",
    )

    assert LLM_TOKENS.labels("test-model", "generation", "input")._value.get() == before_input + 12
    assert LLM_TOKENS.labels("test-model", "generation", "output")._value.get() == before_output + 7
