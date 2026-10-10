"""关联运行日志与业务复盘；不保存请求正文、模型提示词或数据库行。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
import time
import traceback
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from typing import Any
from uuid import UUID, uuid4
from urllib.parse import urlsplit, urlunsplit

from langgraph.errors import GraphInterrupt
from langchain_core.runnables import RunnableConfig
from langsmith.run_helpers import trace

from forge.infrastructure.langsmith_trace import continue_trace

from forge.domain.models import Identity


_context: ContextVar[dict[str, Any]] = ContextVar("forge_trace", default={})
_recorder: ContextVar[Any] = ContextVar("forge_recorder", default=None)
logger = logging.getLogger("forge.telemetry")


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def current_trace_id() -> UUID | None:
    value = _context.get().get("trace_id")
    return UUID(str(value)) if value else None


def error_fields(error: BaseException) -> dict[str, Any]:
    # 不输出异常文本：HTTP/SQL 异常可能携带密钥、输入或数据行。
    return {
        "error_type": type(error).__name__,
        "stack": [
            {"file": frame.filename.replace("\\", "/").rsplit("/", 1)[-1],
             "function": frame.name, "line": frame.lineno}
            for frame in traceback.StackSummary.extract(traceback.walk_tb(error.__traceback__), lookup_lines=False)[-12:]
        ],
    }


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {"timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname, "logger": record.name,
                **getattr(record, "telemetry", {"event": record.getMessage()})}
        if record.exc_info and record.exc_info[1]:
            data.update(error_fields(record.exc_info[1]))
        return json.dumps(data, ensure_ascii=False, default=str)


def configure_logging() -> None:
    root = logging.getLogger("forge")
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    root.propagate = False


@contextmanager
def trace_context(*, recorder: Any = None, **fields: Any):
    token = _context.set({**_context.get(), **fields})
    sink_token = _recorder.set(recorder if recorder is not None else _recorder.get())
    try:
        yield
    finally:
        _recorder.reset(sink_token)
        _context.reset(token)


async def emit(event: str, *, status: str = "info", **fields: Any) -> None:
    from forge.infrastructure.metrics import observe
    observe(event, status, fields)
    context = _context.get()
    identity = context.get("identity") or {}
    data = {"event_id": str(uuid4()), "event": event, "status": status,
            **{key: value for key, value in context.items() if key != "identity"},
            "user_id": identity.get("user_id"), "tenant_id": identity.get("tenant_id"), **fields}
    logger.log(logging.ERROR if status == "failed" else logging.INFO,
               event, extra={"telemetry": data})
    recorder = _recorder.get()
    if recorder is not None and event not in {"span.started", "span.finished"} and context.get("conversation_id") and context.get("identity"):
        try:
            # 避免日志数据库阻塞主流程。关键用户决策仍由业务投影负责持久化。
            await asyncio.wait_for(recorder.record(data, context["identity"]), timeout=2)
        except Exception as exc:
            observe("telemetry.persistence_failed", "failed", {})
            logger.error("telemetry.persistence_failed", extra={"telemetry": {
                "event": "telemetry.persistence_failed", "status": "failed",
                "trace_id": context.get("trace_id"), "job_id": context.get("job_id"),
                **error_fields(exc)}})


@asynccontextmanager
async def span(name: str, *, category: str = "tool", **fields: Any):
    started = time.perf_counter()
    span_id = str(uuid4())
    parent = _context.get().get("span_id")
    safe_metadata = {key: value for key, value in fields.items()
                     if key in {"model", "topic", "attempt", "dimensions", "run_status", "method"}
                     and isinstance(value, (str, int, float, bool))}
    run_type = "tool" if category == "tool" else "chain"
    with trace(name, run_type=run_type, inputs={}, metadata={"category": category, **safe_metadata},
               exceptions_to_handle=(GraphInterrupt,)):
        with trace_context(span_id=span_id, parent_span_id=parent):
            await emit("span.started", name=name, category=category, **fields)
            details: dict[str, Any] = {}
            try:
                yield details
            except GraphInterrupt:
                await emit("span.finished", status="paused", name=name, category=category,
                           duration_ms=round((time.perf_counter() - started) * 1000, 2), **fields)
                raise
            except BaseException as exc:
                await emit("span.finished", status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                           name=name, category=category,
                           duration_ms=round((time.perf_counter() - started) * 1000, 2),
                           **fields, **error_fields(exc))
                raise
            else:
                await emit("span.finished", status=details.pop("status", "succeeded"),
                           name=name, category=category,
                           duration_ms=round((time.perf_counter() - started) * 1000, 2), **fields, **details)


def traced(name: str, *, category: str = "tool"):
    def decorate(function):
        @wraps(function)
        async def observed(*args, **kwargs):
            async with span(name, category=category) as details:
                result = await function(*args, **kwargs)
                if isinstance(result, (list, tuple)):
                    details["result_count"] = len(result)
                    refs = []
                    for item in result:
                        if hasattr(item, "source_kind"):
                            metadata = item.metadata
                            parsed = urlsplit(item.url or "")
                            refs.append({"evidence_id": str(item.evidence_id), "source_kind": item.source_kind,
                                         "url": urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", "")),
                                         "content_fingerprint": fingerprint(item.quote),
                                         **{key: metadata[key] for key in
                                            ("storage", "table", "tables", "resource", "line", "chunk_id") if key in metadata}})
                    if refs:
                        details["evidence_refs"] = refs
                        if any(item["source_kind"] == "skill_md" for item in refs):
                            details["status"] = "degraded"
                return result
        return observed
    return decorate


def observed_node(function, recorder):
    async def observed(state: dict[str, Any], config: RunnableConfig):
        configurable = config.get("configurable", {})
        metadata = config.get("metadata", {})
        with continue_trace(configurable.get("forge_trace_context")):
          with trace_context(
            recorder=recorder,
            trace_id=configurable.get("forge_trace_id") or state.get("trace_id"),
            job_id=configurable.get("forge_job_id") or state.get("job_id"),
            run_id=str(configurable.get("run_id") or metadata.get("run_id") or config.get("run_id") or ""),
            conversation_id=state.get("conversation_id"), identity=state.get("identity"),
            node=function.__name__,
          ):
            async with span(function.__name__, category="node") as details:
                result = await function(state)
                if function.__name__ in {"generator", "revise"} and result.get("draft_changed"):
                    old = {str(item["paragraph_id"]): item for item in state.get("draft_paragraphs", [])}
                    facts = {str(item["fact_id"]): item for item in state.get("facts", [])}
                    issue_ids = {str(item.get("paragraph_id")): [] for item in result["draft_paragraphs"]}
                    for issue in state.get("critic_issues", []) if function.__name__ == "revise" else []:
                        issue_ids.setdefault(str(issue.get("paragraph_id")), []).append(str(issue["issue_id"]))
                    previous = state.get("draft_history", [])
                    snapshot = {
                        "snapshot_id": str(uuid4()), "step": function.__name__,
                        "previous_snapshot_id": previous[-1]["snapshot_id"] if previous else None,
                        "paragraphs": result["draft_paragraphs"],
                        "fact_refs": {str(item["paragraph_id"]): [
                            {"fact_id": fact_id, "version": facts[fact_id]["version"],
                             "claim": facts[fact_id]["claim"],
                             "decision_id": facts[fact_id].get("decision_id"),
                             "evidence": [{"evidence_id": e["evidence_id"], "source_kind": e["source_kind"],
                                           "title": e.get("title", ""), "quote": e.get("quote", ""),
                                           "url": e.get("url", ""), "document_id": e.get("document_id"),
                                           "document_version": e.get("document_version", ""),
                                           "metadata": e.get("metadata", {})}
                                          for e in facts[fact_id].get("evidence", [])]}
                            for fact_id in item.get("fact_ids", []) if fact_id in facts]
                            for item in result["draft_paragraphs"]},
                        "issue_ids": issue_ids,
                        "prior_hashes": {key: fingerprint(item["content"]) for key, item in old.items()},
                    }
                    result["draft_history"] = [*previous, snapshot]
                if function.__name__ == "critic" and result.get("critic_status"):
                    checks = []
                    for row in result.get("fact_checks", []):
                        checks.append({**row, "check_id": row.get("check_id") or str(uuid4())})
                    result["fact_checks"] = checks
                    result["review_history"] = [*state.get("review_history", []), {
                        "review_id": str(uuid4()), "snapshot_id":
                        state.get("draft_history", [])[-1]["snapshot_id"] if state.get("draft_history") else None,
                        "status": result["critic_status"],
                        "failure_number": result.get("critic_failures", state.get("critic_failures", 0)),
                        "issues": result.get("pending_conflicts") or result.get("critic_issues", []),
                        "fact_checks": checks, "stop_reason": result.get("stop_reason", ""),
                    }]
                if function.__name__ == "critic":
                    await emit("review.completed", status=result.get("critic_status", "incomplete"),
                               failure_number=result.get("critic_failures", state.get("critic_failures", 0)),
                               issues=[{key: item.get(key) for key in
                                        ("kind", "severity", "rule_code", "fact_id", "section_id", "requires_user")}
                                       for item in result.get("critic_issues", [])],
                               issue_fingerprints=result.get("issue_history", []),
                               stop_reason=result.get("stop_reason", ""))
                if function.__name__ in {"plan_conflict_decision", "conflict_decision"}:
                    prior_system = {item["decision_id"] for item in state.get("system_decisions", [])}
                    for decision in result.get("system_decisions", []):
                        if decision["decision_id"] not in prior_system:
                            await emit("system.decision", **{key: decision.get(key) for key in
                                       ("decision_id", "fact_id", "fact_version", "action", "rule", "actor_kind")})
                if function.__name__ == "approval" and result.get("approval"):
                    await emit("plan.approved", fact_version=state.get("fact_version"),
                               outline_version=state.get("outline_version"),
                               actor_kind=result["approval"].get("actor_kind", "user"),
                               rule=result["approval"].get("rule", "confirmed_plan"))
                details.update({key: result[key] for key in
                                ("stage", "critic_status", "critic_failures", "fact_version", "outline_version")
                                if key in result})
                details["issue_count"] = len(result.get("critic_issues", []))
                # warning 文本可能包含数据库错误原文，仅保留计数和指纹。
                warnings = result.get("warnings", [])
                details["warning_count"] = len(warnings)
                details["warning_fingerprints"] = [fingerprint(str(item)) for item in warnings]
                if result.get("stage") == "needs_attention":
                    details["status"] = "needs_attention"
                elif warnings:
                    details["status"] = "degraded"
                return {**result, **{key: _context.get()[key] for key in ("trace_id", "job_id") if _context.get().get(key)}}
    return observed


class PostgresTraceRecorder:
    def __init__(self, database):
        self.database = database

    async def record(self, data: dict[str, Any], identity: dict[str, Any]) -> None:
        async with self.database.authorized(Identity.model_validate(identity)) as connection:
            await connection.execute(
                """INSERT INTO trace_events (id, conversation_id, job_id, trace_id, event, status, payload)
                   VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)""",
                UUID(data["event_id"]), UUID(data["conversation_id"]),
                UUID(data["job_id"]) if data.get("job_id") else None,
                UUID(data["trace_id"]) if data.get("trace_id") else None,
                data["event"], data["status"], data,
            )

    async def review(self, conversation_id: UUID, job_id: UUID, identity: Identity,
                     *, after: int = 0) -> dict[str, Any]:
        async with self.database.authorized(identity) as connection:
            job = await connection.fetchrow(
                """SELECT j.id, j.status, j.trace_id, j.remote_run_id FROM workflow_jobs j
                   JOIN conversations c ON c.id=j.conversation_id
                   WHERE j.id=$1 AND c.id=$2 AND c.owner_id=$3""", job_id, conversation_id, identity.user_id)
            if job is None:
                raise LookupError("任务不存在或无权访问")
            stats = await connection.fetch(
                """SELECT event, status, payload->>'name' AS name, count(*) AS count,
                          sum((payload->>'duration_ms')::numeric) AS duration_ms
                   FROM trace_events WHERE job_id=$1 AND conversation_id=$2
                   GROUP BY event,status,payload->>'name' ORDER BY event,status,name""", job_id, conversation_id)
            rows = await connection.fetch(
                """SELECT sequence, created_at, payload FROM trace_events
                   WHERE job_id=$1 AND conversation_id=$2 AND sequence>$3
                   ORDER BY sequence LIMIT 200""", job_id, conversation_id, max(after, 0))
            executions = await connection.fetch(
                """SELECT created_at,payload->>'execution_id' AS execution_id,
                          payload->>'remote_run_id' AS remote_run_id,
                          payload->>'langsmith_trace_id' AS langsmith_trace_id
                   FROM workflow_events WHERE job_id=$1 AND conversation_id=$2
                     AND event_type='submitted' ORDER BY sequence""", job_id, conversation_id)
            audit_reports = await connection.fetch(
                "SELECT * FROM task_audit_reports WHERE source_job_id=$1 ORDER BY created_at", job_id)
        statistics = [dict(row) for row in stats]
        return {"job": dict(job), "summary": {
                    "event_count": sum(item["count"] for item in statistics),
                    "failure_count": sum(item["count"] for item in statistics if item["status"] == "failed"),
                    "degradation_count": sum(item["count"] for item in statistics if item["event"] == "fallback.used"),
                    "statistics": statistics,
                    "note": "失败数按事件计算；嵌套工具与节点可能对应同一根因。"},
                "events": [dict(row) for row in rows],
                "executions": [dict(row) for row in executions],
                "audit_reports": [dict(row) for row in audit_reports],
                "next_cursor": rows[-1]["sequence"] if rows else after}

    async def timeline(self, conversation_id: UUID, identity: Identity, *, after: int = 0) -> dict[str, Any]:
        async with self.database.authorized(identity) as connection:
            if not await connection.fetchval("SELECT id FROM conversations WHERE id=$1 AND owner_id=$2",
                                             conversation_id, identity.user_id):
                raise LookupError("对话不存在或无权访问")
            rows = await connection.fetch(
                """SELECT sequence, created_at, payload FROM trace_events
                   WHERE conversation_id=$1 AND sequence>$2 ORDER BY sequence LIMIT 200""",
                conversation_id, max(after, 0))
        return {"events": [dict(row) for row in rows], "next_cursor": rows[-1]["sequence"] if rows else after}
