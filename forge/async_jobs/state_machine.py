"""文章生成任务的状态与错误分类。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class GenerationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


TERMINAL_GENERATION_STATUSES = {
    GenerationStatus.SUCCEEDED,
    GenerationStatus.FAILED,
    GenerationStatus.CANCELLED,
}

LEGAL_TRANSITIONS: dict[GenerationStatus, frozenset[GenerationStatus]] = {
    GenerationStatus.QUEUED: frozenset(
        {GenerationStatus.RUNNING, GenerationStatus.CANCELLING, GenerationStatus.CANCELLED}
    ),
    GenerationStatus.RUNNING: frozenset(
        {
            GenerationStatus.WAITING_APPROVAL,
            GenerationStatus.RETRY_WAIT,
            GenerationStatus.SUCCEEDED,
            GenerationStatus.FAILED,
            GenerationStatus.CANCELLING,
            GenerationStatus.CANCELLED,
        }
    ),
    GenerationStatus.WAITING_APPROVAL: frozenset(
        {GenerationStatus.QUEUED, GenerationStatus.CANCELLING, GenerationStatus.CANCELLED}
    ),
    GenerationStatus.RETRY_WAIT: frozenset(
        {GenerationStatus.QUEUED, GenerationStatus.CANCELLING, GenerationStatus.CANCELLED}
    ),
    GenerationStatus.CANCELLING: frozenset({GenerationStatus.CANCELLED}),
    GenerationStatus.SUCCEEDED: frozenset(),
    GenerationStatus.FAILED: frozenset(),
    GenerationStatus.CANCELLED: frozenset(),
}


class InvalidTaskTransition(RuntimeError):
    """任务状态转换不合法。"""


class StaleApproval(RuntimeError):
    """审批不属于当前 interrupt 或内容版本。"""


class ApprovalConflict(RuntimeError):
    """审批幂等键或审批目标发生冲突。"""


class TaskNotFound(RuntimeError):
    """任务不存在或不属于当前用户。"""


class ExecutionLeaseLost(RuntimeError):
    """执行租约已被其他 Worker 接管。"""


class TaskCancelled(RuntimeError):
    """任务已请求取消。"""


@dataclass(frozen=True)
class ErrorDisposition:
    retryable: bool
    code: str
    message: str


def ensure_transition(current: str, target: str) -> None:
    source = GenerationStatus(current)
    destination = GenerationStatus(target)
    if destination not in LEGAL_TRANSITIONS[source]:
        raise InvalidTaskTransition(f"非法任务状态转换: {source.value} -> {destination.value}")


def classify_execution_error(exc: BaseException) -> ErrorDisposition:
    """将执行错误分成可重试和不可重试两类。"""
    from asyncio import TimeoutError as AsyncTimeoutError

    message = str(exc) or exc.__class__.__name__
    lowered = message.lower()
    if isinstance(exc, ValueError):
        return ErrorDisposition(False, "invalid_request", message)
    if isinstance(exc, PermissionError):
        return ErrorDisposition(False, "permission_denied", message)
    if any(token in lowered for token in ("401", "403", "permission", "unauthorized")):
        return ErrorDisposition(False, "permission_denied", message)
    if isinstance(exc, (TimeoutError, AsyncTimeoutError)) or any(
        token in lowered
        for token in ("timeout", "temporarily unavailable", "rate limit", "429", "502", "503", "504")
    ):
        return ErrorDisposition(True, "transient_dependency", message)
    return ErrorDisposition(True, "execution_error", message)


def public_task_view(task: dict[str, Any]) -> dict[str, Any]:
    """构建 API 返回，避免暴露内部租约和完整输入。"""
    return {
        "task_id": task["id"],
        "thread_id": task.get("thread_id"),
        "status": task["status"],
        "current_stage": task.get("current_stage"),
        "revision_round": task.get("revision_round", 0),
        "version": task.get("version", 0),
        "pending_approval": task.get("pending_approval"),
        "interrupt_id": task.get("pending_interrupt_id"),
        "content_version": task.get("content_version"),
        "result": task.get("result"),
        "last_error": task.get("error"),
        "last_error_code": task.get("last_error_code"),
        "cancel_requested": task.get("cancel_requested", False),
        "attempt_count": task.get("attempt_count", 0),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
        "finished_at": task.get("finished_at"),
    }
