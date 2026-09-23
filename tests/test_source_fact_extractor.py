from __future__ import annotations

from typing import Any

import pytest

from forge.facts import SourceFactExtractor


class FakeCandidateAdapter:
    def __init__(self, candidates_by_marker: dict[str, list[dict[str, Any]]]) -> None:
        self.candidates_by_marker = candidates_by_marker
        self.seen_chunks: list[str] = []

    async def extract(self, chunk: Any) -> list[dict[str, Any]]:
        self.seen_chunks.append(chunk.text)
        results: list[dict[str, Any]] = []
        for marker, candidates in self.candidates_by_marker.items():
            if marker in chunk.text:
                results.extend(candidates)
        return results


def _candidate(
    claim: str,
    quote: str,
    *,
    subject: str = "锐博集团",
    predicate: str = "company_fact",
    object_value: str = "",
    normalized_value: str = "",
    unit: str = "",
) -> dict[str, Any]:
    return {
        "claim": claim,
        "quote": quote,
        "subject": subject,
        "predicate": predicate,
        "object": object_value or quote,
        "normalized_value": normalized_value or object_value or quote,
        "operator": "=",
        "unit": unit,
        "qualifiers": {},
        "fact_type": "company_fact",
    }


@pytest.mark.asyncio
async def test_reads_content_field_and_extracts_fact_from_end_of_long_article() -> None:
    tail = "锐博集团总部位于南宁市。"
    adapter = FakeCandidateAdapter({tail: [
        _candidate(
            tail, tail,
            predicate="headquarters_location",
            object_value="南宁市",
            normalized_value="南宁",
        )
    ]})
    extractor = SourceFactExtractor(candidate_adapter=adapter, chunk_chars=500)

    result = await extractor.extract({
        "id": "a1",
        "title": "集团介绍",
        "content": "这是背景介绍。" * 300 + tail,
        "source_url": "https://example.com/a1",
    })

    assert len(adapter.seen_chunks) > 1
    assert any(fact["predicate"] == "headquarters_location" for fact in result.facts)
    fact = next(fact for fact in result.facts if fact["predicate"] == "headquarters_location")
    assert fact["evidence_spans"][0]["quote"] == tail
    assert result.source_text[fact["evidence_spans"][0]["char_start"]:fact["evidence_spans"][0]["char_end"]] == tail


@pytest.mark.asyncio
async def test_preserves_three_atomic_facts_from_one_compound_sentence() -> None:
    sentence = "锐博集团成立于2011年，总部位于南宁，服务客户超过一万家。"
    adapter = FakeCandidateAdapter({sentence: [
        _candidate("锐博集团成立于2011年。", "锐博集团成立于2011年", predicate="founding_date", object_value="2011年", normalized_value="2011"),
        _candidate("锐博集团总部位于南宁。", "总部位于南宁", predicate="headquarters_location", object_value="南宁", normalized_value="南宁"),
        _candidate("锐博集团服务客户超过一万家。", "服务客户超过一万家", predicate="customer_count", object_value="超过一万家", normalized_value="10000", unit="家"),
    ]})

    result = await SourceFactExtractor(candidate_adapter=adapter).extract({
        "title": "介绍", "text": sentence, "url": "https://example.com"
    })

    assert {fact["predicate"] for fact in result.facts} == {
        "founding_date", "headquarters_location", "customer_count"
    }
    assert len({fact["fact_id"] for fact in result.facts}) == 3


@pytest.mark.asyncio
async def test_rejects_quote_not_present_in_source_and_numeric_mismatch() -> None:
    source = "锐博集团成立于2011年。"
    adapter = FakeCandidateAdapter({source: [
        _candidate("锐博集团成立于2012年。", "锐博集团成立于2012年", predicate="founding_date", object_value="2012年", normalized_value="2012"),
        _candidate("锐博集团成立于2012年。", source, predicate="founding_date", object_value="2012年", normalized_value="2012"),
    ]})

    result = await SourceFactExtractor(candidate_adapter=adapter).extract({
        "title": "介绍", "text": source, "url": "https://example.com"
    })

    assert result.facts == []
    assert any("quote_not_found" in warning for warning in result.warnings)
    assert any("numeric_mismatch" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_deduplicates_same_fact_and_marks_conflicting_values() -> None:
    text = "2024年锐博集团营收突破200亿元。2024年锐博集团营收为180亿元。"
    adapter = FakeCandidateAdapter({
        "突破200亿元": [_candidate(
            "2024年锐博集团营收突破200亿元。", "2024年锐博集团营收突破200亿元。",
            predicate="annual_revenue", object_value="200亿元", normalized_value="200", unit="亿元",
        )],
        "营收为180亿元": [_candidate(
            "2024年锐博集团营收为180亿元。", "2024年锐博集团营收为180亿元。",
            predicate="annual_revenue", object_value="180亿元", normalized_value="180", unit="亿元",
        )],
    })

    result = await SourceFactExtractor(candidate_adapter=adapter, chunk_chars=24).extract({
        "title": "营收", "text": text, "url": "https://example.com"
    })

    assert len(result.facts) == 2
    assert all(fact["status"] == "disputed" for fact in result.facts)
    assert len({fact["conflict_group"] for fact in result.facts}) == 1


@pytest.mark.asyncio
async def test_time_sensitive_fact_is_not_treated_as_permanent() -> None:
    sentence = "截至2025年，锐博集团服务客户超过一万家。"
    adapter = FakeCandidateAdapter({sentence: [
        _candidate(
            sentence, sentence, predicate="customer_count",
            object_value="超过一万家", normalized_value="10000", unit="家",
        )
    ]})

    result = await SourceFactExtractor(candidate_adapter=adapter).extract({
        "title": "客户", "text": sentence, "url": "https://example.com"
    })

    assert result.facts[0]["volatility"] == "high"
    assert result.facts[0]["as_of_date"] == "2025"
    assert result.facts[0]["status"] == "source_claim"


@pytest.mark.asyncio
async def test_chunks_keep_heading_hierarchy() -> None:
    adapter = FakeCandidateAdapter({})
    text = "一、集团情况\n背景介绍。\n1. 业务范围\n业务说明。"

    result = await SourceFactExtractor(candidate_adapter=adapter, chunk_chars=100).extract({
        "title": "介绍", "text": text, "url": "https://example.com"
    })

    assert result.chunks[0].section_path == ("一、集团情况",)
    assert result.chunks[1].section_path == ("一、集团情况", "1. 业务范围")
