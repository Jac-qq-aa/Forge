from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from forge.application.ports import ArticleStore, AuthorizedDatabase, JobStore
from forge.domain.models import ArticleFact, Evidence, Identity
from forge.infrastructure.telemetry import PostgresTraceRecorder, emit, trace_context


class ConversationCommands:
    def __init__(
        self,
        database: AuthorizedDatabase,
        store: ArticleStore,
        jobs: JobStore,
    ) -> None:
        self._database = database
        self._store = store
        self._jobs = jobs

    async def create_conversation(self, identity: Identity) -> UUID:
        return await self._store.create_conversation(identity)

    async def submit_message(
        self,
        *,
        conversation_id: UUID,
        identity: Identity,
        content: str,
        source_text: str = "",
        platform: str = "",
        idempotency_key: UUID | None = None,
        requested_operation: str | None = None,
        trace_id: UUID | None = None,
    ) -> UUID:
        job_id = idempotency_key or uuid4()
        async with self._database.authorized(identity) as connection:
            article = await connection.fetchrow(
                "SELECT * FROM articles WHERE conversation_id=$1", conversation_id
            )
            created_article = False
            if source_text:
                if article is not None and article["source_text"].strip() != source_text.strip():
                    raise ValueError("一段对话只能处理一篇文章，请新建对话")
                if article is None:
                    created_article = True
                    article = await connection.fetchrow(
                        """
                        INSERT INTO articles (conversation_id, source_text, platform)
                        VALUES ($1,$2,$3) RETURNING *
                        """,
                        conversation_id, source_text, platform,
                    )
            facts: list[dict[str, Any]] = []
            outline: list[dict[str, Any]] = []
            draft = ""
            draft_paragraphs: list[dict[str, Any]] = []
            if article:
                fact_rows = [dict(row) for row in await connection.fetch(
                    "SELECT * FROM article_facts WHERE article_id=$1 AND is_current", article["id"]
                )]
                for row in fact_rows:
                    evidence = await connection.fetch("SELECT * FROM fact_evidence WHERE article_fact_row_id=$1", row["row_id"])
                    row["evidence"] = [{**dict(e), "evidence_id": str(e["id"])} for e in evidence]
                    row["evidence"] = [Evidence.model_validate(e).model_dump(mode="json") for e in row["evidence"]]
                facts = [self._fact_payload(row) for row in fact_rows]
                outline_rows = [dict(row) for row in await connection.fetch(
                    "SELECT * FROM outline_sections WHERE article_id=$1 AND outline_version=$2 ORDER BY position",
                    article["id"], article["outline_version"],
                )]
                outline = [self._outline_payload(row) for row in outline_rows]
                if article["current_draft_version"]:
                    draft_row = await connection.fetchrow(
                        "SELECT content, paragraphs FROM draft_versions WHERE article_id=$1 AND version=$2",
                        article["id"], article["current_draft_version"],
                    )
                    draft = draft_row["content"] if draft_row else ""
                    draft_paragraphs = list(draft_row["paragraphs"] or []) if draft_row else []
        message_id = await self._store.append_message(
            conversation_id, identity, "user", content
        )
        if message_id is None:
            raise LookupError("对话不存在")
        operation = requested_operation or ("generate" if created_article else "message")
        payload = {
            "conversation_id": str(conversation_id),
            "article_id": str(article["id"]) if article else "",
            "identity": identity.model_dump(mode="json"),
            "user_message": content,
            "requested_operation": operation,
            "source_text": source_text or (article["source_text"] if article else ""),
            "direction": article["direction"] if article else "沿用原文主题与方向",
            "article_thesis": article["thesis"] if article else "",
            "key_points": article["key_points"] if article else [],
            "platform": platform or (article["platform"] if article else ""),
            "facts": facts,
            "fact_version": article["fact_version"] if article else 0,
            "initial_fact_version": article["fact_version"] if article else 0,
            "outline": outline,
            "outline_version": article["outline_version"] if article else 0,
            "draft": draft,
            "draft_paragraphs": draft_paragraphs,
            "draft_history": [],
            "review_history": [],
            "critic_failures": 0,
            "issue_history": [],
            "system_decisions": [],
            "approval": {},
            "facts_changed": False,
            "outline_changed": False,
            "draft_changed": False,
        }
        if requested_operation == "modify":
            payload["baseline_facts"] = facts
        await self._jobs.enqueue(
            job_id=job_id,
            conversation_id=conversation_id,
            kind="start",
            payload=payload,
            stage="revising" if operation == "modify" else "researching",
            actor_id=identity.user_id,
            trace_id=trace_id,
        )
        return job_id

    async def revise_from_facts(
        self, *, conversation_id: UUID, identity: Identity
    ) -> UUID:
        workspace = await self._store.workspace(conversation_id, identity)
        if not workspace.get("draft"):
            raise ValueError("当前还没有可修订的文章")
        return await self.submit_message(
            conversation_id=conversation_id,
            identity=identity,
            content="按当前事实清单和已确认方向修订文章",
            requested_operation="modify",
        )

    async def finalize(self, *, conversation_id: UUID, identity: Identity) -> UUID:
        indexed = await self._store.finalize(conversation_id, identity)
        job_id = uuid4()
        await self._jobs.enqueue(
            job_id=job_id,
            conversation_id=conversation_id,
            kind="index_document",
            payload={
                "conversation_id": str(conversation_id),
                "identity": identity.model_dump(mode="json"),
                **indexed,
            },
            actor_id=identity.user_id,
        )
        return job_id

    async def edit_fact(
        self,
        *,
        conversation_id: UUID,
        identity: Identity,
        operation: str,
        fact: ArticleFact | None,
        fact_id: UUID | None,
        expected_version: int,
    ) -> dict[str, Any]:
        result = await self._store.apply_fact_change(
            conversation_id, identity, operation, fact, fact_id, expected_version
        )
        with trace_context(recorder=PostgresTraceRecorder(self._database), conversation_id=str(conversation_id),
                           identity=identity.model_dump(mode="json")):
            await emit("user.fact_changed", action=operation, fact_id=str(fact_id or (fact.fact_id if fact else "")),
                       expected_version=expected_version, fact_version=result["fact_version"], decided_by=str(identity.user_id))
        if not result["has_draft"]:
            result["job_id"] = str(await self.submit_message(
                conversation_id=conversation_id, identity=identity,
                content="按当前事实重新规划并生成文章", requested_operation="modify"))
        return result

    async def resume_pause(
        self, *, conversation_id: UUID, job_id: UUID, identity: Identity,
        decision: dict[str, Any], expected_fact_version: int,
    ) -> None:
        async with self._database.authorized(identity) as connection:
            row = await connection.fetchrow(
                """SELECT a.fact_version FROM articles a JOIN conversations c ON c.id=a.conversation_id
                   WHERE c.id=$1 AND c.owner_id=$2""", conversation_id, identity.user_id)
        if row is None:
            raise LookupError("对话不存在")
        if row["fact_version"] != expected_fact_version:
            raise RuntimeError("事实清单版本已变化，请刷新后重新裁决")
        await self._jobs.resume_waiting(
            job_id=job_id, identity=identity,
            decision={"conversation_id": str(conversation_id), "decision": decision})

    @staticmethod
    def _fact_payload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "fact_id": str(row.get("id") or row.get("fact_id")),
            "version": row["version"],
            "claim": row["claim"],
            "subject": row.get("subject", ""),
            "predicate": row.get("predicate", ""),
            "value": row.get("value", ""),
            "unit": row.get("unit", ""),
            "qualifiers": row.get("qualifiers") or {},
            "origin": row["origin"],
            "verification": row["verification"],
            "marked": row["marked"],
            "mark_reasons": list(row.get("mark_reasons") or []),
            "excluded": row["excluded"],
            "required_for_article": row.get("required_for_article", False),
            "decision_id": str(row["decision_id"]) if row.get("decision_id") else None,
            "evidence": row.get("evidence", []),
        }

    @staticmethod
    def _outline_payload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "section_id": str(row["section_id"]),
            "order": row.get("position", row.get("order")),
            "title": row["title"],
            "purpose": row["purpose"],
            "main_points": row["main_points"],
            "fact_ids": [str(item) for item in row.get("fact_ids", [])],
            "paragraphs": row.get("paragraphs", []),
        }
