from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Sequence
import json

from forge.application.ports import EvidenceSearch, ModelGateway, ReadOnlyQuery
from forge.domain.models import ArticleFact, Evidence, Identity
from forge.infrastructure.telemetry import emit, error_fields


@dataclass(frozen=True)
class ResearchOutcome:
    thesis: str
    key_points: list[str]
    facts: list[ArticleFact]
    warnings: list[str]
    completed_sources: frozenset[str]


class EvidenceResearcher:
    """Extract a concise source-led fact set; retrieval adds screened evidence by role."""

    def __init__(
        self, model: ModelGateway, search: EvidenceSearch, company: ReadOnlyQuery, fact_skill: str
    ) -> None:
        self._model = model
        self._search = search
        self._company = company
        self._fact_skill = fact_skill

    async def collect(
        self, *, source_text: str, direction: str, user_request: str, identity: Identity
    ) -> ResearchOutcome:
        thesis, key_points, source_facts = await self._extract(source_text, direction, user_request)
        await emit("research.core_facts_selected", fact_count=len(source_facts), key_point_count=len(key_points))
        if not source_facts:
            return ResearchOutcome(thesis, key_points, [], ["原文中未能提取到有引用依据的主旨事实"], frozenset())

        company_query = (
            f"仅为文章补充公司自身资料。文章主旨：{thesis}\n"
            f"需要说明的公司背景：{'；'.join(key_points)}\n"
            "查询适用的公司主体、关系、业务、资质或服务方式；不要返回一般行业知识。"
        )[:6000]
        calls = {"database": asyncio.create_task(self._company.ask(company_query, identity=identity)),
                 "vector": asyncio.create_task(self._search.semantic(
                     f"文章主旨：{thesis}\n表达重点：{'；'.join(key_points)}", identity=identity,
                     scope="company"))}
        for fact in source_facts:
            query = f"{fact.claim} {fact.qualifiers.get('supports_point', '')}"[:1200]
            calls[f"web:{fact.fact_id}"] = asyncio.create_task(self._search.web(query))
        evidence: list[Evidence] = []
        warnings: list[str] = []
        completed: set[str] = set()
        for source, task in calls.items():
            try:
                source_evidence = list(await task)
                if source.startswith("web:"):
                    for item in source_evidence:
                        item.metadata["related_fact_id"] = source.split(":", 1)[1]
                evidence.extend(source_evidence)
                completed.add("web" if source.startswith("web:") else source)
                for warning in sorted({
                    str(item.metadata["warning"]) for item in source_evidence
                    if item.source_kind == "skill_md" and item.metadata.get("warning")
                }):
                    if warning not in warnings:
                        warnings.append(warning)
            except Exception as exc:
                await emit("fallback.used", status="degraded", reason="research_source_failed", source=source,
                           strategy="remaining_sources", **error_fields(exc))
                warnings.append(f"{source.split(':', 1)[0]} 检索失败：{type(exc).__name__}: {exc}")

        supplemental, rejected = await self._screen_evidence(
            evidence, thesis, key_points, source_facts
        ) if evidence else ([], [])
        if rejected:
            warnings.append(f"已隔离 {len(rejected)} 条无关、冲突或证据不足的检索候选")

        facts = [*source_facts]
        for fact in supplemental:
            key = fact.semantic_key()
            match = next((existing for existing in facts if existing.semantic_key() == key), None)
            if match is None:
                facts.append(fact)
            else:
                seen = {item.evidence_id for item in match.evidence}
                match.evidence.extend(item for item in fact.evidence if item.evidence_id not in seen)
                if fact.verification == "verified":
                    match.verification = "verified"
        return ResearchOutcome(thesis, key_points, facts, warnings, frozenset(completed))

    async def _extract(
        self, source_text: str, direction: str, user_request: str
    ) -> tuple[str, list[str], list[ArticleFact]]:
        result = await self._model.structured(
            system=(
                "你负责从员工粘贴的原文中挑出少量关键事实。先概括一句文章主旨，再列出3至5个读者应理解的表达重点。"
                "逐条筛选候选：必须直接支撑一个表达重点，说明删掉会损失什么，并引用原文中的逐字片段。"
                "优先保留核心结论、必要依据、关键数据/机制及限制条件。排除重复、宣传语、装饰性背景、"
                "无关沿革、人名地点和不影响理解的旁支。相同意义合并为最完整的一条；事实数量依内容决定，不能凑数。"
                "保留时间、对象、地域、统计口径、适用范围和例外。不得扩展个案为普遍结论。"
                "事实筛选规范如下：\n" + self._fact_skill
            ),
            prompt=f"写作方向：{direction}\n用户修改要求：{user_request}\n原文：\n{source_text}",
            schema={
                "type": "object",
                "properties": {
                    "thesis": {"type": "string"},
                    "key_points": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5},
                    "facts": {"type": "array", "items": {
                        "type": "object",
                        "properties": {
                            "claim": {"type": "string"}, "subject": {"type": "string"},
                            "predicate": {"type": "string"}, "value": {"type": "string"},
                            "unit": {"type": "string"}, "quote": {"type": "string"},
                            "supports_point": {"type": "string"}, "omission_impact": {"type": "string"},
                            "fact_role": {"enum": ["core"]},
                            "include": {"type": "boolean"},
                        },
                        "required": ["claim", "quote", "supports_point", "omission_impact", "fact_role", "include"],
                    }},
                },
                "required": ["thesis", "key_points", "facts"],
            },
        )
        thesis = str(result["thesis"]).strip()
        if not thesis:
            raise ValueError("文章主旨不能为空")
        key_points = [str(item).strip() for item in result["key_points"] if str(item).strip()][:5]
        if not key_points:
            raise ValueError("至少需要一个文章表达重点")
        facts: list[ArticleFact] = []
        for raw in result["facts"]:
            quote = str(raw.get("quote", ""))
            supports = str(raw.get("supports_point", "")).strip()
            impact = str(raw.get("omission_impact", "")).strip()
            if not raw.get("include") or raw.get("fact_role") != "core" or not quote or quote not in source_text:
                continue
            point = self._matching_point(supports, key_points)
            if point is None or not impact or not str(raw.get("claim", "")).strip():
                continue
            paragraph, char_start = self._source_location(source_text, quote)
            facts.append(ArticleFact(
                claim=str(raw["claim"]).strip(), subject=str(raw.get("subject", "")),
                predicate=str(raw.get("predicate", "")), value=str(raw.get("value", "")),
                unit=str(raw.get("unit", "")), origin="source", verification="pending",
                qualifiers={"supports_point": point, "fact_role": "core", "omission_impact": impact},
                evidence=[Evidence(
                    source_kind="source_article", title=f"原文第 {paragraph} 段", quote=quote,
                    authority=0.7, metadata={"paragraph": paragraph, "char_start": char_start,
                                             "char_end": char_start + len(quote)},
                )],
            ))
        return thesis, key_points, self._deduplicate(facts)

    async def _screen_evidence(
        self, evidence: Sequence[Evidence], thesis: str,
        key_points: Sequence[str], core_facts: Sequence[ArticleFact],
    ) -> tuple[list[ArticleFact], list[str]]:
        if not evidence:
            return [], []
        by_id = {str(item.evidence_id): item for item in evidence if item.quote.strip()}
        result = await self._model.structured(
            system=(
                "审核检索证据是否可作为文章补充事实。事实筛选规范如下：\n" + self._fact_skill + "\n"
                "Tavily 只可丰富已有核心事实；公司向量检索只可提供公司自身情况用于说明。"
                "相似度不代表核实。每条候选必须指出key_point和删除影响；逐字使用证据原文作为claim，不得自行推断。"
                "如果冲突维度相同且不能用时间、地域、对象或口径解释，标conflict并排除。"
                "只输出传入的真实 evidence_id。skill_md 永远 unverified，数据库检索失败不能改写其状态。"
            ),
            prompt=json.dumps({
                "thesis": thesis, "key_points": list(key_points),
                "core_facts": [{"fact_id": str(f.fact_id), "claim": f.claim,
                                "supports_point": f.qualifiers.get("supports_point", "")} for f in core_facts],
                "candidates": [item.model_dump(mode="json") for item in evidence],
            }, ensure_ascii=False),
            schema={
                "type": "object", "properties": {"selections": {"type": "array", "items": {
                    "type": "object", "properties": {
                        "evidence_id": {"type": "string"}, "include": {"type": "boolean"},
                        "supports_point": {"type": "string"}, "omission_impact": {"type": "string"},
                        "fact_role": {"enum": ["web_enrichment", "company_context"]},
                        "conflict": {"type": "boolean"}, "conflict_reason": {"type": "string"},
                        "conflicts_with_fact_ids": {"type": "array", "items": {"type": "string"}},
                    }, "required": ["evidence_id", "include", "supports_point", "omission_impact",
                                  "fact_role", "conflict"],
                }}}, "required": ["selections"],
            },
        )
        accepted: list[ArticleFact] = []
        rejected: list[str] = []
        fact_by_id = {str(f.fact_id): f for f in core_facts}
        conflicts = 0
        for selection in result["selections"]:
            evidence_id = str(selection.get("evidence_id", ""))
            item = by_id.get(evidence_id)
            point = self._matching_point(str(selection.get("supports_point", "")), key_points)
            role = str(selection.get("fact_role", ""))
            valid_role = (item is not None and (
                (item.source_kind == "web" and role == "web_enrichment")
                or (item.source_kind in {"database", "skill_md", "company_document"} and role == "company_context")
            ))
            if selection.get("conflict"):
                conflicts += 1
                reason = str(selection.get("conflict_reason", "检索来源存在不一致"))[:500]
                for fact_id in selection.get("conflicts_with_fact_ids", []):
                    core = fact_by_id.get(str(fact_id))
                    if core is not None and item is not None:
                        core.verification = "conflicted"
                        core.qualifiers["conflict_reason"] = reason
                        known = {source.evidence_id for source in core.evidence}
                        if item.evidence_id not in known:
                            item.metadata["conflict_with_core"] = True
                            core.evidence.append(item)
            impact = str(selection.get("omission_impact", "")).strip()
            if (item is None or not selection.get("include") or selection.get("conflict")
                    or not valid_role or point is None or not impact):
                rejected.append(evidence_id)
                continue
            accepted.append(ArticleFact(
                claim=item.quote.strip(), origin=("web" if item.source_kind == "web" else
                    "history" if item.source_kind == "company_document" else "company"),
                verification=("verified" if item.source_kind == "database" else "unverified"),
                qualifiers={"supports_point": point, "fact_role": role, "omission_impact": impact},
                evidence=[item],
            ))
        await emit("research.evidence_screened", candidate_count=len(evidence), accepted_count=len(accepted),
                   rejected_count=len(rejected), conflict_count=conflicts)
        return self._deduplicate(accepted), rejected

    @staticmethod
    def _matching_point(candidate: str, key_points: Sequence[str]) -> str | None:
        normalized = " ".join(candidate.casefold().split())
        for point in key_points:
            if normalized == " ".join(point.casefold().split()):
                return point
        return None

    @staticmethod
    def _source_location(source_text: str, quote: str) -> tuple[int, int]:
        char_start = source_text.find(quote)
        if char_start < 0:
            return 1, 0
        paragraph = 1
        for match in re.finditer(r"[^\r\n]+", source_text):
            if not match.group(0).strip():
                continue
            if match.start() <= char_start < match.end():
                return paragraph, char_start
            paragraph += 1
        return max(1, paragraph - 1), char_start

    @staticmethod
    def _research_query(thesis: str, key_points: Sequence[str], facts: Sequence[ArticleFact]) -> str:
        claims = "；".join(f.claim for f in facts[:12])
        return f"文章主旨：{thesis}\n表达重点：{'；'.join(key_points)}\n需要补充的核心事实：{claims}"[:6000]

    @staticmethod
    def _deduplicate(facts: Sequence[ArticleFact]) -> list[ArticleFact]:
        chosen: dict[str, ArticleFact] = {}
        for fact in facts:
            key = fact.semantic_key() if fact.subject and fact.predicate else fact.claim.strip().casefold()
            current = chosen.get(key)
            if current is None:
                chosen[key] = fact
                continue
            known = {e.evidence_id for e in current.evidence}
            current.evidence.extend(e for e in fact.evidence if e.evidence_id not in known)
        return list(chosen.values())
