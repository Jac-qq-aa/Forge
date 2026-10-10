from __future__ import annotations

from typing import Any
import hashlib
from uuid import UUID

from forge.domain.models import Identity
from forge.infrastructure.postgres import Database


class TaskAuditor:
    """确定性检查持久化完整性；不调用模型，不修改文章或人工裁决。"""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def check(self, audit_job_id: UUID, payload: dict[str, Any], identity: Identity) -> dict[str, Any]:
        source_id = UUID(payload["source_job_id"])
        conversation_id = UUID(payload["conversation_id"])
        checks: list[dict[str, Any]] = []

        def record(code: str, expected: Any, actual: Any) -> None:
            checks.append({"code": code, "status": "pass" if expected == actual else "attention",
                           "expected": expected, "actual": actual})

        async with self._database.authorized(identity) as connection:
            async with connection.transaction():
                existing = await connection.fetchrow("SELECT status,checks FROM task_audit_reports WHERE audit_job_id=$1", audit_job_id)
                if existing:
                    return {"source_job_id": str(source_id), **dict(existing)}
                source = await connection.fetchrow(
                    """SELECT j.* FROM workflow_jobs j JOIN conversations c ON c.id=j.conversation_id
                       WHERE j.id=$1 AND c.id=$2 AND c.owner_id=$3""", source_id, conversation_id, identity.user_id)
                if source is None:
                    raise PermissionError("审计任务来源不存在或无权访问")
                same_execution = source["payload"].get("_execution_id") == payload["source_execution_id"]
                record("execution_current", True, same_execution)
                if same_execution:
                    record("terminal_status", payload["source_status"], source["status"])
                    record("task_completed", "completed", source["status"])
                    event_count = await connection.fetchval(
                        "SELECT count(*) FROM workflow_events WHERE job_id=$1 AND event_type=$2",
                        source_id, payload["source_status"])
                    record("terminal_event_saved", True, event_count > 0)
                    result = source["result"] or {}
                    if source["kind"] == "start" and source["status"] == "completed":
                        projected = await connection.fetchval(
                            "SELECT count(*) FROM workflow_projection_runs WHERE job_id=$1 AND run_id=$2",
                            source_id, payload["source_run_id"])
                        record("run_projected", 1, projected)
                        for snapshot in result.get("draft_history", []):
                            draft = await connection.fetchrow(
                                "SELECT article_id,version,paragraphs FROM draft_versions WHERE snapshot_id=$1",
                                UUID(snapshot["snapshot_id"]))
                            record("snapshot_saved:" + snapshot["snapshot_id"], True, draft is not None)
                            if draft:
                                count = await connection.fetchval(
                                    "SELECT count(*) FROM paragraph_provenance WHERE article_id=$1 AND draft_version=$2",
                                    draft["article_id"], draft["version"])
                                record("paragraph_provenance:" + snapshot["snapshot_id"], len(draft["paragraphs"]), count)
                                provenance = await connection.fetch(
                                    """SELECT paragraph_id,section_id,content_fingerprint,fact_refs,issue_ids
                                       FROM paragraph_provenance WHERE article_id=$1 AND draft_version=$2""",
                                    draft["article_id"], draft["version"])
                                saved = {str(item["paragraph_id"]): item for item in provenance}
                                links_match = True
                                for paragraph in snapshot["paragraphs"]:
                                    paragraph_id = paragraph["paragraph_id"]
                                    item = saved.get(paragraph_id)
                                    links_match = links_match and item is not None and (
                                        str(item["section_id"]) == paragraph["section_id"]
                                        and item["content_fingerprint"] == hashlib.sha256(paragraph["content"].encode("utf-8")).hexdigest()
                                        and item["fact_refs"] == snapshot["fact_refs"].get(paragraph_id, [])
                                        and [str(value) for value in item["issue_ids"]] == snapshot["issue_ids"].get(paragraph_id, []))
                                record("paragraph_links:" + snapshot["snapshot_id"], True, bool(links_match))
                        for review in result.get("review_history", []):
                            count = await connection.fetchval("SELECT count(*) FROM review_reports WHERE review_id=$1", UUID(review["review_id"]))
                            record("review_saved:" + review["review_id"], 1, count)
                        for decision in result.get("user_decisions", []):
                            count = await connection.fetchval("SELECT count(*) FROM user_decisions WHERE id=$1", UUID(decision["decision_id"]))
                            record("user_decision_saved:" + decision["decision_id"], 1, count)
                    if source["kind"] == "index_document" and source["status"] == "completed":
                        chunk_ids = [UUID(item["chunk_id"]) for item in source["payload"]["chunks"]]
                        count = await connection.fetchval(
                            "SELECT count(*) FROM chunk_embeddings WHERE chunk_id=ANY($1::uuid[]) AND embedding_model=$2",
                            chunk_ids, result["embedding_model"])
                        record("embeddings_saved", len(set(chunk_ids)), count)
                    trace_count = await connection.fetchval("SELECT count(*) FROM trace_events WHERE job_id=$1", source_id)
                    record("trace_events_available", True, trace_count > 0)
                status = "pass" if all(item["status"] == "pass" for item in checks) else "attention"
                await connection.execute(
                    """INSERT INTO task_audit_reports(audit_job_id,source_job_id,conversation_id,
                       source_execution_id,source_run_id,check_version,status,checks)
                       VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb) ON CONFLICT DO NOTHING""",
                    audit_job_id, source_id, conversation_id, UUID(payload["source_execution_id"]),
                    payload["source_run_id"], payload["check_version"], status, checks)
                return {"source_job_id": str(source_id), "status": status, "checks": checks}
