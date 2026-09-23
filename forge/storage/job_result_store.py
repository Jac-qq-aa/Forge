"""异步任务大结果的对象存储分层。"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import json
import os
from dataclasses import dataclass
from typing import Any, Protocol


OBJECT_REF_FIELD = "_forge_object_ref"


class ResultStorageConfigurationError(RuntimeError):
    """结果超过内联阈值，但对象存储未正确配置。"""


class ResultObjectCorrupted(RuntimeError):
    """对象内容与数据库中记录的摘要不一致。"""


class ResultObjectTooLarge(RuntimeError):
    """任务结果超过允许持久化和回读的上限。"""


class ObjectClient(Protocol):
    def put_object(self, **kwargs: Any) -> Any: ...

    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class JobResultStoreConfig:
    bucket: str
    prefix: str
    inline_max_bytes: int
    max_read_bytes: int
    endpoint_url: str | None
    region: str | None

    @classmethod
    def from_env(cls) -> "JobResultStoreConfig":
        return cls(
            bucket=os.getenv("JOB_RESULT_S3_BUCKET", "").strip(),
            prefix=os.getenv("JOB_RESULT_S3_PREFIX", "workflow-results").strip("/"),
            inline_max_bytes=int(os.getenv("JOB_RESULT_INLINE_MAX_BYTES", "65536")),
            max_read_bytes=int(os.getenv("JOB_RESULT_MAX_READ_BYTES", "16777216")),
            endpoint_url=os.getenv("JOB_RESULT_S3_ENDPOINT") or None,
            region=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or None,
        )


class JobResultStore:
    """小结果保留在 JSONB，大结果压缩后存到 S3 兼容存储。"""

    def __init__(
        self,
        config: JobResultStoreConfig | None = None,
        client: ObjectClient | None = None,
    ) -> None:
        self.config = config or JobResultStoreConfig.from_env()
        if self.config.inline_max_bytes <= 0:
            raise ValueError("JOB_RESULT_INLINE_MAX_BYTES 必须大于 0")
        if self.config.max_read_bytes <= self.config.inline_max_bytes:
            raise ValueError("JOB_RESULT_MAX_READ_BYTES 必须大于内联阈值")
        self._client = client

    def _get_client(self) -> ObjectClient:
        if self._client is None:
            import boto3

            self._client = boto3.client(
                "s3",
                endpoint_url=self.config.endpoint_url,
                region_name=self.config.region,
            )
        return self._client

    async def persist(self, job_id: str, result: dict[str, Any]) -> dict[str, Any]:
        raw = json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(raw) > self.config.max_read_bytes:
            raise ResultObjectTooLarge(
                f"任务结果大小 {len(raw)} 超过上限 {self.config.max_read_bytes}"
            )
        if len(raw) <= self.config.inline_max_bytes:
            return result
        if not self.config.bucket:
            raise ResultStorageConfigurationError(
                "任务结果超过内联阈值，必须配置 JOB_RESULT_S3_BUCKET"
            )

        compressed = gzip.compress(raw, compresslevel=6)
        digest = hashlib.sha256(raw).hexdigest()
        key = f"{self.config.prefix}/{job_id}.json.gz"
        await asyncio.to_thread(
            self._get_client().put_object,
            Bucket=self.config.bucket,
            Key=key,
            Body=compressed,
            ContentType="application/json",
            ContentEncoding="gzip",
            Metadata={"sha256": digest, "uncompressed-size": str(len(raw))},
        )
        return {
            OBJECT_REF_FIELD: {
                "version": 1,
                "bucket": self.config.bucket,
                "key": key,
                "sha256": digest,
                "size": len(raw),
                "stored_size": len(compressed),
                "encoding": "gzip",
            }
        }

    async def hydrate(self, stored: Any) -> Any:
        if not isinstance(stored, dict) or set(stored) != {OBJECT_REF_FIELD}:
            return stored
        reference = stored[OBJECT_REF_FIELD]
        if reference.get("version") != 1:
            raise ResultObjectCorrupted("不支持的任务结果对象引用版本")
        if reference.get("bucket") != self.config.bucket:
            raise ResultObjectCorrupted("任务结果对象引用了未配置的存储桶")
        expected_prefix = f"{self.config.prefix}/"
        if not str(reference.get("key", "")).startswith(expected_prefix):
            raise ResultObjectCorrupted("任务结果对象不在配置的键前缀下")
        size = int(reference["size"])
        if size > self.config.max_read_bytes:
            raise ResultObjectCorrupted(
                f"对象声明大小 {size} 超过读取上限 {self.config.max_read_bytes}"
            )
        response = await asyncio.to_thread(
            self._get_client().get_object,
            Bucket=reference["bucket"],
            Key=reference["key"],
        )
        body = await asyncio.to_thread(response["Body"].read)
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
            raw = stream.read(self.config.max_read_bytes + 1)
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != reference["sha256"]:
            raise ResultObjectCorrupted("任务结果对象大小或 SHA-256 校验失败")
        return json.loads(raw)
