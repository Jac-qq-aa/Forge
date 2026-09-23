"""原文事实抽取深模块。

外部接口只有 ``SourceFactExtractor.extract(article)``。文档拆解、模型调用、
引用定位、数值校验、事实规范化、去重和冲突检测均隐藏在实现内部。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol


@dataclass(frozen=True)
class SourceChunk:
    chunk_id: str
    section_path: tuple[str, ...]
    paragraph_index: int
    char_start: int
    char_end: int
    text: str


@dataclass(frozen=True)
class SourceFactExtraction:
    source_text: str
    chunks: tuple[SourceChunk, ...]
    evidence: list[dict[str, Any]]
    facts: list[dict[str, Any]]
    warnings: list[str]


class CandidateAdapter(Protocol):
    async def extract(self, chunk: SourceChunk) -> list[dict[str, Any]]: ...


def _parse_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("candidate response does not contain JSON")
        candidate = candidate[start:end + 1]
    payload = json.loads(candidate)
    if not isinstance(payload, dict):
        raise ValueError("candidate response must be an object")
    return payload


class LLMCandidateAdapter:
    """生产适配器：逐块抽取原子事实，禁止使用块外知识。"""

    async def extract(self, chunk: SourceChunk) -> list[dict[str, Any]]:
        from forge.tools.llm_client import LLMClient

        prompt = f"""你是原文原子事实抽取器。只能抽取下方原文片段明确声称的客观事实。

章节路径：{' > '.join(chunk.section_path) or '正文'}
片段 ID：{chunk.chunk_id}
原文片段：
{chunk.text}

要求：
1. 一个对象关系只能形成一条事实；复合句必须拆成多条。
2. quote 必须逐字复制原文中的最小充分证据，不得改写。
3. 观点、建议、修辞、推测、示例计算和宣传口号不属于事实。
4. 数字、日期、比例、金额和单位必须与 quote 完全一致。
5. predicate 优先使用 founding_date、headquarters_location、customer_count、employee_count、
   annual_revenue、business_scope、qualification、policy_effective_date、legal_requirement。
6. normalized_value 只做规范化，不得创造原文不存在的信息。

只输出 JSON：
{{"facts":[{{
  "claim":"完整的原子事实陈述",
  "quote":"原文逐字引用",
  "subject":"主体",
  "predicate":"snake_case 关系",
  "object":"原文对象或值",
  "normalized_value":"规范化值",
  "operator":"=|>|>=|<|<=|约",
  "unit":"单位或空字符串",
  "qualifiers":{{"year":"2025"}},
  "fact_type":"company_fact|policy_fact|event_fact|general_fact"
}}]}}
"""
        response = await LLMClient().chat_with_retry(prompt)
        payload = _parse_json_object(response)
        facts = payload.get("facts", [])
        return [item for item in facts if isinstance(item, dict)] if isinstance(facts, list) else []


HEADING_RE = re.compile(
    r"^(?:#{1,6}\s+|[一二三四五六七八九十]+[、.]|\d+[、.．]\s*|第[一二三四五六七八九十\d]+[章节])"
)
PREDICATE_ALIASES = {
    "成立时间": "founding_date",
    "成立日期": "founding_date",
    "总部地点": "headquarters_location",
    "总部位置": "headquarters_location",
    "客户数量": "customer_count",
    "客户数": "customer_count",
    "员工数量": "employee_count",
    "员工数": "employee_count",
    "营业收入": "annual_revenue",
    "营收": "annual_revenue",
    "业务范围": "business_scope",
}
HIGH_VOLATILITY_PREDICATES = {
    "customer_count", "employee_count", "annual_revenue", "business_scope", "qualification",
}
TEMPORAL_TERMS = ("截至", "当前", "目前", "近期", "今年", "去年", "最新")
MAX_SOURCE_CHUNKS = 80


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _article_text(article: dict[str, Any]) -> str:
    return str(article.get("text") or article.get("content") or "").strip()


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in re.finditer(r".+?(?:[。！？!?；;](?=\s*|$)|\n+|$)", text, re.DOTALL):
        start, end = match.span()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            spans.append((start, end))
    return spans or ([(0, len(text))] if text else [])


def _split_long_span(text: str, start: int, end: int, limit: int) -> list[tuple[int, int]]:
    if end - start <= limit:
        return [(start, end)]
    return [(position, min(position + limit, end)) for position in range(start, end, limit)]


def _heading_level(value: str) -> int:
    markdown = re.match(r"^(#{1,6})\s+", value)
    if markdown:
        return len(markdown.group(1))
    if re.match(r"^(?:[一二三四五六七八九十]+[、.]|第[一二三四五六七八九十\d]+章)", value):
        return 1
    return 2


def _build_chunks(text: str, chunk_chars: int) -> tuple[SourceChunk, ...]:
    atomic_spans: list[tuple[int, int]] = []
    for start, end in _sentence_spans(text):
        atomic_spans.extend(_split_long_span(text, start, end, chunk_chars))
    chunks: list[SourceChunk] = []
    section_path: tuple[str, ...] = ()
    section_stack: list[str] = []
    current_start = -1
    current_end = -1
    paragraph_index = 0

    def flush() -> None:
        nonlocal current_start, current_end, paragraph_index
        if current_start < 0:
            return
        chunk_text = text[current_start:current_end]
        chunks.append(SourceChunk(
            chunk_id=f"SRC-C{len(chunks) + 1:03d}",
            section_path=section_path,
            paragraph_index=paragraph_index,
            char_start=current_start,
            char_end=current_end,
            text=chunk_text,
        ))
        paragraph_index += 1
        current_start = current_end = -1

    for start, end in atomic_spans:
        sentence = text[start:end].strip()
        is_heading = len(sentence) <= 100 and bool(HEADING_RE.match(sentence))
        if is_heading:
            flush()
            level = _heading_level(sentence)
            heading = sentence.strip("# \r\n")
            section_stack[level - 1:] = [heading]
            section_path = tuple(section_stack)
            continue
        if current_start < 0:
            current_start, current_end = start, end
        elif end - current_start <= chunk_chars:
            current_end = end
        else:
            flush()
            current_start, current_end = start, end
        if len(chunks) >= MAX_SOURCE_CHUNKS:
            break
    flush()
    return tuple(chunks[:MAX_SOURCE_CHUNKS])


def _normalize_predicate(value: str) -> str:
    normalized = re.sub(r"\s+", "", value or "").lower()
    if normalized in PREDICATE_ALIASES:
        return PREDICATE_ALIASES[normalized]
    normalized = re.sub(r"[^a-z0-9_\u3400-\u9fff]", "_", normalized).strip("_")
    return normalized or "general_fact"


def _arabic_numbers(text: str) -> set[str]:
    return set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?%?", text or ""))


def _year_from_text(text: str) -> str:
    match = re.search(r"(?:截至\s*)?((?:19|20)\d{2})年?", text)
    return match.group(1) if match else ""


def _volatility(predicate: str, quote: str) -> str:
    if predicate in HIGH_VOLATILITY_PREDICATES or any(term in quote for term in TEMPORAL_TERMS):
        return "high"
    if predicate in {"policy_effective_date", "legal_requirement"}:
        return "medium"
    return "low"


def _evidence_for_chunk(chunk: SourceChunk, article: dict[str, Any], index: int) -> dict[str, Any]:
    source_url = str(article.get("url") or article.get("source_url") or "source://article")
    source = str(article.get("source") or article.get("platform") or "原文章")
    return {
        "evidence_id": f"E{index:03d}",
        "task_id": "source",
        "source": source,
        "title": str(article.get("title") or "原文章"),
        "url": source_url,
        "published_at": str(article.get("published_at") or article.get("publish_date") or article.get("date") or ""),
        "retrieved_at": _utc_now(),
        "excerpt": chunk.text,
        "source_type": "source_article",
        "authority_score": 1.0,
        "relevance_score": 1.0,
        "content_hash": hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
        "chunk_id": chunk.chunk_id,
        "section_path": list(chunk.section_path),
        "paragraph_index": chunk.paragraph_index,
        "char_start": chunk.char_start,
        "char_end": chunk.char_end,
    }


class SourceFactExtractor:
    """将一篇原文转换为带精确引用的事实候选和分块证据。"""

    def __init__(
        self,
        *,
        candidate_adapter: CandidateAdapter | None = None,
        chunk_chars: int = 700,
        max_concurrency: int = 4,
    ) -> None:
        if chunk_chars < 20:
            raise ValueError("chunk_chars must be at least 20")
        self._adapter = candidate_adapter or LLMCandidateAdapter()
        self._chunk_chars = chunk_chars
        self._max_concurrency = max(1, max_concurrency)

    async def extract(self, article: dict[str, Any]) -> SourceFactExtraction:
        source_text = _article_text(article)
        if not source_text:
            raise ValueError("source article requires non-empty text or content")
        chunks = _build_chunks(source_text, self._chunk_chars)
        evidence = [
            _evidence_for_chunk(chunk, article, index)
            for index, chunk in enumerate(chunks, 1)
        ]
        evidence_by_chunk = {
            item["chunk_id"]: item for item in evidence
        }
        semaphore = asyncio.Semaphore(self._max_concurrency)
        warnings: list[str] = []

        async def extract_chunk(chunk: SourceChunk) -> tuple[SourceChunk, list[dict[str, Any]], str]:
            async with semaphore:
                try:
                    return chunk, await self._adapter.extract(chunk), ""
                except Exception as exc:
                    return chunk, [], str(exc)

        extracted = await asyncio.gather(*(extract_chunk(chunk) for chunk in chunks))
        facts: list[dict[str, Any]] = []
        for chunk, candidates, error in extracted:
            if error:
                warnings.append(f"{chunk.chunk_id}:adapter_error:{error}")
                continue
            evidence_item = evidence_by_chunk[chunk.chunk_id]
            for candidate_index, candidate in enumerate(candidates, 1):
                normalized, rejection = self._normalize_candidate(
                    candidate, chunk, evidence_item, article
                )
                if rejection:
                    warnings.append(f"{chunk.chunk_id}:{candidate_index}:{rejection}")
                elif normalized:
                    facts.append(normalized)

        facts = self._deduplicate_and_mark_conflicts(facts)
        for index, fact in enumerate(facts, 1):
            fact["fact_id"] = f"F{index:03d}"
        if len(chunks) >= MAX_SOURCE_CHUNKS and chunks[-1].char_end < len(source_text):
            warnings.append("source_chunk_limit_reached")
        return SourceFactExtraction(source_text, chunks, evidence, facts, warnings)

    def _normalize_candidate(
        self,
        candidate: dict[str, Any],
        chunk: SourceChunk,
        evidence: dict[str, Any],
        article: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        claim = str(candidate.get("claim") or "").strip()
        quote = str(candidate.get("quote") or "").strip()
        subject = str(candidate.get("subject") or "").strip()
        predicate = _normalize_predicate(str(candidate.get("predicate") or ""))
        object_value = str(candidate.get("object") or "").strip()
        if not claim or not quote or not subject or not object_value:
            return None, "missing_required_field"
        local_start = chunk.text.find(quote)
        if local_start < 0:
            return None, "quote_not_found"
        claim_numbers = _arabic_numbers(claim + " " + object_value)
        if not claim_numbers.issubset(_arabic_numbers(quote)):
            return None, "numeric_mismatch"
        char_start = chunk.char_start + local_start
        char_end = char_start + len(quote)
        qualifiers = candidate.get("qualifiers")
        qualifiers = {
            str(key): str(value)
            for key, value in (qualifiers.items() if isinstance(qualifiers, dict) else [])
            if str(value).strip()
        }
        year = _year_from_text(quote)
        if year and predicate in HIGH_VOLATILITY_PREDICATES:
            qualifiers.setdefault("year", year)
        volatility = _volatility(predicate, quote)
        as_of_date = year if volatility == "high" else ""
        source_info = {
            key: evidence[key]
            for key in ("evidence_id", "source", "title", "url", "published_at", "retrieved_at")
        }
        return {
            "fact_id": "",
            "task_id": "source",
            "section_id": chunk.section_path[-1] if chunk.section_path else "source",
            "claim": claim,
            "status": "source_claim",
            "confidence": 1.0,
            "evidence_ids": [evidence["evidence_id"]],
            "sources": [source_info],
            "review": {
                "decision": "pending", "comment": "", "priority": "normal", "reviewed_at": "",
            },
            "subject": subject,
            "predicate": predicate,
            "object": object_value,
            "normalized_value": str(candidate.get("normalized_value") or object_value).strip(),
            "operator": str(candidate.get("operator") or "=").strip(),
            "unit": str(candidate.get("unit") or "").strip(),
            "qualifiers": qualifiers,
            "fact_type": str(candidate.get("fact_type") or "general_fact").strip(),
            "volatility": volatility,
            "valid_from": "",
            "valid_to": "",
            "as_of_date": as_of_date,
            "region": str(candidate.get("region") or "").strip(),
            "source_status": "source_claim",
            "conflict_group": "",
            "evidence_spans": [{
                "evidence_id": evidence["evidence_id"],
                "chunk_id": chunk.chunk_id,
                "quote": quote,
                "char_start": char_start,
                "char_end": char_end,
            }],
            "article_id": str(article.get("id") or ""),
        }, ""

    @staticmethod
    def _deduplicate_and_mark_conflicts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduplicated: list[dict[str, Any]] = []
        exact: dict[tuple[Any, ...], dict[str, Any]] = {}
        for fact in facts:
            qualifiers = tuple(sorted(fact["qualifiers"].items()))
            exact_key = (
                fact["subject"], fact["predicate"], fact["normalized_value"],
                fact["operator"], fact["unit"], qualifiers,
            )
            existing = exact.get(exact_key)
            if existing:
                for evidence_id in fact["evidence_ids"]:
                    if evidence_id not in existing["evidence_ids"]:
                        existing["evidence_ids"].append(evidence_id)
                existing["sources"].extend(
                    source for source in fact["sources"] if source not in existing["sources"]
                )
                existing["evidence_spans"].extend(fact["evidence_spans"])
                continue
            exact[exact_key] = fact
            deduplicated.append(fact)

        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for fact in deduplicated:
            conflict_key = (
                fact["subject"], fact["predicate"], fact["unit"],
                tuple(sorted(fact["qualifiers"].items())),
            )
            groups.setdefault(conflict_key, []).append(fact)
        for key, grouped in groups.items():
            values = {fact["normalized_value"] for fact in grouped}
            if len(values) <= 1:
                continue
            digest = hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:12]
            conflict_group = f"CG-{digest}"
            for fact in grouped:
                fact["status"] = "disputed"
                fact["conflict_group"] = conflict_group
        return deduplicated
