from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from forge.domain.models import ArticleFact


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=10, max_length=200)


class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    source_text: str = Field(default="", max_length=500_000)
    platform: str = Field(default="", max_length=100)
    idempotency_key: UUID | None = None


class FactChangeRequest(BaseModel):
    operation: str = Field(pattern="^(create|update|delete)$")
    fact: ArticleFact | None = None
    fact_id: UUID | None = None
    expected_version: int = Field(ge=0)


class WorkflowDecisionRequest(BaseModel):
    job_id: UUID
    fact_version: int = Field(ge=1)
    decision: dict[str, Any]


class JobAccepted(BaseModel):
    job_id: UUID
    status: str = "pending"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
