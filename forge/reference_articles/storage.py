"""参考文章的 PostgreSQL 源数据仓库。"""

from __future__ import annotations

import json
from typing import Any

from forge.reference_articles.models import AnalyzedReferenceArticle
from forge.storage.pg_client import get_pg_pool


class ReferenceArticleRepository:
    async def upsert_fact_candidates(self, candidates: list[dict[str, Any]]) -> int:
        if not candidates:
            return 0
        pool = await get_pg_pool()
        if pool is None:
            raise RuntimeError("PostgreSQL unavailable for company fact candidates")
        inserted = 0
        async with pool.acquire() as conn:
            for item in candidates:
                result = await conn.execute(
                    """
                    INSERT INTO reference_company_fact_candidates (
                        article_id, claim, normalized_hash, is_temporal, review_status
                    ) VALUES ($1, $2, $3, $4, 'pending')
                    ON CONFLICT (normalized_hash) DO NOTHING
                    """,
                    item["article_id"], item["claim"], item["normalized_hash"], item["is_temporal"],
                )
                inserted += int(result == "INSERT 0 1")
        return inserted

    async def upsert(self, article: AnalyzedReferenceArticle) -> tuple[str, bool]:
        """保存分析结果；返回数据库 ID 和是否为新内容。"""
        pool = await get_pg_pool()
        if pool is None:
            raise RuntimeError("PostgreSQL unavailable for reference article import")
        async with pool.acquire() as conn:
            duplicate = await conn.fetchrow(
                """
                SELECT id, article_id FROM reference_articles
                WHERE content_hash = $1 OR dedup_fingerprint = $2
                LIMIT 1
                """,
                article.content_hash,
                article.dedup_fingerprint,
            )
            if duplicate and duplicate["article_id"] != article.article_id:
                return str(duplicate["id"]), False
            row = await conn.fetchrow(
                """
                INSERT INTO reference_articles (
                    article_id, title, content, source_url, platform, author,
                    publish_date, composition_type, archetype, topic_text,
                    style_text, style_profile, section_blueprint,
                    content_hash, dedup_fingerprint
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, NULLIF($7, '')::date, $8, $9,
                    $10, $11, $12::jsonb, $13::jsonb, $14, $15
                )
                ON CONFLICT (article_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    content = EXCLUDED.content,
                    source_url = EXCLUDED.source_url,
                    platform = EXCLUDED.platform,
                    author = EXCLUDED.author,
                    publish_date = EXCLUDED.publish_date,
                    composition_type = EXCLUDED.composition_type,
                    archetype = EXCLUDED.archetype,
                    topic_text = EXCLUDED.topic_text,
                    style_text = EXCLUDED.style_text,
                    style_profile = EXCLUDED.style_profile,
                    section_blueprint = EXCLUDED.section_blueprint,
                    content_hash = EXCLUDED.content_hash,
                    dedup_fingerprint = EXCLUDED.dedup_fingerprint,
                    updated_at = NOW()
                RETURNING id
                """,
                article.article_id,
                article.title,
                article.content,
                article.source_url,
                article.platform,
                article.author,
                article.publish_date,
                article.composition_type,
                article.archetype,
                article.topic_text,
                article.style_text,
                json.dumps(article.style_profile.to_dict(), ensure_ascii=False),
                json.dumps([item.to_dict() for item in article.sections], ensure_ascii=False),
                article.content_hash,
                article.dedup_fingerprint,
            )
        return str(row["id"]), True

    async def mark_indexed(self, row_id: str, vector_id: str) -> None:
        pool = await get_pg_pool()
        if pool is None:
            raise RuntimeError("PostgreSQL unavailable for reference article indexing")
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE reference_articles SET vector_id = $2, indexed_at = NOW() WHERE id = $1::uuid",
                row_id,
                vector_id,
            )

    async def get_many(self, row_ids: list[str]) -> list[dict[str, Any]]:
        if not row_ids:
            return []
        pool = await get_pg_pool()
        if pool is None:
            raise RuntimeError("PostgreSQL unavailable for reference article retrieval")
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, article_id, title, composition_type, archetype,
                       style_profile, section_blueprint
                FROM reference_articles
                WHERE id::text = ANY($1::text[]) AND is_active = TRUE
                """,
                row_ids,
            )
        by_id = {str(row["id"]): dict(row) for row in rows}
        return [by_id[row_id] for row_id in row_ids if row_id in by_id]

    async def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        pool = await get_pg_pool()
        if pool is None:
            raise RuntimeError("PostgreSQL unavailable for reference article indexing")
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, article_id, platform, composition_type, archetype,
                       topic_text, style_text, title
                FROM reference_articles
                WHERE is_active = TRUE AND vector_id IS NULL
                ORDER BY created_at ASC LIMIT $1
                """,
                limit,
            )
        return [dict(row) for row in rows]
