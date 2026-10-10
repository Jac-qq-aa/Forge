from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


JobKind = Literal["start", "index_document", "audit_job"]

COMMAND_ROUTES = {
    "start": ("forge-workflow-commands", "forge-workflow-workers"),
    "index_document": ("forge-index-commands", "forge-index-workers"),
    "audit_job": ("forge-audit-commands", "forge-audit-workers"),
}


class WorkflowCommand(BaseModel):
    """RocketMQ 中传输的稳定命令格式。"""

    schema_version: int = Field(default=1, ge=1)
    message_id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    conversation_id: UUID
    tenant_id: UUID | None = None
    actor_user_id: UUID
    kind: JobKind
    attempt: int = Field(default=0, ge=0)
    trace_id: UUID = Field(default_factory=uuid4)
    trace_context: dict[str, str] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any]

    @property
    def topic(self) -> str:
        return COMMAND_ROUTES[self.kind][0]

    def transport_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class WorkflowSubmission(BaseModel):
    thread_id: str
    run_id: str


class WorkflowRunState(BaseModel):
    thread_id: str
    run_id: str
    status: Literal["pending", "running", "error", "success", "timeout", "interrupted"]
    values: dict[str, Any] = Field(default_factory=dict)
    interrupts: list[dict[str, Any]] = Field(default_factory=list)
