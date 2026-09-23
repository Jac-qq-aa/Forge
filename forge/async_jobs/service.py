"""异步任务提交入口。"""

import hashlib
import json
from typing import Any

from forge.security.quota import enforce_submission_rate

from .config import (
    JOB_MAX_ATTEMPTS,
    ROCKETMQ_WORKFLOW_TOPIC,
    TENANT_MAX_ACTIVE_JOBS,
    USER_MAX_ACTIVE_JOBS,
)
from .repository import JobRepository


async def submit_job(
    *,
    operation: str,
    payload: dict[str, Any],
    tenant_id: str,
    actor_id: str,
    idempotency_key: str,
    topic: str = ROCKETMQ_WORKFLOW_TOPIC,
    max_attempts: int = JOB_MAX_ATTEMPTS,
) -> tuple[dict[str, Any], bool]:
    if TENANT_MAX_ACTIVE_JOBS <= 0:
        raise RuntimeError("TENANT_MAX_ACTIVE_JOBS 必须大于 0")
    if USER_MAX_ACTIVE_JOBS <= 0:
        raise RuntimeError("USER_MAX_ACTIVE_JOBS 必须大于 0")
    await enforce_submission_rate(tenant_id, actor_id)
    repository = JobRepository()
    canonical_request = json.dumps(
        {"operation": operation, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return await repository.create_job(
        operation=operation,
        payload=payload,
        tenant_id=tenant_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        request_hash=hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        topic=topic,
        max_attempts=max_attempts,
        max_active_jobs=TENANT_MAX_ACTIVE_JOBS,
        max_user_active_jobs=USER_MAX_ACTIVE_JOBS,
    )
