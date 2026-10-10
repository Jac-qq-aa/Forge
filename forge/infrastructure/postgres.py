from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from uuid import UUID

import asyncpg

from forge.domain.models import ArticleFact, Evidence, Identity
from forge.infrastructure.telemetry import traced


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.9g}" for value in values) + "]"


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def open(self) -> None:
        async def configure(connection: asyncpg.Connection) -> None:
            encoder = lambda value: json.dumps(value, ensure_ascii=False, default=str)
            await connection.set_type_codec(
                "json", encoder=encoder, decoder=json.loads, schema="pg_catalog"
            )
            await connection.set_type_codec(
                "jsonb", encoder=encoder, decoder=json.loads, schema="pg_catalog"
            )

        self._pool = await asyncpg.create_pool(
            self._dsn, min_size=1, max_size=10, init=configure
        )

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("数据库连接池尚未启动")
        return self._pool

    @asynccontextmanager
    async def authorized(self, identity: Identity) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.user_id', $1, true), "
                    "set_config('app.tenant_id', $2, true), "
                    "set_config('app.space_ids', $3, true)",
                    str(identity.user_id),
                    str(identity.tenant_id or ""),
                    ",".join(str(item) for item in identity.knowledge_space_ids),
                )
                yield connection

    @traced("database.readonly")
    async def execute_readonly(
        self, sql: str, identity: Identity, *, timeout_ms: int = 5000
    ) -> list[dict[str, Any]]:
        async with self.authorized(identity) as connection:
            await connection.execute("SET LOCAL TRANSACTION READ ONLY")
            await connection.execute("SET LOCAL ROLE forge_query")
            await connection.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
            return [dict(row) for row in await connection.fetch(sql)]


class PgVectorSearch:
    def __init__(self, database: Database, embeddings: Any) -> None:
        self._database = database
        self._embeddings = embeddings

    async def semantic(
        self, query: str, *, identity: Identity, limit: int = 8, scope: str = "all"
    ) -> list[Evidence]:
        if scope not in {"all", "company"}:
            raise ValueError("向量检索范围必须为 all 或 company")
        vector = _vector_literal(await self._embeddings.embed(query))
        async with self._database.authorized(identity) as connection:
            rows = await connection.fetch(
                """
                SELECT c.id, d.id AS document_id, d.title, d.source_url,
                       d.version, c.content, c.position,
                       1 - (e.embedding <=> $1::vector) AS similarity
                FROM chunk_embeddings e
                JOIN document_chunks c ON c.id = e.chunk_id
                JOIN documents d ON d.id = c.document_id
                JOIN knowledge_spaces ks ON ks.id = d.space_id
                WHERE d.status = 'published'
                  AND e.embedding_model = $2
                  AND ($3 = 'all' OR (ks.scope='company' AND ks.tenant_id IS NOT DISTINCT FROM $4))
                ORDER BY e.embedding <=> $1::vector
                LIMIT $5
                """,
                vector,
                self._embeddings.model,
                scope,
                identity.tenant_id,
                min(max(limit, 1), 30),
            )
        return [
            Evidence(
                source_kind="company_document",
                title=row["title"],
                url=row["source_url"],
                quote=row["content"],
                document_id=row["document_id"],
                document_version=row["version"],
                authority=min(0.9, max(0.4, float(row["similarity"]))),
                metadata={
                    "storage": "postgresql_pgvector",
                    "table": "chunk_embeddings",
                    "document_table": "documents",
                    "chunk_table": "document_chunks",
                    "chunk_id": str(row["id"]),
                    "position": row["position"],
                    "similarity": float(row["similarity"]),
                },
            )
            for row in rows
        ]


class PostgresArticleStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_conversation(self, identity: Identity) -> UUID:
        async with self._database.authorized(identity) as connection:
            return await connection.fetchval(
                "INSERT INTO conversations (owner_id, tenant_id) VALUES ($1, $2) RETURNING id",
                identity.user_id,
                identity.tenant_id,
            )

    async def append_message(
        self, conversation_id: UUID, identity: Identity, role: str, content: str
    ) -> UUID | None:
        async with self._database.authorized(identity) as connection:
            return await connection.fetchval(
                """
                INSERT INTO messages (conversation_id, role, content)
                SELECT id, $3, $4 FROM conversations WHERE id = $1 AND owner_id = $2
                RETURNING id
                """,
                conversation_id,
                identity.user_id,
                role,
                content,
            )

    async def workspace(self, conversation_id: UUID, identity: Identity) -> dict[str, Any]:
        async with self._database.authorized(identity) as connection:
            row = await connection.fetchrow(
                """
                SELECT c.id, c.stage, a.id AS article_id, a.source_text, a.direction,
                       a.thesis AS article_thesis, a.key_points,
                       a.fact_version, a.outline_version, a.current_draft_version, a.plan_dirty
                FROM conversations c LEFT JOIN articles a ON a.conversation_id = c.id
                WHERE c.id = $1 AND c.owner_id = $2
                """,
                conversation_id,
                identity.user_id,
            )
            if row is None:
                raise LookupError("对话不存在")
            messages = await connection.fetch(
                "SELECT id, role, content, created_at FROM messages WHERE conversation_id=$1 ORDER BY created_at",
                conversation_id,
            )
            facts = await connection.fetch(
                """
                SELECT af.id, af.version, af.claim, af.subject, af.predicate, af.value,
                       af.unit, af.qualifiers, af.origin, af.verification, af.marked,
                       af.mark_reasons, af.excluded, af.decision_id, af.required_for_article,
                       COALESCE(
                           jsonb_agg(jsonb_build_object(
                               'evidence_id', fe.id, 'source_kind', fe.source_kind,
                               'title', fe.title, 'url', fe.url, 'quote', fe.quote,
                               'document_id', fe.document_id,
                               'document_version', fe.document_version,
                               'authority', fe.authority, 'metadata', fe.metadata,
                               'retrieved_at', fe.retrieved_at
                           )) FILTER (WHERE fe.id IS NOT NULL), '[]'::jsonb
                       ) AS evidence
                FROM article_facts af
                LEFT JOIN fact_evidence fe ON fe.article_fact_row_id=af.row_id
                WHERE af.article_id=$1 AND af.is_current
                GROUP BY af.row_id ORDER BY af.created_at
                """,
                row["article_id"],
            ) if row["article_id"] else []
            outline = await connection.fetch(
                """
                SELECT section_id, position, title, purpose, main_points, fact_ids, paragraphs
                FROM outline_sections WHERE article_id=$1 AND outline_version=$2 ORDER BY position
                """,
                row["article_id"],
                row["outline_version"],
            ) if row["article_id"] and row["outline_version"] else []
            decisions = await connection.fetch(
                "SELECT id,fact_id,fact_version,note,conflict FROM system_decisions WHERE article_id=$1 ORDER BY created_at",
                row["article_id"],
            ) if row["article_id"] else []
            user_decisions = await connection.fetch(
                "SELECT id,fact_id,fact_version,action,issue_id,note,evidence_snapshot,decided_by,decided_at FROM user_decisions WHERE article_id=$1 ORDER BY decided_at",
                row["article_id"],
            ) if row["article_id"] else []
            review = await connection.fetchrow(
                """
                SELECT draft_version, status, issues, fact_checks, stop_reason, failure_number, created_at
                FROM review_reports WHERE article_id=$1 ORDER BY created_at DESC LIMIT 1
                """,
                row["article_id"],
            ) if row["article_id"] else None
            draft = await connection.fetchrow(
                """
                SELECT version, content, paragraphs, created_at FROM draft_versions
                WHERE article_id=$1 AND version=$2
                """,
                row["article_id"], row["current_draft_version"],
            ) if row["article_id"] and row["current_draft_version"] else None
            provenance = await connection.fetch(
                """SELECT paragraph_id,section_id,draft_version,fact_refs,issue_ids,
                          previous_draft_version,previous_content_fingerprint,
                          content_fingerprint,step,created_at
                   FROM paragraph_provenance
                   WHERE article_id=$1 AND draft_version=$2 ORDER BY created_at,paragraph_id""",
                row["article_id"], row["current_draft_version"],
            ) if draft else []
            issue_reports = await connection.fetch(
                "SELECT issues FROM review_reports WHERE article_id=$1 ORDER BY created_at",
                row["article_id"],
            ) if provenance else []
            events = await connection.fetch(
                """
                SELECT sequence, event_type, payload, created_at
                FROM workflow_events WHERE conversation_id=$1
                ORDER BY sequence DESC LIMIT 20
                """,
                conversation_id,
            )
            waiting_job = await connection.fetchrow(
                """SELECT id,result FROM workflow_jobs WHERE conversation_id=$1 AND status='waiting'
                   ORDER BY updated_at DESC LIMIT 1""", conversation_id)
        issues_by_id = {str(issue["issue_id"]): issue
                        for report in issue_reports for issue in report["issues"]
                        if issue.get("issue_id")}
        provenance_rows = [{**dict(item), "issues": [issues_by_id[str(issue_id)]
                            for issue_id in item["issue_ids"] if str(issue_id) in issues_by_id]}
                           for item in provenance]
        return {
            **dict(row),
            "messages": [dict(item) for item in messages],
            "facts": [dict(item) for item in facts],
            "outline": [dict(item) for item in outline],
            "system_decisions": [dict(item) for item in decisions],
            "user_decisions": [dict(item) for item in user_decisions],
            "review": dict(review) if review else None,
            "draft": dict(draft) if draft else None,
            "paragraph_provenance": provenance_rows,
            "waiting_job_id": str(waiting_job["id"]) if waiting_job else None,
            "pause_request": ((waiting_job["result"] or {}).get("pause_request")
                              if waiting_job else None),
            "events": [dict(item) for item in reversed(events)],
        }

    async def apply_fact_change(
        self,
        conversation_id: UUID,
        identity: Identity,
        operation: str,
        fact: ArticleFact | None,
        fact_id: UUID | None,
        expected_version: int,
    ) -> dict[str, Any]:
        async with self._database.authorized(identity) as connection:
            await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 31))", str(conversation_id))
            if await connection.fetchval(
                """SELECT 1 FROM workflow_jobs WHERE conversation_id=$1
                   AND status IN ('pending','queued','dispatching','submission_unknown','submitted','running','waiting') LIMIT 1""", conversation_id
            ):
                raise RuntimeError("当前任务执行中，请等待完成后修改事实")
            article = await connection.fetchrow(
                """
                SELECT a.* FROM articles a JOIN conversations c ON c.id=a.conversation_id
                WHERE c.id=$1 AND c.owner_id=$2 FOR UPDATE
                """,
                conversation_id,
                identity.user_id,
            )
            if article is None:
                raise LookupError("文章不存在")
            if article["fact_version"] != expected_version:
                raise RuntimeError("事实清单版本冲突")
            next_version = expected_version + 1
            if operation == "delete":
                if fact_id is None:
                    raise ValueError("删除事实必须提供 fact_id")
                current = await connection.fetchrow(
                    "SELECT * FROM article_facts WHERE article_id=$1 AND id=$2 AND is_current FOR UPDATE",
                    article["id"],
                    fact_id,
                )
                if current is None:
                    raise LookupError("事实不存在")
                await connection.execute(
                    "UPDATE article_facts SET is_current=false WHERE row_id=$1", current["row_id"]
                )
                reasons = set(current["mark_reasons"] or []) | {"user_deleted"}
                deleted_row_id = await connection.fetchval(
                    """
                    INSERT INTO article_facts (
                        id, article_id, version, claim, subject, predicate, value, unit,
                        qualifiers, origin, verification, marked, mark_reasons, excluded, decision_id, required_for_article
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,true,$12,true,$13,$14)
                    RETURNING row_id
                    """,
                    current["id"], article["id"], current["version"] + 1,
                    current["claim"], current["subject"], current["predicate"],
                    current["value"], current["unit"], current["qualifiers"],
                    current["origin"], current["verification"], list(reasons), current["decision_id"],
                    current["required_for_article"],
                )
                await connection.execute(
                    """
                    INSERT INTO fact_evidence (
                        id, article_fact_row_id, source_kind, title, url, quote,
                        document_id, document_version, authority, metadata, retrieved_at
                    )
                    SELECT gen_random_uuid(), $2, source_kind, title, url, quote,
                           document_id, document_version, authority, metadata, retrieved_at
                    FROM fact_evidence WHERE article_fact_row_id=$1
                    """,
                    current["row_id"], deleted_row_id,
                )
            else:
                if fact is None:
                    raise ValueError("新增或编辑必须提供事实")
                if operation == "update" and fact_id:
                    current = await connection.fetchrow(
                        "SELECT row_id, version FROM article_facts WHERE article_id=$1 AND id=$2 AND is_current FOR UPDATE",
                        article["id"],
                        fact_id,
                    )
                    if current is None:
                        raise LookupError("事实不存在")
                    await connection.execute(
                        "UPDATE article_facts SET is_current=false WHERE row_id=$1", current["row_id"]
                    )
                    fact.fact_id = fact_id
                    fact.version = current["version"] + 1
                    fact.marked = True
                    fact.mark_reasons.add("user_edited")
                    fact.required_for_article = True
                elif operation == "create":
                    fact.origin = "user"
                    fact.marked = True
                    fact.mark_reasons.add("user_created")
                else:
                    raise ValueError("不支持的事实操作")
                fact.decision_id = None
                fact.verification = "unverified"
                await connection.execute(
                    """
                    INSERT INTO article_facts (
                        id, article_id, version, claim, subject, predicate, value, unit,
                        qualifiers, origin, verification, marked, mark_reasons, excluded, decision_id, required_for_article
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                    """,
                    fact.fact_id, article["id"], fact.version, fact.claim, fact.subject,
                    fact.predicate, fact.value, fact.unit, fact.qualifiers,
                    fact.origin, fact.verification, fact.marked, list(fact.mark_reasons),
                    fact.excluded, fact.decision_id, fact.required_for_article,
                )
            await connection.execute(
                "UPDATE articles SET fact_version=$2, plan_dirty=true, approved_at=NULL WHERE id=$1",
                article["id"],
                next_version,
            )
        return {
            "fact_version": next_version,
            "outline_invalidated": True,
            "has_draft": bool(article["current_draft_version"]),
        }

    async def finalize(self, conversation_id: UUID, identity: Identity) -> dict[str, Any]:
        async with self._database.authorized(identity) as connection:
            article = await connection.fetchrow(
                """
                SELECT a.id, a.current_draft_version, d.content
                FROM articles a
                JOIN conversations c ON c.id=a.conversation_id
                JOIN draft_versions d ON d.article_id=a.id AND d.version=a.current_draft_version
                WHERE c.id=$1 AND c.owner_id=$2 AND c.stage='ready'
                FOR UPDATE OF a
                """,
                conversation_id, identity.user_id,
            )
            if article is None:
                raise RuntimeError("只有通过 critic 的最新文章可以定稿")
            space_id = await connection.fetchval(
                "SELECT id FROM knowledge_spaces WHERE owner_id=$1 AND scope='personal' LIMIT 1",
                identity.user_id,
            )
            if space_id is None:
                space_id = await connection.fetchval(
                    "INSERT INTO knowledge_spaces (owner_id, scope, name) VALUES ($1,'personal','个人历史知识库') RETURNING id",
                    identity.user_id,
                )
            import hashlib
            content_hash = hashlib.sha256(article["content"].encode("utf-8")).hexdigest()
            document_id = await connection.fetchval(
                """
                INSERT INTO documents (space_id, external_id, version, title, content_hash, status, metadata)
                VALUES ($1,$2,$3,$4,$5,'published',$6::jsonb)
                ON CONFLICT (space_id, external_id, version) DO UPDATE
                SET title=excluded.title, content_hash=excluded.content_hash, status='published'
                RETURNING id
                """,
                space_id, f"article:{article['id']}", str(article["current_draft_version"]),
                "Forge 定稿", content_hash,
                {"article_id": str(article["id"]), "conversation_id": str(conversation_id)},
            )
            chunks: list[dict[str, str]] = []
            paragraphs = [item.strip() for item in article["content"].split("\n\n") if item.strip()]
            grouped: list[str] = []
            buffer = ""
            for paragraph in paragraphs:
                if buffer and len(buffer) + len(paragraph) + 2 > 3000:
                    grouped.append(buffer)
                    buffer = paragraph
                else:
                    buffer = f"{buffer}\n\n{paragraph}".strip()
            if buffer:
                grouped.append(buffer)
            for position, content in enumerate(grouped or [article["content"]]):
                chunk_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                chunk_id = await connection.fetchval(
                    """
                    INSERT INTO document_chunks (document_id, position, content, content_hash)
                    VALUES ($1,$2,$3,$4)
                    ON CONFLICT (document_id, position) DO UPDATE
                    SET content=excluded.content, content_hash=excluded.content_hash
                    RETURNING id
                    """,
                    document_id, position, content, chunk_hash,
                )
                chunks.append({"chunk_id": str(chunk_id), "content": content})
            await connection.execute(
                "UPDATE conversations SET stage='finalized', updated_at=now() WHERE id=$1",
                conversation_id,
            )
        return {"document_id": str(document_id), "chunks": chunks}

    async def save_embedding(
        self, *, chunk_id: UUID, vector: list[float], model: str, dimensions: int, identity: Identity
    ) -> None:
        async with self._database.authorized(identity) as connection:
            await connection.execute(
                """
                INSERT INTO chunk_embeddings (chunk_id, embedding_model, embedding_dimensions, embedding)
                VALUES ($1,$2,$3,$4::vector)
                ON CONFLICT (chunk_id, embedding_model) DO UPDATE
                SET embedding_dimensions=excluded.embedding_dimensions, embedding=excluded.embedding
                """,
                chunk_id, model, dimensions, _vector_literal(vector),
            )
