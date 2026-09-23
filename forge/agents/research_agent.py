"""Structured research pipeline: plan, gather, normalize, and verify evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from tavily import TavilyClient

from forge.agents.research_types import (
    Evidence,
    Fact,
    FactSource,
    ResearchResult,
    ResearchTask,
    apply_fact_reviews,
    pending_fact_review,
    validate_research_result,
)
from forge.tools.llm_client import LLMClient
from forge.facts import SourceFactExtractor

logger = logging.getLogger(__name__)

MAX_RESEARCH_TASKS = 8
MAX_WEB_EVIDENCE = 12
MIN_RESEARCH_COVERAGE = 0.8


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("LLM response does not contain a JSON object")
        candidate = candidate[start:end + 1]
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def _fallback_tasks(outline: str) -> list[ResearchTask]:
    headings = [
        line.strip()
        for line in outline.splitlines()
        if line.strip() and (
            re.match(r"^[一二三四五六七八九十]+[、.]", line.strip())
            or line.strip().startswith("#")
        )
    ]
    if not headings:
        headings = [outline.strip()[:200] or "核实原文章核心事实"]
    return [
        {
            "task_id": f"T{index:02d}",
            "section_id": f"S{index:02d}",
            "question": f"为章节“{heading.lstrip('#').strip()}”收集可验证事实和一手来源",
            "required": True,
            "source_policy": "official_preferred",
            "freshness_days": None,
            "status": "pending",
        }
        for index, heading in enumerate(headings[:MAX_RESEARCH_TASKS], 1)
    ]


async def plan_research_tasks(
    outline: str,
    raw_content: dict[str, Any],
    user_input: str,
    source_facts: list[Fact],
) -> tuple[list[ResearchTask], list[str]]:
    """Create bounded, section-linked research tasks."""
    source_fact_summary = "\n".join(
        f"- {fact['subject']} | {fact['predicate']} | {fact['normalized_value']} "
        f"| volatility={fact['volatility']} | claim={fact['claim']}"
        for fact in source_facts[:120]
    ) or "未抽取到原文事实候选"
    prompt = f"""你是研究规划器。根据文章主题、完整原文事实候选和用户要求生成需要外部核验的研究任务。

文章主题或暂定方向：
{outline}

原文标题：{raw_content.get('title', '')}
原文摘要：{str(raw_content.get('text') or raw_content.get('content') or '')[:3000]}
完整原文事实候选：
{source_fact_summary}
用户要求：{user_input}

只输出 JSON：
{{
  "tasks": [
    {{
      "section_id": "S01",
      "question": "需要核验的明确问题",
      "required": true,
      "source_policy": "official_preferred",
      "freshness_days": null
    }}
  ]
}}

规则：最多 {MAX_RESEARCH_TASKS} 项；稳定且无需核验的原文陈述可以不搜索；高时效事实、数字、日期、
机构资质和政策条款优先核验；任务必须能指向明确事实关系。
"""
    warnings: list[str] = []
    try:
        response = await LLMClient().chat_with_retry(prompt)
        payload = _parse_json_object(response)
        tasks: list[ResearchTask] = []
        for index, item in enumerate(payload.get("tasks", [])[:MAX_RESEARCH_TASKS], 1):
            question = str(item.get("question", "")).strip()
            if not question:
                continue
            freshness = item.get("freshness_days")
            tasks.append({
                "task_id": f"T{index:02d}",
                "section_id": str(item.get("section_id") or f"S{index:02d}"),
                "question": question,
                "required": bool(item.get("required", True)),
                "source_policy": str(item.get("source_policy") or "official_preferred"),
                "freshness_days": int(freshness) if freshness is not None else None,
                "status": "pending",
            })
        if tasks:
            return tasks, warnings
        raise ValueError("planner returned no usable tasks")
    except Exception as exc:
        warnings.append(f"research planner degraded: {exc}")
        return _fallback_tasks(outline), warnings


def _make_evidence(
    *,
    evidence_id: str,
    task_id: str,
    source: str,
    title: str,
    url: str,
    published_at: str,
    excerpt: str,
    source_type: str,
    authority_score: float,
    relevance_score: float,
) -> Evidence:
    normalized_excerpt = re.sub(r"\s+", " ", excerpt).strip()[:3000]
    return {
        "evidence_id": evidence_id,
        "task_id": task_id,
        "source": source.strip(),
        "title": title.strip(),
        "url": url.strip(),
        "published_at": published_at.strip(),
        "retrieved_at": _utc_now(),
        "excerpt": normalized_excerpt,
        "source_type": source_type,  # type: ignore[typeddict-item]
        "authority_score": round(max(0.0, min(1.0, authority_score)), 3),
        "relevance_score": round(max(0.0, min(1.0, relevance_score)), 3),
        "content_hash": hashlib.sha256(normalized_excerpt.encode("utf-8")).hexdigest(),
        "chunk_id": evidence_id,
        "section_path": [],
        "paragraph_index": 0,
        "char_start": 0,
        "char_end": len(normalized_excerpt),
    }


def _seed_evidence(source_evidence: list[Evidence], rag_context: str) -> list[Evidence]:
    evidence = [dict(item) for item in source_evidence]  # type: ignore[misc]
    if rag_context.strip():
        evidence.append(_make_evidence(
            evidence_id=f"E{len(evidence) + 1:03d}",
            task_id="knowledge_base",
            source="锐博内部向量知识库",
            title="与当前主题相关的内部知识片段",
            url="milvus://ruibo_knowledge_v2",
            published_at="",
            excerpt=rag_context,
            source_type="knowledge_base",
            authority_score=0.85,
            relevance_score=0.8,
        ))
    return evidence


def _domain_source(url: str) -> str:
    return urlparse(url).netloc.lower() or "外部网页"


def _authority_score(url: str, source_policy: str) -> float:
    domain = urlparse(url).netloc.lower()
    if domain.endswith("gov.cn") or domain.endswith("edu.cn"):
        return 0.95
    if source_policy == "official_only":
        return 0.5
    return 0.65


async def gather_evidence(
    tasks: list[ResearchTask],
    source_evidence: list[Evidence],
    rag_context: str,
) -> tuple[list[Evidence], list[str]]:
    """Gather source, knowledge-base, and bounded web evidence."""
    evidence = _seed_evidence(source_evidence, rag_context)
    warnings: list[str] = []
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        warnings.append("TAVILY_API_KEY is missing; web research skipped")
        return evidence, warnings

    client = TavilyClient(api_key=api_key)
    semaphore = asyncio.Semaphore(4)

    async def search_task(task: ResearchTask) -> tuple[str, list[dict[str, Any]], str]:
        async with semaphore:
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.search,
                        query=task["question"],
                        max_results=3,
                        include_raw_content=True,
                    ),
                    timeout=20.0,
                )
                return task["task_id"], response.get("results", []), ""
            except Exception as exc:
                return task["task_id"], [], str(exc)

    results = await asyncio.gather(*(search_task(task) for task in tasks))
    task_by_id = {task["task_id"]: task for task in tasks}
    seen = {(item["url"], item["content_hash"]) for item in evidence}
    web_evidence_count = 0
    for task_id, items, error in results:
        task = task_by_id[task_id]
        if error:
            task["status"] = "failed"
            warnings.append(f"{task_id} search failed: {error}")
            continue
        added = 0
        for item in items:
            if web_evidence_count >= MAX_WEB_EVIDENCE:
                break
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or url or "未命名网页").strip()
            excerpt = str(item.get("raw_content") or item.get("content") or "").strip()
            if not url or not excerpt:
                continue
            candidate = _make_evidence(
                evidence_id=f"E{len(evidence) + 1:03d}",
                task_id=task_id,
                source=_domain_source(url),
                title=title,
                url=url,
                published_at=str(item.get("published_date") or ""),
                excerpt=excerpt,
                source_type="web",
                authority_score=_authority_score(url, task["source_policy"]),
                relevance_score=float(item.get("score") or 0.5),
            )
            key = (candidate["url"], candidate["content_hash"])
            if key in seen:
                continue
            seen.add(key)
            evidence.append(candidate)
            added += 1
            web_evidence_count += 1
        task["status"] = "completed" if added else "no_evidence"
    return evidence, warnings


def _fact_identity(fact: Fact) -> tuple[Any, ...]:
    return (
        str(fact.get("subject") or "").strip(),
        str(fact.get("predicate") or "").strip(),
        str(fact.get("normalized_value") or fact.get("object") or "").strip(),
        str(fact.get("unit") or "").strip(),
        tuple(sorted((fact.get("qualifiers") or {}).items())),
    )


def _merge_fact_registry(source_facts: list[Fact], researched_facts: list[Fact]) -> list[Fact]:
    """Merge corroborating evidence without losing source spans or conflict states."""
    merged = [dict(fact) for fact in source_facts]  # type: ignore[misc]
    by_identity = {_fact_identity(fact): fact for fact in merged}
    for researched in researched_facts:
        identity = _fact_identity(researched)
        existing = by_identity.get(identity)
        if existing is None:
            merged.append(researched)
            by_identity[identity] = researched
            continue
        for evidence_id in researched["evidence_ids"]:
            if evidence_id not in existing["evidence_ids"]:
                existing["evidence_ids"].append(evidence_id)
        existing["sources"].extend(
            source for source in researched["sources"] if source not in existing["sources"]
        )
        existing["evidence_spans"].extend(
            span for span in researched["evidence_spans"] if span not in existing["evidence_spans"]
        )
        if researched["status"] in {"verified", "disputed"}:
            existing["status"] = researched["status"]
            existing["source_status"] = researched["status"]
            existing["confidence"] = max(existing["confidence"], researched["confidence"])
    conflict_groups: dict[tuple[Any, ...], list[Fact]] = {}
    for fact in merged:
        key = (
            fact.get("subject", ""), fact.get("predicate", ""), fact.get("unit", ""),
            tuple(sorted((fact.get("qualifiers") or {}).items())),
        )
        conflict_groups.setdefault(key, []).append(fact)  # type: ignore[arg-type]
    for key, grouped in conflict_groups.items():
        if len({fact.get("normalized_value", "") for fact in grouped}) <= 1:
            continue
        conflict_group = "CG-" + hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:12]
        for fact in grouped:
            fact["status"] = "disputed"
            fact["source_status"] = "disputed"
            fact["conflict_group"] = conflict_group
    for index, fact in enumerate(merged, 1):
        fact["fact_id"] = f"F{index:03d}"
    return merged  # type: ignore[return-value]


async def verify_facts(
    outline: str,
    tasks: list[ResearchTask],
    evidence: list[Evidence],
    source_facts: list[Fact],
) -> tuple[list[Fact], list[str]]:
    """Build a claim registry whose citations must resolve to gathered evidence."""
    warnings: list[str] = []
    evidence_by_id = {item["evidence_id"]: item for item in evidence}
    external_evidence = [
        item for item in evidence if item["source_type"] != "source_article"
    ]
    evidence_text = "\n\n".join(
        f"[{item['evidence_id']}] task={item['task_id']} source={item['source']} "
        f"title={item['title']} url={item['url']} published_at={item['published_at'] or 'unknown'}\n"
        f"excerpt={item['excerpt'][:1200]}"
        for item in external_evidence
    ) or "无原文之外的核验证据"
    task_text = "\n".join(
        f"{task['task_id']} section={task['section_id']} question={task['question']}"
        for task in tasks
    )
    source_fact_text = "\n".join(
        f"[{fact['fact_id']}] {fact['subject']} | {fact['predicate']} | "
        f"{fact['normalized_value']} | claim={fact['claim']}"
        for fact in source_facts
    ) or "无"
    prompt = f"""你是事实核验器。只能根据给定证据建立事实注册表，禁止使用记忆补充。

大纲：
{outline}

研究任务：
{task_text}

已经从原文精确定位的事实候选：
{source_fact_text}

证据：
{evidence_text}

只输出 JSON：
{{"facts":[{{
  "task_id":"T01",
  "section_id":"S01",
  "claim":"可直接用于写作的原子事实",
  "status":"verified|disputed|unsupported",
  "confidence":0.0,
  "evidence_ids":["E001"],
  "subject":"主体",
  "predicate":"snake_case 关系",
  "object":"对象或原始值",
  "normalized_value":"规范化值",
  "operator":"=|>|>=|<|<=|约",
  "unit":"单位或空字符串",
  "qualifiers":{{"year":"2025"}},
  "fact_type":"company_fact|policy_fact|event_fact|general_fact"
}}]}}

规则：verified 必须有原文之外的直接支持证据；只有 source_article 支持时保持原文候选，不重复输出；
来源冲突用 disputed；没有证据用 unsupported；数字和日期必须逐字可在证据中找到。
"""
    try:
        response = await LLMClient().chat_with_retry(prompt)
        payload = _parse_json_object(response)
    except Exception as exc:
        warnings.append(f"fact verifier degraded: {exc}")
        return source_facts, warnings

    facts: list[Fact] = []
    for index, item in enumerate(payload.get("facts", []), 1):
        claim = str(item.get("claim") or "").strip()
        if not claim:
            continue
        evidence_ids = [
            str(value)
            for value in item.get("evidence_ids", [])
            if str(value) in evidence_by_id
        ]
        status = str(item.get("status") or "unsupported")
        if status not in {"verified", "disputed", "unsupported"}:
            status = "unsupported"
        external_evidence_ids = [
            evidence_id for evidence_id in evidence_ids
            if evidence_by_id[evidence_id]["source_type"] != "source_article"
        ]
        if status == "verified" and not external_evidence_ids:
            status = "unsupported"
        sources: list[FactSource] = []
        for evidence_id in evidence_ids:
            source = evidence_by_id[evidence_id]
            sources.append({
                key: source[key]
                for key in ("evidence_id", "source", "title", "url", "published_at", "retrieved_at")
            })  # type: ignore[arg-type]
        facts.append({
            "fact_id": f"F{index:03d}",
            "task_id": str(item.get("task_id") or "unassigned"),
            "section_id": str(item.get("section_id") or "unassigned"),
            "claim": claim,
            "status": status,  # type: ignore[typeddict-item]
            "confidence": round(max(0.0, min(1.0, float(item.get("confidence") or 0.0))), 3),
            "evidence_ids": evidence_ids,
            "sources": sources,
            "review": pending_fact_review(),
            "subject": str(item.get("subject") or "").strip(),
            "predicate": str(item.get("predicate") or "general_fact").strip(),
            "object": str(item.get("object") or claim).strip(),
            "normalized_value": str(item.get("normalized_value") or item.get("object") or claim).strip(),
            "operator": str(item.get("operator") or "=").strip(),
            "unit": str(item.get("unit") or "").strip(),
            "qualifiers": {
                str(key): str(value)
                for key, value in (
                    item.get("qualifiers", {}).items()
                    if isinstance(item.get("qualifiers"), dict) else []
                )
            },
            "fact_type": str(item.get("fact_type") or "general_fact").strip(),
            "volatility": "medium",
            "valid_from": "",
            "valid_to": "",
            "as_of_date": "",
            "region": str(item.get("region") or "").strip(),
            "source_status": status,
            "conflict_group": "",
            "evidence_spans": [],
            "article_id": "",
        })
    usable = [
        fact for fact in facts
        if fact["subject"] and fact["predicate"] and fact["object"]
    ]
    return _merge_fact_registry(source_facts, usable), warnings


def _coverage(tasks: list[ResearchTask], facts: list[Fact]) -> float:
    required = {task["task_id"] for task in tasks if task["required"]}
    if not required:
        return 1.0
    covered = {
        fact["task_id"]
        for fact in facts
        if fact["status"] == "verified" and fact["task_id"] in required
    }
    return round(len(covered) / len(required), 3)


async def run_research_agent(
    outline: str,
    raw_content: dict[str, Any],
    user_input: str,
    require_user_review: bool,
    rag_context: str = "",
) -> ResearchResult:
    """Run the bounded research pipeline and return traceable structured facts."""
    extraction = await SourceFactExtractor().extract(raw_content)
    tasks, warnings = await plan_research_tasks(
        outline,
        raw_content,
        user_input,
        extraction.facts,  # type: ignore[arg-type]
    )
    warnings.extend(extraction.warnings)
    evidence, gather_warnings = await gather_evidence(
        tasks,
        extraction.evidence,  # type: ignore[arg-type]
        rag_context,
    )
    warnings.extend(gather_warnings)
    facts, verification_warnings = await verify_facts(
        outline,
        tasks,
        evidence,
        extraction.facts,  # type: ignore[arg-type]
    )
    warnings.extend(verification_warnings)
    coverage = _coverage(tasks, facts)
    result: ResearchResult = {
        "status": "complete" if coverage >= MIN_RESEARCH_COVERAGE else "degraded",
        "tasks": tasks,
        "evidence": evidence,
        "facts": facts,
        "coverage": coverage,
        "warnings": warnings,
        "generated_at": _utc_now(),
    }
    if not require_user_review:
        result, _ = apply_fact_reviews(result, [], accept_all=True)
    contract_errors = validate_research_result(result)
    if contract_errors:
        result["warnings"].extend(contract_errors)
        result["status"] = "needs_review"
    logger.info(
        "[ResearchAgent] status=%s tasks=%s evidence=%s facts=%s coverage=%.3f",
        result["status"],
        len(tasks),
        len(evidence),
        len(facts),
        coverage,
    )
    return result


__all__ = [
    "gather_evidence",
    "plan_research_tasks",
    "run_research_agent",
    "verify_facts",
]
