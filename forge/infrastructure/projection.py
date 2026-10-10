from __future__ import annotations

import hashlib

from typing import Any
from uuid import UUID

from forge.domain.models import ArticleFact, Identity, OutlineSection
from forge.infrastructure.postgres import Database


_STAGE_MAP = {
    "routing": "researching",
    "planning_outline": "planning",
    "generating": "generating",
    "critic": "critic",
    "revising": "revising",
    "ready": "ready",
    "answered": "idle",
    "awaiting_input": "idle",
    "needs_attention": "needs_attention",
}


class PostgresProgressReporter:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def report(
        self,
        *,
        conversation_id: UUID,
        identity: Identity,
        stage: str,
        code: str,
        message: str,
    ) -> None:
        async with self._database.authorized(identity) as connection:
            updated = await connection.fetchval(
                """
                UPDATE conversations SET stage=$2::article_stage, updated_at=now()
                WHERE id=$1 AND owner_id=$3 RETURNING id
                """,
                conversation_id,
                stage,
                identity.user_id,
            )
            if updated is None:
                raise LookupError("对话不存在或无权更新进度")
            await connection.execute(
                """
                INSERT INTO workflow_events (conversation_id, event_type, payload)
                VALUES ($1,'progress',$2::jsonb)
                """,
                conversation_id,
                {"stage": stage, "code": code, "message": message},
            )


class WorkflowProjection:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def project(self, result: dict[str, Any]) -> None:
        identity = Identity.model_validate(result["identity"])
        conversation_id = UUID(result["conversation_id"])
        article_id = UUID(result["article_id"]) if result.get("article_id") else None
        async with self._database.authorized(identity) as connection:
            if result.get("job_id") and result.get("projection_run_id"):
                inserted = await connection.fetchval(
                    "INSERT INTO workflow_projection_runs(job_id,run_id) VALUES ($1,$2) ON CONFLICT DO NOTHING RETURNING job_id",
                    UUID(result["job_id"]), str(result["projection_run_id"]),
                )
                if inserted is None:
                    return
            elif result.get("job_id"):
                inserted = await connection.fetchval(
                    "INSERT INTO workflow_projections(job_id) VALUES ($1) ON CONFLICT DO NOTHING RETURNING job_id",
                    UUID(result["job_id"]),
                )
                if inserted is None:
                    return
            stage = _STAGE_MAP.get(result.get("stage", ""), "failed" if result.get("error") else "idle")
            pause_kind = (result.get("pause_request") or {}).get("kind")
            if pause_kind in {"facts", "outline"}:
                stage = "awaiting_approval"
            elif pause_kind in {"plan_conflicts", "critic_conflicts"}:
                stage = "awaiting_decision"
            await connection.execute(
                "UPDATE conversations SET stage=$2, updated_at=now() WHERE id=$1",
                conversation_id, stage,
            )
            if article_id is not None:
                version = await connection.fetchval("SELECT fact_version FROM articles WHERE id=$1 FOR UPDATE", article_id)
                baseline = result.get("initial_fact_version")
                next_version = int(result.get("fact_version", 0))
                if (baseline is not None and version != baseline and version != next_version
                        and version != next_version - 1):
                    raise RuntimeError("任务执行期间事实版本发生变化，已阻止覆盖用户修改")
                await self._facts(connection, article_id, result)
                await self._outline(connection, article_id, result)
                await self._draft(connection, article_id, result)
                await connection.execute(
                    """
                    UPDATE articles SET fact_version=$2, outline_version=$3,
                        thesis=CASE WHEN $4<>'' THEN $4 ELSE thesis END,
                        key_points=CASE WHEN cardinality($5::text[]) > 0 THEN $5 ELSE key_points END,
                        updated_at=now()
                    WHERE id=$1
                    """,
                    article_id,
                    int(result.get("fact_version", 0)),
                    int(result.get("outline_version", 0)),
                    str(result.get("article_thesis", "")),
                    list(result.get("key_points", [])),
                )
                if result.get("outline_changed") or result.get("draft_changed"):
                    await connection.execute(
                        "UPDATE articles SET plan_dirty=false WHERE id=$1", article_id
                    )
                await self._decisions(connection, article_id, result)
                await self._review(connection, article_id, result)
                if result.get("approval", {}).get("action") == "approve":
                    approval = result["approval"]
                    await connection.execute(
                        """
                        INSERT INTO approval_snapshots (
                            article_id, fact_version, outline_version, actor_kind, rule, approved_by
                        ) VALUES ($1,$2,$3,$4,$5,$6)
                        ON CONFLICT (article_id, fact_version, outline_version) DO NOTHING
                        """,
                        article_id, approval["fact_version"], approval["outline_version"],
                        approval.get("actor_kind", "system"), approval["rule"],
                        identity.user_id if approval.get("actor_kind") == "user" else None,
                    )
                    await connection.execute(
                        """
                        UPDATE articles SET approved_fact_version=$2,
                            approved_outline_version=$3, approved_at=now()
                        WHERE id=$1
                        """,
                        article_id, approval["fact_version"], approval["outline_version"],
                    )
            if result.get("answer"):
                await connection.execute(
                    "INSERT INTO messages (conversation_id, role, content) VALUES ($1,'assistant',$2)",
                    conversation_id, result["answer"],
                )
            if result.get("warnings"):
                await connection.execute(
                    """
                    INSERT INTO workflow_events (conversation_id, event_type, payload)
                    VALUES ($1,'degraded',$2::jsonb)
                    """,
                    conversation_id,
                    {"warnings": result["warnings"]},
                )

    async def _facts(self, connection: Any, article_id: UUID, result: dict[str, Any]) -> None:
        if not result.get("facts_changed"):
            return
        await connection.execute("UPDATE article_facts SET is_current=false WHERE article_id=$1", article_id)
        for raw in result["facts"]:
            fact = ArticleFact.model_validate(raw)
            row_id = await connection.fetchval(
                """
                INSERT INTO article_facts (
                    id, article_id, version, claim, subject, predicate, value, unit,
                    qualifiers, origin, verification, marked, mark_reasons, excluded, decision_id,
                    required_for_article
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12,$13,$14,$15,$16)
                ON CONFLICT (article_id, id, version) DO UPDATE SET
                    claim=excluded.claim, verification=excluded.verification,
                    marked=excluded.marked, mark_reasons=excluded.mark_reasons,
                    excluded=excluded.excluded, decision_id=excluded.decision_id,
                    required_for_article=excluded.required_for_article, is_current=true
                RETURNING row_id
                """,
                fact.fact_id, article_id, fact.version, fact.claim, fact.subject,
                fact.predicate, fact.value, fact.unit,
                fact.qualifiers, fact.origin,
                fact.verification, fact.marked, list(fact.mark_reasons), fact.excluded,
                fact.decision_id, fact.required_for_article,
            )
            await connection.execute("DELETE FROM fact_evidence WHERE article_fact_row_id=$1", row_id)
            for evidence in fact.evidence:
                await connection.execute(
                    """
                    INSERT INTO fact_evidence (
                        id, article_fact_row_id, source_kind, title, url, quote,
                        document_id, document_version, authority, metadata, retrieved_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11)
                    """,
                    evidence.evidence_id, row_id, evidence.source_kind, evidence.title,
                    evidence.url, evidence.quote, evidence.document_id,
                    evidence.document_version, evidence.authority,
                    evidence.metadata, evidence.retrieved_at,
                )

    async def _outline(self, connection: Any, article_id: UUID, result: dict[str, Any]) -> None:
        if not result.get("outline_changed") or not result.get("outline") or not result.get("outline_version"):
            return
        version = int(result["outline_version"])
        await connection.execute(
            "DELETE FROM outline_sections WHERE article_id=$1 AND outline_version=$2",
            article_id, version,
        )
        for raw in result["outline"]:
            section = OutlineSection.model_validate(raw)
            await connection.execute(
                """
                INSERT INTO outline_sections (
                    article_id, outline_version, section_id, position, title, purpose, main_points, fact_ids, paragraphs
                ) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9::jsonb)
                """,
                article_id, version, section.section_id, section.order, section.title,
                section.purpose, section.main_points, section.fact_ids,
                [item.model_dump(mode="json") for item in section.paragraphs],
            )

    async def _draft(self, connection: Any, article_id: UUID, result: dict[str, Any]) -> None:
        snapshots = result.get("draft_history", [])
        if result.get("draft_changed") and not snapshots:
            raise ValueError("草稿缺少可复盘的段落快照")
        current = int(await connection.fetchval(
            "SELECT current_draft_version FROM articles WHERE id=$1 FOR UPDATE", article_id
        ))
        previous_current = current
        versions: dict[str, int] = {}
        for snapshot in snapshots:
            snapshot_id = UUID(snapshot["snapshot_id"])
            existing = await connection.fetchval(
                "SELECT version FROM draft_versions WHERE article_id=$1 AND snapshot_id=$2",
                article_id, snapshot_id)
            if existing is not None:
                versions[str(snapshot_id)] = int(existing)
                continue
            paragraphs = snapshot["paragraphs"]
            if not paragraphs:
                raise ValueError("草稿快照没有段落")
            current += 1
            await connection.execute(
                """INSERT INTO draft_versions (article_id,version,content,paragraphs,snapshot_id)
                   VALUES ($1,$2,$3,$4::jsonb,$5)""",
                article_id, current, "\n\n".join(item["content"] for item in paragraphs),
                paragraphs, snapshot_id)
            versions[str(snapshot_id)] = current
            previous = snapshot.get("previous_snapshot_id")
            previous_version = versions.get(str(previous)) if previous else (previous_current or None)
            for paragraph in paragraphs:
                paragraph_id = str(paragraph["paragraph_id"])
                await connection.execute(
                    """INSERT INTO paragraph_provenance
                       (article_id,draft_version,paragraph_id,section_id,fact_refs,issue_ids,
                        previous_draft_version,previous_content_fingerprint,content_fingerprint,step)
                       VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10)""",
                    article_id, current, UUID(paragraph_id), UUID(paragraph["section_id"]),
                    snapshot["fact_refs"].get(paragraph_id, []),
                    [UUID(item) for item in snapshot["issue_ids"].get(paragraph_id, [])],
                    previous_version, snapshot["prior_hashes"].get(paragraph_id),
                    hashlib.sha256(paragraph["content"].encode("utf-8")).hexdigest(), snapshot["step"])
        if snapshots:
            await connection.execute(
                "UPDATE articles SET current_draft_version=$2 WHERE id=$1", article_id,
                versions[snapshots[-1]["snapshot_id"]])

    async def _decisions(self, connection: Any, article_id: UUID, result: dict[str, Any]) -> None:
        for item in result.get("system_decisions", []):
            await connection.execute(
                """INSERT INTO system_decisions (id,article_id,fact_id,fact_version,action,rule,note,conflict)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb) ON CONFLICT (id) DO NOTHING""",
                UUID(item["decision_id"]), article_id, UUID(item["fact_id"]), item["fact_version"],
                item["action"], item["rule"], item["note"], item["conflict"],
            )
        for item in result.get("user_decisions", []):
            await connection.execute(
                """INSERT INTO user_decisions (id,article_id,fact_id,fact_version,action,issue_id,note,evidence_snapshot,decided_by)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9)
                   ON CONFLICT (article_id,fact_id,fact_version) DO NOTHING""",
                UUID(item["decision_id"]), article_id, UUID(item["fact_id"]), item["fact_version"], item["action"],
                UUID(item["conflict"]["issue_id"]) if item.get("conflict", {}).get("issue_id") else None,
                item.get("note", ""), item.get("conflict", {}).get("evidence", []),
                UUID(result["identity"]["user_id"]),
            )
    async def _review(self, connection: Any, article_id: UUID, result: dict[str, Any]) -> None:
        for review in result.get("review_history", []):
            snapshot_id = review.get("snapshot_id")
            if not snapshot_id:
                raise ValueError("Critic 记录缺少对应草稿快照")
            draft_version = await connection.fetchval(
                "SELECT version FROM draft_versions WHERE article_id=$1 AND snapshot_id=$2",
                article_id, UUID(snapshot_id))
            if draft_version is None:
                raise ValueError("Critic 对应草稿版本尚未持久化")
            await connection.execute(
                """INSERT INTO review_reports
                   (article_id,draft_version,failure_number,status,issues,stop_reason,fact_checks,review_id)
                   VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7::jsonb,$8)
                   ON CONFLICT (review_id) DO NOTHING""",
                article_id, draft_version, int(review["failure_number"]), review["status"],
                review["issues"], review["stop_reason"], review["fact_checks"],
                UUID(review["review_id"]))
