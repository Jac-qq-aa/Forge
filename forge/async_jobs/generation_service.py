"""文章生成任务的提交与审批入口。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from forge.security.quota import enforce_submission_rate

from .generation_repository import GenerationTaskRepository


def request_hash(value: dict[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def create_generation_task(
    *,
    payload: dict[str, Any],
    tenant_id: str,
    actor_id: str,
    idempotency_key: str,
) -> tuple[dict[str, Any], bool]:
    await enforce_submission_rate(tenant_id, actor_id)
    return await GenerationTaskRepository().create_task(
        payload=payload,
        tenant_id=tenant_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash(payload),
    )


async def approve_generation_task(
    *,
    task_id: str,
    tenant_id: str,
    actor_id: str,
    idempotency_key: str,
    interrupt_id: str,
    content_version: str,
    decision: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    approval_request = {
        "interrupt_id": interrupt_id,
        "content_version": content_version,
        "decision": decision,
        "payload": payload,
    }
    return await GenerationTaskRepository().submit_approval(
        task_id=task_id,
        tenant_id=tenant_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash(approval_request),
        interrupt_id=interrupt_id,
        content_version=content_version,
        decision=decision,
        payload=payload,
    )
