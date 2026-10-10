from __future__ import annotations

from typing import Any, Literal, TypedDict


class WorkflowState(TypedDict, total=False):
    trace_id: str
    job_id: str
    conversation_id: str
    article_id: str
    identity: dict[str, Any]
    user_message: str
    requested_operation: Literal["generate", "modify", "message"]
    intent: Literal["generate", "modify", "question", "clarify"]
    source_text: str
    direction: str
    platform: str
    article_type: str
    article_type_name: str
    article_skill: str
    article_thesis: str
    key_points: list[str]
    fact_policy: str
    facts: list[dict[str, Any]]
    baseline_facts: list[dict[str, Any]]
    fact_version: int
    initial_fact_version: int
    outline: list[dict[str, Any]]
    outline_confirmed: bool
    facts_confirmed: bool
    draft_paragraphs: list[dict[str, Any]]
    draft_history: list[dict[str, Any]]
    review_history: list[dict[str, Any]]
    current_paragraph_id: str
    current_draft_version: int
    outline_version: int
    approval: dict[str, Any]
    draft: str
    answer: str
    critic_issues: list[dict[str, Any]]
    fact_checks: list[dict[str, Any]]
    issue_history: list[str]
    critic_failures: int
    critic_status: Literal["pass", "fail", "needs_user", "incomplete", "stopped"] | None
    pending_conflicts: list[dict[str, Any]]
    system_decisions: list[dict[str, Any]]
    user_decisions: list[dict[str, Any]]
    warnings: list[str]
    stage: str
    stop_reason: str
    error: str
    facts_changed: bool
    outline_changed: bool
    draft_changed: bool
    pause_request: dict[str, Any]
    pause_decision: dict[str, Any]
