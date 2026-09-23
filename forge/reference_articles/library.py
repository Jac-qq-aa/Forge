"""ReferenceArticleLibrary 深模块：隐藏分析、存储、索引、融合与提示压缩。"""

from __future__ import annotations

from collections import Counter
from typing import Any

from forge.reference_articles.analyzer import generic_copy_phrases
from forge.reference_articles.models import ReferenceExample, ReferencePack, ReferenceRequest
from forge.reference_articles.storage import ReferenceArticleRepository
from forge.reference_articles.vector_index import ReferenceVectorIndex


class ReferenceArticleLibrary:
    def __init__(self, repository: Any = None, vector_index: Any = None) -> None:
        self.repository = repository or ReferenceArticleRepository()
        self.vector_index = vector_index or ReferenceVectorIndex()

    async def select_reference_pack(self, request: ReferenceRequest) -> ReferencePack:
        style_query = (
            f"平台={request.target_platform};内容类型={request.content_type};"
            f"文章原型={request.desired_archetype or '自动'};要求结构清楚、表达自然"
        )
        ranked = await self.vector_index.search(
            topic_query=request.query_text(),
            style_query=style_query,
            platform=request.target_platform,
            archetype=request.desired_archetype,
            limit=request.limit,
        )
        rows = await self.repository.get_many([row_id for row_id, _ in ranked])
        score_by_id = dict(ranked)
        if not rows:
            return ReferencePack.empty()
        archetype = request.desired_archetype or Counter(str(row["archetype"]) for row in rows).most_common(1)[0][0]
        matching = [row for row in rows if str(row["archetype"]) == archetype] or rows
        roles = self._consensus_roles(matching)
        guidance = self._style_guidance(matching)
        examples = tuple(
            ReferenceExample(
                article_id=str(row["article_id"]), title=str(row["title"]),
                archetype=str(row["archetype"]), composition_type=str(row["composition_type"]),
                score=round(score_by_id.get(str(row["id"]), 0.0), 4), structure_roles=tuple(
                    str(section.get("role") or "") for section in self._json_value(row["section_blueprint"])
                ),
            ) for row in matching[:request.limit]
        )
        return ReferencePack(archetype, roles, guidance, generic_copy_phrases(), examples)

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, str):
            import json
            return json.loads(value)
        return value or []

    def _consensus_roles(self, rows: list[dict[str, Any]]) -> tuple[str, ...]:
        sequences = [
            tuple(str(item.get("role") or "") for item in self._json_value(row["section_blueprint"]))
            for row in rows
        ]
        return Counter(sequences).most_common(1)[0][0]

    def _style_guidance(self, rows: list[dict[str, Any]]) -> tuple[str, ...]:
        profiles = [self._json_value(row["style_profile"]) for row in rows]
        average_sentence = sum(float(item.get("average_sentence_chars", 0)) for item in profiles) / len(profiles)
        average_paragraph = sum(float(item.get("average_paragraph_chars", 0)) for item in profiles) / len(profiles)
        tone = Counter(str(item.get("tone") or "专业、克制") for item in profiles).most_common(1)[0][0]
        question_ratio = sum(float(item.get("question_ratio", 0)) for item in profiles) / len(profiles)
        return (
            f"采用{tone}的语气，不模仿任何单篇文章的句子",
            f"句子平均约 {average_sentence:.0f} 字，长短句交替",
            f"自然段平均约 {average_paragraph:.0f} 字，避免整齐模板段",
            "可使用少量设问推进" if question_ratio >= 0.05 else "少用连续设问，优先直接解释",
        )


_library: ReferenceArticleLibrary | None = None


def get_reference_article_library() -> ReferenceArticleLibrary:
    global _library
    if _library is None:
        _library = ReferenceArticleLibrary()
    return _library
