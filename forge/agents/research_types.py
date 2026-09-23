"""Structured contracts shared by research and reflection agents."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict


ResearchStatus = Literal["complete", "degraded", "needs_review", "failed"]
FactStatus = Literal["source_claim", "verified", "disputed", "unsupported", "expired"]
FactReviewDecision = Literal[
    "pending",
    "accepted",
    "excluded",
    "challenged",
    "needs_more_research",
]
FactPriority = Literal["low", "normal", "high"]


class ResearchTask(TypedDict):
    task_id: str
    section_id: str
    question: str
    required: bool
    source_policy: str
    freshness_days: int | None
    status: Literal["pending", "completed", "no_evidence", "failed"]


class Evidence(TypedDict):
    evidence_id: str
    task_id: str
    source: str
    title: str
    url: str
    published_at: str
    retrieved_at: str
    excerpt: str
    source_type: Literal["source_article", "knowledge_base", "web"]
    authority_score: float
    relevance_score: float
    content_hash: str
    chunk_id: str
    section_path: list[str]
    paragraph_index: int
    char_start: int
    char_end: int


class EvidenceSpan(TypedDict):
    evidence_id: str
    chunk_id: str
    quote: str
    char_start: int
    char_end: int


class FactSource(TypedDict):
    evidence_id: str
    source: str
    title: str
    url: str
    published_at: str
    retrieved_at: str


class FactReview(TypedDict):
    decision: FactReviewDecision
    comment: str
    priority: FactPriority
    reviewed_at: str


class Fact(TypedDict):
    fact_id: str
    task_id: str
    section_id: str
    claim: str
    status: FactStatus
    confidence: float
    evidence_ids: list[str]
    sources: list[FactSource]
    review: FactReview
    subject: str
    predicate: str
    object: str
    normalized_value: str
    operator: str
    unit: str
    qualifiers: dict[str, str]
    fact_type: str
    volatility: Literal["low", "medium", "high"]
    valid_from: str
    valid_to: str
    as_of_date: str
    region: str
    source_status: str
    conflict_group: str
    evidence_spans: list[EvidenceSpan]
    article_id: str


class ResearchResult(TypedDict):
    status: ResearchStatus
    tasks: list[ResearchTask]
    evidence: list[Evidence]
    facts: list[Fact]
    coverage: float
    warnings: list[str]
    generated_at: str


class FactReviewInput(TypedDict):
    fact_id: str
    action: Literal[
        "accepted",
        "excluded",
        "challenged",
        "needs_more_research",
    ]
    comment: str
    priority: FactPriority


class ReflectionIssue(TypedDict):
    issue_id: str
    issue_type: str
    severity: Literal["low", "medium", "high"]
    span: str
    fact_id: str
    instruction: str


DraftFactMatchStatus = Literal[
    "supported",
    "contradicted",
    "unsupported",
    "partial",
    "not_verifiable",
]


class DraftFact(TypedDict):
    """A single verifiable claim extracted from the generated draft."""

    draft_fact_id: str
    claim: str
    span: str
    section_id: str
    subject: str
    predicate: str
    value: str
    normalized_value: str
    value_type: str
    unit: str
    qualifiers: dict[str, str]
    matched_fact_ids: list[str]
    match_status: DraftFactMatchStatus
    confidence: float
    conflicting_fields: list[str]


class FactMatchSummary(TypedDict):
    total: int
    supported: int
    contradicted: int
    unsupported: int
    partial: int
    not_verifiable: int


class QualityScores(TypedDict):
    grounding: float
    completeness: float
    structure: float
    style: float


class WordBudget(TypedDict):
    target_chars: int
    min_chars: int
    max_chars: int


class GenerationSection(TypedDict):
    section_id: str
    title: str
    purpose: str
    fact_ids: list[str]
    budget: WordBudget
    actual_chars: int
    status: Literal["pending", "within_budget", "too_short", "too_long", "failed"]
    content: str
    summary: str
    revision_attempts: int


class GenerationPlan(TypedDict):
    mode: Literal["single_pass", "section_sequential", "hybrid"]
    reason: str
    target_chars: int
    min_chars: int
    max_chars: int
    counting_method: str
    sections: list[GenerationSection]


class WritingMemory(TypedDict):
    thesis: str
    terminology: dict[str, str]
    used_fact_ids: list[str]
    section_summaries: dict[str, str]
    open_threads: list[str]
    previous_tail: str
    actual_chars: int
    remaining_target_chars: int


class GeneratorDecision(TypedDict):
    action: Literal[
        "draft_section",
        "adjust_section_length",
        "repair_section_boundary",
        "improve_transition",
        "accept_section",
        "finish_generation",
    ]
    section_id: str
    fact_ids: list[str]
    reason: str


class GeneratorObservation(TypedDict):
    section_id: str
    tool: str
    actual_chars: int
    word_status: Literal["pending", "within_budget", "too_short", "too_long", "failed"]
    fact_boundary_valid: bool
    unsupported_fact_tokens: list[str]
    declared_fact_ids_valid: bool
    missing_fact_ids: list[str]
    outline_boundary_valid: bool
    foreign_section_ids: list[str]
    content_hash: str


class GeneratorAction(TypedDict):
    action_id: str
    tool: str
    section_id: str
    fact_ids: list[str]
    status: Literal["success", "rejected", "failed"]
    reason: str
    observation: GeneratorObservation
    model_calls: int
    message: str


class WordCountReport(TypedDict):
    target_chars: int
    min_chars: int
    max_chars: int
    actual_chars: int
    deviation: float
    status: Literal["within_budget", "too_short", "too_long"]


class RevisionConstraints(TypedDict):
    target_chars: int | None
    tolerance: float
    target_tone: str
    preserve_facts: bool
    preserve_other_sections: bool


class RevisionTask(TypedDict):
    task_id: str
    source: Literal["critic", "user"]
    source_issue_ids: list[str]
    operation_hints: list[str]
    target_section_ids: list[str]
    target_fact_ids: list[str]
    requirements: list[str]
    constraints: RevisionConstraints
    priority: int
    status: Literal["pending", "in_progress", "resolved", "unresolved", "blocked"]
    validation_rules: list[str]


class RevisionPlan(TypedDict):
    revision_cycle: int
    source: Literal["critic", "user"]
    tasks: list[RevisionTask]
    max_actions: int


class RevisionAction(TypedDict):
    action_id: str
    task_id: str
    tool: str
    target_section_ids: list[str]
    status: Literal["success", "rejected", "failed"]
    before_hashes: dict[str, str]
    after_hashes: dict[str, str]
    validation: dict[str, Any]
    message: str


class IssueResolution(TypedDict):
    issue_id: str
    task_id: str
    status: Literal["resolved", "unresolved", "blocked"]
    tool_actions: list[str]
    validation: dict[str, Any]


class TuningIntent(TypedDict):
    intent: Literal["edit", "question", "clarify"]
    original_request: str
    tasks: list[RevisionTask]
    question: str
    clarification_question: str
    reason: str


class ReflectionResult(TypedDict):
    status: Literal["approved", "needs_review", "max_revisions", "failed"]
    draft: str
    approved: bool
    revision_attempts: int
    scores: QualityScores
    issues: list[ReflectionIssue]
    draft_facts: list[DraftFact]
    fact_match_summary: FactMatchSummary
    verification_log: list[str]
    generation_plan: GenerationPlan
    writing_memory: WritingMemory
    section_reports: list[GenerationSection]
    word_count: WordCountReport
    generator_action_log: list[GeneratorAction]
    generator_tool_action_count: int
    generator_model_call_count: int
    generator_stop_reason: str
    revision_plan: RevisionPlan
    revision_action_log: list[RevisionAction]
    issue_resolution_log: list[IssueResolution]
    revision_cycles: int
    tool_action_count: int
    unresolved_issues: list[str]
    reference_pack: dict[str, Any]


def validate_research_result(result: dict[str, Any]) -> list[str]:
    """Return contract violations without mutating the research result."""
    errors: list[str] = []
    for index, item in enumerate(result.get("evidence", []), 1):
        if not str(item.get("evidence_id", "")).strip():
            errors.append(f"evidence at index {index} missing evidence_id")
    evidence_by_id = {
        item.get("evidence_id"): item
        for item in result.get("evidence", [])
        if item.get("evidence_id")
    }
    for evidence_id, item in evidence_by_id.items():
        for field in ("source", "title", "url", "retrieved_at", "excerpt"):
            if not str(item.get(field, "")).strip():
                errors.append(f"evidence {evidence_id} missing {field}")
    for fact in result.get("facts", []):
        fact_id = fact.get("fact_id", "unknown")
        review = fact.get("review")
        if not isinstance(review, dict):
            errors.append(f"fact {fact_id} missing review")
        else:
            decision = review.get("decision")
            if decision not in {
                "pending",
                "accepted",
                "excluded",
                "challenged",
                "needs_more_research",
            }:
                errors.append(f"fact {fact_id} has invalid review decision {decision}")
            if review.get("priority") not in {"low", "normal", "high"}:
                errors.append(f"fact {fact_id} has invalid review priority")
            if decision == "accepted" and fact.get("status") == "unsupported":
                errors.append(f"unsupported fact {fact_id} cannot be accepted")
        for evidence_id in fact.get("evidence_ids", []):
            if evidence_id not in evidence_by_id:
                errors.append(f"fact {fact_id} references unknown evidence {evidence_id}")
        if fact.get("status") in {"source_claim", "verified", "disputed"} and not fact.get("evidence_ids"):
            errors.append(f"evidence-backed fact {fact_id} has no evidence")
        if fact.get("status") in {"source_claim", "verified", "disputed"} and not fact.get("sources"):
            errors.append(f"evidence-backed fact {fact_id} has no source metadata")
        for field in ("subject", "predicate", "object", "normalized_value", "fact_type"):
            if not str(fact.get(field, "")).strip():
                errors.append(f"fact {fact_id} missing {field}")
        for span in fact.get("evidence_spans", []):
            evidence_id = span.get("evidence_id")
            if evidence_id not in evidence_by_id:
                errors.append(f"fact {fact_id} span references unknown evidence {evidence_id}")
                continue
            excerpt = str(evidence_by_id[evidence_id].get("excerpt") or "")
            quote = str(span.get("quote") or "")
            local_start = excerpt.find(quote)
            if local_start < 0:
                errors.append(f"fact {fact_id} quote is not present in evidence {evidence_id}")
                continue
            evidence_start = int(evidence_by_id[evidence_id].get("char_start") or 0)
            expected_start = evidence_start + local_start
            expected_end = expected_start + len(quote)
            if span.get("char_start") != expected_start or span.get("char_end") != expected_end:
                errors.append(f"fact {fact_id} has invalid source offsets for evidence {evidence_id}")
        for source in fact.get("sources", []):
            for field in ("evidence_id", "source", "title", "url", "retrieved_at"):
                if not str(source.get(field, "")).strip():
                    errors.append(f"fact {fact_id} source missing {field}")
            source_evidence_id = source.get("evidence_id")
            if source_evidence_id and source_evidence_id not in evidence_by_id:
                errors.append(
                    f"fact {fact_id} source references unknown evidence {source_evidence_id}"
                )
    return errors


def pending_fact_review() -> FactReview:
    """Create the explicit initial user-review state for a researched fact."""
    return {
        "decision": "pending",
        "comment": "",
        "priority": "normal",
        "reviewed_at": "",
    }


def apply_fact_reviews(
    result: ResearchResult,
    reviews: list[FactReviewInput],
    *,
    accept_all: bool = False,
) -> tuple[ResearchResult, list[str]]:
    """Apply user decisions without allowing unsupported facts to become approved."""
    updated = deepcopy(result)
    errors: list[str] = []
    facts_by_id = {fact["fact_id"]: fact for fact in updated.get("facts", [])}
    reviewed_at = datetime.now(timezone.utc).isoformat()

    if accept_all:
        for fact in facts_by_id.values():
            if fact["status"] == "unsupported":
                fact["review"] = {
                    "decision": "excluded",
                    "comment": "系统自动排除无证据事实",
                    "priority": "normal",
                    "reviewed_at": reviewed_at,
                }
            elif fact["review"]["decision"] == "pending":
                fact["review"] = {
                    "decision": "accepted",
                    "comment": "",
                    "priority": fact["review"]["priority"],
                    "reviewed_at": reviewed_at,
                }

    for review_input in reviews:
        fact_id = str(review_input.get("fact_id") or "").strip()
        fact = facts_by_id.get(fact_id)
        if fact is None:
            errors.append(f"unknown fact_id {fact_id}")
            continue
        action = review_input.get("action")
        if action not in {
            "accepted",
            "excluded",
            "challenged",
            "needs_more_research",
        }:
            errors.append(f"fact {fact_id} has invalid action {action}")
            continue
        if action == "accepted" and fact["status"] == "unsupported":
            errors.append(f"unsupported fact {fact_id} cannot be accepted")
            continue
        priority = review_input.get("priority", "normal")
        if priority not in {"low", "normal", "high"}:
            errors.append(f"fact {fact_id} has invalid priority {priority}")
            continue
        fact["review"] = {
            "decision": action,
            "comment": str(review_input.get("comment") or "").strip(),
            "priority": priority,
            "reviewed_at": reviewed_at,
        }

    return updated, errors


def is_fact_approved(fact: Fact) -> bool:
    """Only explicitly accepted, evidence-backed facts may enter writing."""
    return (
        fact["status"] in {"source_claim", "verified", "disputed"}
        and fact["review"]["decision"] == "accepted"
    )


def format_verified_facts(result: ResearchResult) -> str:
    """Render user-approved facts with traceable sources for LLM prompts."""
    lines: list[str] = []
    for fact in result.get("facts", []):
        if not is_fact_approved(fact):
            continue
        lines.append(
            f"[{fact['fact_id']}] ({fact['status']}, confidence={fact['confidence']:.2f}) "
            f"{fact['claim']} | subject={fact.get('subject', '')} | "
            f"predicate={fact.get('predicate', '')} | value={fact.get('normalized_value', '')} "
            f"{fact.get('unit', '')} | volatility={fact.get('volatility', '')} | "
            f"as_of={fact.get('as_of_date') or 'unknown'} | priority={fact['review']['priority']}"
        )
        for span in fact.get("evidence_spans", []):
            lines.append(
                f"  - quote={span['quote']} | chunk={span['chunk_id']} | "
                f"chars={span['char_start']}:{span['char_end']}"
            )
        for source in fact.get("sources", []):
            lines.append(
                f"  - evidence_id={source['evidence_id']} | source={source['source']} | title={source['title']} | "
                f"url={source['url']} | published_at={source['published_at'] or 'unknown'} | "
                f"retrieved_at={source['retrieved_at']}"
            )
    return "\n".join(lines) or "没有经用户确认的事实；不得生成文章或大纲。"


__all__ = [
    "DraftFact",
    "DraftFactMatchStatus",
    "Evidence",
    "Fact",
    "FactMatchSummary",
    "FactPriority",
    "FactReview",
    "FactReviewDecision",
    "FactReviewInput",
    "FactSource",
    "GenerationPlan",
    "GenerationSection",
    "GeneratorAction",
    "GeneratorDecision",
    "GeneratorObservation",
    "QualityScores",
    "ReflectionIssue",
    "ReflectionResult",
    "ResearchResult",
    "ResearchTask",
    "RevisionAction",
    "RevisionConstraints",
    "RevisionPlan",
    "RevisionTask",
    "IssueResolution",
    "TuningIntent",
    "WordBudget",
    "WordCountReport",
    "WritingMemory",
    "apply_fact_reviews",
    "format_verified_facts",
    "is_fact_approved",
    "pending_fact_review",
    "validate_research_result",
]
