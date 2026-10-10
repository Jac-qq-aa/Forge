from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class Intent(str, Enum):
    GENERATE = "generate"
    MODIFY = "modify"
    QUESTION = "question"
    CLARIFY = "clarify"


class KnowledgeScope(str, Enum):
    PUBLIC = "public"
    COMPANY = "company"
    PERSONAL = "personal"


class Identity(BaseModel):
    user_id: UUID
    tenant_id: UUID | None = None
    account_type: Literal["guest", "employee", "admin"] = "guest"
    roles: frozenset[str] = Field(default_factory=frozenset)
    knowledge_space_ids: frozenset[UUID] = Field(default_factory=frozenset)


class Evidence(BaseModel):
    evidence_id: UUID = Field(default_factory=uuid4)
    source_kind: Literal["source_article", "database", "skill", "skill_md", "company_document", "web"]
    title: str = ""
    url: str = ""
    quote: str = ""
    document_id: UUID | None = None
    document_version: str = ""
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    authority: float = Field(default=0.5, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArticleFact(BaseModel):
    fact_id: UUID = Field(default_factory=uuid4)
    version: int = Field(default=1, ge=1)
    claim: str = Field(min_length=1)
    subject: str = ""
    predicate: str = ""
    value: str = ""
    unit: str = ""
    qualifiers: dict[str, str] = Field(default_factory=dict)
    origin: Literal["source", "company", "web", "history", "user", "draft"] = "source"
    evidence: list[Evidence] = Field(default_factory=list)
    verification: Literal["verified", "unverified", "conflicted", "pending"] = "pending"
    marked: bool = False
    mark_reasons: set[Literal["user_created", "user_edited", "user_deleted", "user_locked", "conflict"]] = Field(
        default_factory=set
    )
    excluded: bool = False
    required_for_article: bool = False
    decision_id: UUID | None = None

    @model_validator(mode="after")
    def enforce_marking(self) -> "ArticleFact":
        if self.mark_reasons:
            self.marked = True
        if self.origin == "user" and not self.evidence:
            self.marked = True
            self.mark_reasons.add("user_created")
            if self.verification == "pending":
                self.verification = "unverified"
        if self.origin in {"source", "user"}:
            self.required_for_article = True
        return self

    def semantic_key(self) -> str:
        payload = {
            "subject": self.subject.strip().casefold(),
            "predicate": self.predicate.strip().casefold(),
            "value": self.value.strip().casefold(),
            "unit": self.unit.strip().casefold(),
            "qualifiers": self.qualifiers,
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()


class OutlineParagraph(BaseModel):
    paragraph_id: UUID = Field(default_factory=uuid4)
    order: int = Field(ge=1)
    purpose: str = Field(min_length=1)
    main_points: list[str] = Field(min_length=1)
    fact_ids: list[UUID] = Field(min_length=1)
    required_fact_ids: list[UUID] = Field(default_factory=list)


class OutlineSection(BaseModel):
    section_id: UUID = Field(default_factory=uuid4)
    order: int = Field(ge=1)
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    main_points: list[str] = Field(min_length=1)
    fact_ids: list[UUID] = Field(default_factory=list)
    paragraphs: list[OutlineParagraph] = Field(min_length=1)


class CriticIssue(BaseModel):
    issue_id: UUID = Field(default_factory=uuid4)
    kind: Literal["fact", "outline", "business", "legal", "style", "duplication", "platform"]
    severity: Literal["error", "warning"] = "error"
    rule_code: str = Field(default="unspecified", min_length=1, max_length=80)
    message: str
    section_id: UUID | None = None
    paragraph_id: UUID | None = None
    claim: str = ""
    expected_fact: str = ""
    fact_id: UUID | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    requires_user: bool = False

    def fingerprint(self) -> str:
        stable = f"{self.kind}|{self.rule_code.casefold()}|{self.section_id or ''}|{self.fact_id or ''}"
        return hashlib.sha256(stable.encode("utf-8")).hexdigest()

