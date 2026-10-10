from __future__ import annotations

from uuid import uuid4

import pytest

from forge.domain.models import Identity
from forge.knowledge.research import EvidenceResearcher


class ExtractionModel:
    async def structured(self, *, system, prompt, schema):
        return {
            "facts": [
                {
                    "claim": "第二段包含关键事实",
                    "quote": "第二段包含关键事实",
                }
            ]
        }


class EmptySearch:
    async def web(self, query, *, limit=5):
        return []

    async def semantic(self, query, *, identity, limit=8):
        return []


class EmptyCompany:
    async def ask(self, question, *, identity):
        return []


@pytest.mark.asyncio
async def test_source_evidence_records_paragraph_and_character_range() -> None:
    researcher = EvidenceResearcher(ExtractionModel(), EmptySearch(), EmptyCompany())
    source = "第一段介绍背景。\n\n第二段包含关键事实。\n第三段收尾。"
    outcome = await researcher.collect(
        source_text=source,
        direction="沿用原文主题与方向",
        identity=Identity(user_id=uuid4()),
    )
    evidence = outcome.facts[0].evidence[0]
    assert evidence.title == "原文第 2 段"
    assert evidence.metadata["paragraph"] == 2
    assert evidence.metadata["char_start"] == source.index("第二段包含关键事实")
    assert evidence.metadata["char_end"] > evidence.metadata["char_start"]
