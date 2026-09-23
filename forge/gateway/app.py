"""Gateway API - 使用 LangGraph SDK 调用 LangGraph Server。

架构：
┌─────────────────┐    ┌─────────────────────────────┐
│   Gateway API   │───▶│   LangGraph Server (2024)   │
│   (FastAPI)     │SDK │   deep_mode_agent           │
│   (8001)        │    │   Thread 视角观察            │
│   Trace stitching│    │   LangSmith / Studio        │
└─────────────────┘    └─────────────────────────────┘

Trace 设计：
- Gateway 创建 deep_mode_session parent trace
- LangSmith headers 存在 LangGraph thread state 中
- 后续 resume 使用 parent headers 创建 child trace，并传给 LangGraph Server
"""

from contextlib import asynccontextmanager
import logging
from typing import AsyncIterator, Dict, Any, Literal, Optional, Tuple
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from forge.security.auth import (
    Principal,
    authenticate_websocket,
    require_admin,
    require_principal,
)
from forge.observability.logging import configure_logging
from forge.observability.metrics import install_http_metrics

import langsmith as ls
from langgraph_sdk import get_client
from langgraph_sdk.schema import Command

from dotenv import load_dotenv
load_dotenv()

configure_logging("gateway")
logger = logging.getLogger(__name__)


# ============================================================================
# Scraper Helper
# ============================================================================

async def _fetch_article_content(url: str) -> Dict[str, str]:
    """根据 URL 抓取文章内容。

    Args:
        url: 文章 URL

    Returns:
        dict with title, text
    """
    from forge.scraping.article_fetcher import fetch_article_content

    return await fetch_article_content(url)

# ============================================================================
# LangGraph SDK Client
# ============================================================================

import os
LANGGRAPH_SERVER_URL = os.getenv("LANGGRAPH_SERVER_URL", "http://langgraph:2024")
DEEP_MODE_AGENT_ID = os.getenv("DEEP_MODE_AGENT_ID", "deep_mode_agent")
GATEWAY_TRACE_HEADERS_KEY = "gateway_trace_headers"
_client = None


def get_langgraph_client():
    """获取 LangGraph SDK Client。"""
    global _client
    if _client is None:
        _client = get_client(url=LANGGRAPH_SERVER_URL)
        logger.info(f"[Gateway] LangGraph SDK client initialized: {LANGGRAPH_SERVER_URL}")
    return _client


# ============================================================================
# LangSmith Trace Stitching
# ============================================================================

async def _save_trace_headers(client, thread_id: str, headers: Dict[str, str]) -> None:
    """Persist parent trace headers in LangGraph thread state."""
    if not headers:
        return

    try:
        await client.threads.update_state(
            thread_id,
            {GATEWAY_TRACE_HEADERS_KEY: dict(headers)},
            headers=headers,
        )
        logger.info(f"[GatewayTrace] Saved trace headers for thread: {thread_id}")
    except Exception as e:
        logger.warning(f"[GatewayTrace] Failed to save trace headers for {thread_id}: {e}")


async def _load_trace_headers(client, thread_id: str) -> Dict[str, str]:
    """Load parent trace headers from LangGraph thread state."""
    try:
        state = await client.threads.get_state(thread_id)
        values = state.get("values", {})
        headers = values.get(GATEWAY_TRACE_HEADERS_KEY) or {}
        if isinstance(headers, dict):
            return {str(k): str(v) for k, v in headers.items()}
    except Exception as e:
        logger.warning(f"[GatewayTrace] Failed to load trace headers for {thread_id}: {e}")

    return {}


@asynccontextmanager
async def _child_trace(
    name: str,
    parent_headers: Dict[str, str],
    *,
    thread_id: str,
    inputs: Dict[str, Any],
) -> AsyncIterator[Tuple[Optional[Any], Optional[Dict[str, str]]]]:
    """Create a Gateway child span and return headers for the remote LangGraph run."""
    if not parent_headers:
        yield None, None
        return

    async with ls.trace(
        name,
        run_type="chain",
        inputs=inputs,
        parent=parent_headers,
        metadata={"thread_id": thread_id, "component": "forge-gateway"},
    ) as run:
        yield run, run.to_headers()


def _summarize_result(result: Any) -> Dict[str, Any]:
    """Keep Gateway trace outputs small and useful."""
    if not isinstance(result, dict):
        return {"status": "completed", "result_type": type(result).__name__}

    if "__interrupt__" in result:
        interrupt_data = result["__interrupt__"][0]
        interrupt_value = interrupt_data.get("value", {})
        return {
            "status": "interrupted",
            "interrupt_type": interrupt_value.get("type", ""),
            "stage": interrupt_value.get("stage", ""),
        }

    return {
        "status": "completed",
        "stage": result.get("stage", ""),
        "has_final_draft": bool(result.get("final_draft")),
    }


# ============================================================================
# Request Models
# ============================================================================

class CreateSessionRequest(BaseModel):
    article_id: str
    source_article: Dict[str, str]
    user_input: Optional[str] = None
    target_platform: str = "zhihu_article"
    memory_owner_id: str = "local-user"


class OutlineActionRequest(BaseModel):
    session_id: str
    action: str  # "accept" / "modify"
    modification: Optional[str] = None


class FactReviewRequest(BaseModel):
    """事实清单审核请求。"""
    session_id: str
    action: str  # "accept" / "update" / "research_more"
    reviews: list[dict[str, Any]] = Field(default_factory=list)
    research_request: Optional[str] = None


class FinalizeRequest(BaseModel):
    session_id: str
    content: Optional[str] = None


class UpdateOutlineRequest(BaseModel):
    session_id: str
    outline: str
    outline_version: Optional[int] = None


class GenerationTaskRequest(BaseModel):
    article_id: str
    source_article: Dict[str, str]
    user_input: str = ""
    target_platform: str = "zhihu_article"


class GenerationApprovalRequest(BaseModel):
    interrupt_id: str = Field(min_length=1, max_length=256)
    content_version: str = Field(min_length=1, max_length=128)
    decision: Literal[
        "accept",
        "modify",
        "approve",
        "reject",
        "research_more",
        "tuning",
        "finalize",
    ]
    data: Dict[str, Any] = Field(default_factory=dict)

    def resume_payload(self) -> Dict[str, Any]:
        decision = {
            "accept": "approve",
            "modify": "reject",
        }.get(self.decision, self.decision)
        return {**self.data, "decision": decision}


# ============================================================================
# FastAPI App
# ============================================================================

app = FastAPI(
    title="Forge Gateway API",
    description="Gateway for LangGraph Server with LangSmith trace stitching",
)
install_http_metrics(app, "gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def close_gateway_job_events():
    from forge.async_jobs.events import close_job_event_hub

    await close_job_event_hub()


@app.middleware("http")
async def authenticate_gateway_api(request, call_next):
    if request.url.path.startswith("/api/") and request.method != "OPTIONS":
        try:
            await require_principal(request)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers,
            )
    return await call_next(request)


# ============================================================================
# API Routes
# ============================================================================

@app.get("/")
async def root():
    return {"message": "Forge Gateway API", "langgraph_server": LANGGRAPH_SERVER_URL}


@app.get("/live")
async def live():
    return {"status": "alive"}


@app.get("/ready")
async def ready():
    from forge.storage.pg_client import get_pg_pool

    if await get_pg_pool() is None:
        raise HTTPException(status_code=503, detail="PostgreSQL unavailable")
    try:
        await get_langgraph_client().assistants.search()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="LangGraph unavailable") from exc
    return {"status": "ready"}


@app.get("/health")
async def health():
    """Health check."""
    client = get_langgraph_client()
    try:
        assistants = await client.assistants.search()
        return {
            "status": "healthy",
            "langgraph_server": LANGGRAPH_SERVER_URL,
            "assistants": len(assistants),
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


@app.post("/generation-tasks", status_code=202)
async def create_generation_task_route(
    request: GenerationTaskRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
    principal: Principal = Depends(require_principal),
):
    """持久化文章生成任务和 Outbox 事件后立即返回。"""
    from forge.async_jobs.generation_service import create_generation_task
    from forge.async_jobs.repository import (
        IdempotencyConflict,
        JobStoreUnavailable,
        TenantActiveJobLimitExceeded,
    )
    from forge.security.quota import TenantRateLimitExceeded

    source_article = dict(request.source_article)
    if not source_article.get("url") and request.article_id.startswith(("http://", "https://")):
        source_article["url"] = request.article_id
    payload = {
        "article_id": request.article_id,
        "source_article": source_article,
        "user_input": request.user_input,
        "target_platform": request.target_platform,
        "memory_owner_id": principal.subject,
    }
    try:
        task, created = await create_generation_task(
            payload=payload,
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
            idempotency_key=idempotency_key,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TenantActiveJobLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except TenantRateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except JobStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(
        status_code=202,
        content={
            "task_id": str(task["id"]),
            "thread_id": str(task["thread_id"]),
            "status": task["status"],
            "deduplicated": not created,
            "status_url": f"/generation-tasks/{task['id']}",
        },
    )


@app.get("/generation-tasks/{task_id}")
async def get_generation_task_route(
    task_id: str,
    principal: Principal = Depends(require_principal),
):
    from forge.async_jobs.generation_repository import GenerationTaskRepository
    from forge.async_jobs.repository import JobStoreUnavailable
    from forge.async_jobs.state_machine import public_task_view

    try:
        task = await GenerationTaskRepository().get_task(
            task_id, principal.tenant_id, principal.subject
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="task_id 必须是 UUID") from exc
    except JobStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="Generation task not found")
    return public_task_view(task)


@app.post("/generation-tasks/{task_id}/approvals", status_code=202)
async def approve_generation_task_route(
    task_id: str,
    request: GenerationApprovalRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
    principal: Principal = Depends(require_principal),
):
    from forge.async_jobs.generation_service import approve_generation_task
    from forge.async_jobs.repository import JobStoreUnavailable
    from forge.async_jobs.state_machine import ApprovalConflict, StaleApproval, TaskNotFound

    try:
        approval, created = await approve_generation_task(
            task_id=task_id,
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
            idempotency_key=idempotency_key,
            interrupt_id=request.interrupt_id,
            content_version=request.content_version,
            decision=request.decision,
            payload=request.resume_payload(),
        )
    except (ApprovalConflict, StaleApproval) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="task_id 必须是 UUID") from exc
    except JobStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(
        status_code=202,
        content={
            "task_id": task_id,
            "approval_id": str(approval["id"]),
            "status": "queued",
            "deduplicated": not created,
        },
    )


@app.post("/generation-tasks/{task_id}/cancel", status_code=202)
async def cancel_generation_task_route(
    task_id: str,
    principal: Principal = Depends(require_principal),
):
    from forge.async_jobs.generation_repository import GenerationTaskRepository
    from forge.async_jobs.repository import JobStoreUnavailable
    from forge.async_jobs.state_machine import TaskNotFound, public_task_view

    try:
        task = await GenerationTaskRepository().request_cancel(
            task_id=task_id,
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
        )
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="task_id 必须是 UUID") from exc
    except JobStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(status_code=202, content=public_task_view(task))


@app.get("/api/admin/generation-dead-letters")
async def list_generation_dead_letters(
    limit: int = Query(default=100, ge=1, le=500),
    _principal: Principal = Depends(require_admin),
):
    from forge.async_jobs.generation_repository import GenerationTaskRepository

    return {"items": await GenerationTaskRepository().list_dead_letters(limit)}


@app.post("/api/admin/generation-dead-letters/{dead_letter_id}/replay")
async def replay_generation_dead_letter(
    dead_letter_id: int,
    principal: Principal = Depends(require_admin),
):
    from forge.async_jobs.generation_repository import GenerationTaskRepository

    replayed = await GenerationTaskRepository().replay_dead_letter(
        dead_letter_id, principal.subject
    )
    if replayed is None:
        raise HTTPException(status_code=409, detail="死信不存在、已重放或任务状态不允许重放")
    return replayed


async def execute_create_session(request: CreateSessionRequest):
    """创建深度生成会话。"""
    client = get_langgraph_client()

    try:
        # 创建 thread
        thread = await client.threads.create()
        thread_id = thread["thread_id"]
        logger.info(f"[Gateway] Thread created: {thread_id}")

        # 如果 source_article.text 为空，自动抓取内容
        source_article = dict(request.source_article)
        if not source_article.get("url") and request.article_id.startswith(("http://", "https://")):
            source_article["url"] = request.article_id
        if not source_article.get("text"):
            logger.info(f"[Gateway] Article text is empty, fetching from URL: {request.article_id}")
            fetched = await _fetch_article_content(request.article_id)
            if fetched.get("text"):
                source_article["text"] = fetched["text"]
                if not source_article.get("title") and fetched.get("title"):
                    source_article["title"] = fetched["title"]
                logger.info(f"[Gateway] Fetched article content: {len(fetched['text'])} chars")
            else:
                logger.warning(f"[Gateway] Failed to fetch article content from {request.article_id}")

        run_input = {
            "session_id": thread_id,
            "memory_owner_id": request.memory_owner_id,
            "source_article": source_article,
            "user_input": request.user_input or "",
            "target_platform": request.target_platform,
        }

        # 创建 parent trace，并把 headers 传给 LangGraph Server 的第一次 run
        async with ls.trace(
            "deep_mode_session",
            run_type="chain",
            inputs={
                "session_id": thread_id,
                "article_id": request.article_id,
                "user_input": request.user_input or "",
            },
            metadata={
                "thread_id": thread_id,
                "article_id": request.article_id,
                "component": "forge-gateway",
            },
        ) as trace_run:
            trace_headers = trace_run.to_headers()
            result = await client.runs.wait(
                thread_id,
                DEEP_MODE_AGENT_ID,
                input=run_input,
                headers=trace_headers,
            )
            trace_run.end(outputs=_summarize_result(result))

        await _save_trace_headers(client, thread_id, trace_headers)

        # 检查是否遇到 interrupt
        if "__interrupt__" in result:
            interrupt_data = result["__interrupt__"][0]
            interrupt_value = interrupt_data.get("value", {})
            logger.info(f"[Gateway] Interrupted at: {interrupt_data.get('ns', [])}")

            if interrupt_value.get("type") == "fact_approval":
                return {
                    "session_id": thread_id,
                    "stage": "waiting_facts",
                    "research_result": interrupt_value.get("research_result", {}),
                    "hil_status": "interrupted",
                    "interrupt_type": "fact_approval",
                }
            return {
                "session_id": thread_id,
                "stage": "waiting_outline",
                "outline": interrupt_value.get("outline", ""),
                "outline_version": interrupt_value.get("outline_version", 1),
                "hil_status": "interrupted",
                "interrupt_type": "outline_approval",
            }

        return {
            "session_id": thread_id,
            "stage": result.get("stage", "completed"),
            "error": result.get("error"),
        }

    except Exception as e:
        logger.error(f"[Gateway] Create session failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def execute_outline_action(request: OutlineActionRequest):
    """大纲操作。"""
    client = get_langgraph_client()
    thread_id = request.session_id

    try:
        # 构建 resume 响应
        if request.action == "accept":
            resume_value = {"decision": "approve"}
        elif request.action == "modify":
            resume_value = {"decision": "reject", "feedback": request.modification or ""}
        else:
            raise HTTPException(status_code=400, detail=f"Invalid action: {request.action}")

        parent_headers = await _load_trace_headers(client, thread_id)
        async with _child_trace(
            "outline_action",
            parent_headers,
            thread_id=thread_id,
            inputs={
                "action": request.action,
                "has_modification": bool(request.modification),
            },
        ) as (trace_run, trace_headers):
            result = await client.runs.wait(
                thread_id,
                DEEP_MODE_AGENT_ID,
                command=Command(resume=resume_value),
                headers=trace_headers,
            )
            if trace_run:
                trace_run.end(outputs=_summarize_result(result))

        # 检查是否遇到下一个 interrupt
        if "__interrupt__" in result:
            interrupt_data = result["__interrupt__"][0]
            interrupt_value = interrupt_data.get("value", {})
            interrupt_type = interrupt_value.get("type", "")

            if interrupt_type == "fact_approval":
                return {
                    "session_id": thread_id,
                    "stage": "waiting_facts",
                    "research_result": interrupt_value.get("research_result", {}),
                    "hil_status": "interrupted",
                    "interrupt_type": "fact_approval",
                }
            elif interrupt_type == "content_approval":
                return {
                    "session_id": thread_id,
                    "stage": "tuning",
                    "draft": interrupt_value.get("draft", ""),
                    "research_result": interrupt_value.get("research_result", {}),
                    "quality_report": interrupt_value.get("quality_report", {}),
                    "tuning_messages": interrupt_value.get("tuning_messages", []),
                    "hil_status": "interrupted",
                    "interrupt_type": "content_approval",
                }
            elif interrupt_type == "outline_approval":
                return {
                    "session_id": thread_id,
                    "stage": "waiting_outline",
                    "outline": interrupt_value.get("outline", ""),
                    "outline_version": interrupt_value.get("outline_version", 1),
                    "outline_thinking": interrupt_value.get("outline_thinking", ""),
                    "hil_status": "interrupted",
                    "interrupt_type": "outline_approval",
                }

        return {
            "session_id": thread_id,
            "stage": result.get("stage", "completed"),
            "outline": result.get("outline", ""),
            "outline_version": result.get("outline_version", 1),
            "final_draft": result.get("final_draft", ""),
        }

    except Exception as e:
        logger.error(f"[Gateway] Outline action failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def execute_fact_action(request: FactReviewRequest):
    """提交事实清单的接受、排除、质疑或补充研究请求。"""
    client = get_langgraph_client()
    thread_id = request.session_id
    if request.action not in {"accept", "update", "research_more"}:
        raise HTTPException(status_code=400, detail=f"Invalid fact action: {request.action}")
    decision = {
        "accept": "approve",
        "update": "update",
        "research_more": "research_more",
    }[request.action]

    try:
        parent_headers = await _load_trace_headers(client, thread_id)
        async with _child_trace(
            "fact_action",
            parent_headers,
            thread_id=thread_id,
            inputs={
                "action": request.action,
                "review_count": len(request.reviews),
                "has_research_request": bool(request.research_request),
            },
        ) as (trace_run, trace_headers):
            result = await client.runs.wait(
                thread_id,
                DEEP_MODE_AGENT_ID,
                command=Command(resume={
                    "decision": decision,
                    "reviews": request.reviews,
                    "research_request": request.research_request or "",
                }),
                headers=trace_headers,
            )
            if trace_run:
                trace_run.end(outputs=_summarize_result(result))

        if "__interrupt__" in result:
            interrupt_value = result["__interrupt__"][0].get("value", {})
            interrupt_type = interrupt_value.get("type", "")
            if interrupt_type == "fact_approval":
                return {
                    "session_id": thread_id,
                    "stage": "waiting_facts",
                    "research_result": interrupt_value.get("research_result", {}),
                    "hil_status": "interrupted",
                    "interrupt_type": "fact_approval",
                }
            if interrupt_type == "outline_approval":
                return {
                    "session_id": thread_id,
                    "stage": "waiting_outline",
                    "outline": interrupt_value.get("outline", ""),
                    "outline_version": interrupt_value.get("outline_version", 1),
                    "research_result": interrupt_value.get("research_result", {}),
                    "hil_status": "interrupted",
                    "interrupt_type": "outline_approval",
                }
        return {
            "session_id": thread_id,
            "stage": result.get("stage", "completed"),
            "outline": result.get("outline", ""),
            "outline_version": result.get("outline_version", 1),
            "final_draft": result.get("final_draft", ""),
        }
    except Exception as e:
        logger.error(f"[Gateway] Fact action failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/deep_mode/update_outline")
async def update_outline(
    request: UpdateOutlineRequest,
    principal: Principal = Depends(require_principal),
):
    """同步用户编辑后的大纲到 LangGraph 线程状态。"""
    from forge.async_jobs.repository import JobRepository

    if not await JobRepository().owns_session(
        request.session_id,
        principal.tenant_id,
        principal.subject,
    ):
        raise HTTPException(status_code=404, detail="Session not found")
    client = get_langgraph_client()
    thread_id = request.session_id

    try:
        parent_headers = await _load_trace_headers(client, thread_id)
        state = await client.threads.get_state(thread_id)
        values = state.get("values", {})
        outline_version = request.outline_version
        if outline_version is None:
            outline_version = values.get("outline_version", 0) + 1

        async with _child_trace(
            "update_outline",
            parent_headers,
            thread_id=thread_id,
            inputs={
                "outline_length": len(request.outline),
                "outline_version": outline_version,
            },
        ) as (trace_run, trace_headers):
            await client.threads.update_state(
                thread_id,
                {
                    "outline": request.outline,
                    "outline_version": outline_version,
                },
                headers=trace_headers,
            )
            if trace_run:
                trace_run.end(outputs={"status": "updated", "outline_version": outline_version})

        return {
            "session_id": thread_id,
            "status": "updated",
            "outline": request.outline,
            "outline_version": outline_version,
        }

    except Exception as e:
        logger.error(f"[Gateway] Update outline failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def execute_finalize_session(request: FinalizeRequest):
    """定稿会话。"""
    client = get_langgraph_client()
    thread_id = request.session_id

    try:
        parent_headers = await _load_trace_headers(client, thread_id)

        if request.content:
            await client.threads.update_state(
                thread_id,
                {"current_draft": request.content},
                headers=parent_headers or None,
            )

        async with _child_trace(
            "finalize",
            parent_headers,
            thread_id=thread_id,
            inputs={"has_user_content": bool(request.content)},
        ) as (trace_run, trace_headers):
            result = await client.runs.wait(
                thread_id,
                DEEP_MODE_AGENT_ID,
                command=Command(resume={"decision": "finalize"}),
                headers=trace_headers,
            )
            if trace_run:
                trace_run.end(outputs=_summarize_result(result))

        return {
            "session_id": thread_id,
            "status": "completed",
            "final_draft": result.get("final_draft", ""),
        }

    except Exception as e:
        logger.error(f"[Gateway] Finalize failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _enqueue_workflow_operation(
    operation: str,
    request: BaseModel,
    idempotency_key: Optional[str],
    tenant_id: str,
    actor_id: str,
):
    from forge.async_jobs.repository import (
        IdempotencyConflict,
        JobStoreUnavailable,
        ResourceOwnershipError,
        TenantActiveJobLimitExceeded,
    )
    from forge.async_jobs.service import submit_job
    from forge.security.quota import QuotaStoreUnavailable, TenantRateLimitExceeded

    key = idempotency_key or str(uuid4())
    if not tenant_id or len(tenant_id) > 128:
        raise HTTPException(status_code=400, detail="JWT tenant_id 长度必须为 1-128")
    if len(key) > 128:
        raise HTTPException(status_code=400, detail="Idempotency-Key 长度不能超过 128")
    payload = request.model_dump()
    if operation == "create_session":
        payload["memory_owner_id"] = actor_id
    try:
        job, created = await submit_job(
            operation=operation,
            payload=payload,
            tenant_id=tenant_id,
            actor_id=actor_id,
            idempotency_key=key,
        )
    except JobStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TenantRateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except TenantActiveJobLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ResourceOwnershipError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except QuotaStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return JSONResponse(
        status_code=202,
        content={
            "job_id": job["id"],
            "operation": job["operation"],
            "status": job["status"],
            "created_at": job["created_at"],
            "deduplicated": not created,
            "status_url": f"/api/jobs/{job['id']}",
        },
    )


@app.post("/api/deep_mode/create_session", status_code=202)
async def create_session(
    request: CreateSessionRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_principal),
):
    """提交创建会话任务，立即返回 job_id。"""
    return await _enqueue_workflow_operation(
        "create_session", request, idempotency_key, principal.tenant_id, principal.subject
    )


@app.post("/api/deep_mode/outline_action", status_code=202)
async def outline_action(
    request: OutlineActionRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_principal),
):
    """提交大纲操作任务，立即返回 job_id。"""
    return await _enqueue_workflow_operation(
        "outline_action", request, idempotency_key, principal.tenant_id, principal.subject
    )


@app.post("/api/deep_mode/fact_action", status_code=202)
async def fact_action(
    request: FactReviewRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_principal),
):
    """提交事实审核任务，立即返回 job_id。"""
    return await _enqueue_workflow_operation(
        "fact_action", request, idempotency_key, principal.tenant_id, principal.subject
    )


@app.post("/api/deep_mode/finalize", status_code=202)
async def finalize_session(
    request: FinalizeRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_principal),
):
    """提交定稿任务，立即返回 job_id。"""
    return await _enqueue_workflow_operation(
        "finalize", request, idempotency_key, principal.tenant_id, principal.subject
    )


@app.get("/api/jobs/{job_id}")
async def get_job(
    job_id: str,
    principal: Principal = Depends(require_principal),
):
    """查询异步任务状态与结果。"""
    from forge.async_jobs.repository import JobRepository, JobStoreUnavailable

    try:
        job = await JobRepository().get_job(
            job_id,
            principal.tenant_id,
            principal.subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="job_id 必须是 UUID") from exc
    except JobStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs/{job_id}/events")
async def get_job_events(
    job_id: str,
    principal: Principal = Depends(require_principal),
):
    """以 SSE 推送任务状态，替代客户端高频轮询。"""
    from forge.async_jobs.events import stream_job_events
    from forge.async_jobs.repository import JobRepository

    try:
        job = await JobRepository().get_job(
            job_id,
            principal.tenant_id,
            principal.subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="job_id 必须是 UUID") from exc
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return StreamingResponse(
        stream_job_events(job_id, principal.tenant_id, principal.subject),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/admin/outbox/dead-letters")
async def list_outbox_dead_letters(
    limit: int = Query(default=100, ge=1, le=500),
    _principal: Principal = Depends(require_admin),
):
    """列出 Outbox 死信，供运维审计。"""
    from forge.async_jobs.repository import JobRepository

    return {"items": await JobRepository().list_dead_letters(limit)}


@app.post("/api/admin/outbox/dead-letters/{outbox_id}/replay")
async def replay_outbox_dead_letter(
    outbox_id: int,
    _principal: Principal = Depends(require_admin),
):
    """重置死信状态，由 Outbox 发布器重新投递。"""
    from forge.async_jobs.repository import JobRepository

    replayed = await JobRepository().replay_dead_letter(outbox_id)
    if not replayed:
        raise HTTPException(status_code=404, detail="Outbox dead letter not found")
    return {"status": "queued", "outbox_id": outbox_id}


@app.get("/api/deep_mode/session/{session_id}")
async def get_session(
    session_id: str,
    principal: Principal = Depends(require_principal),
):
    """获取会话状态。"""
    from forge.async_jobs.repository import JobRepository

    if not await JobRepository().owns_session(
        session_id,
        principal.tenant_id,
        principal.subject,
    ):
        raise HTTPException(status_code=404, detail="Session not found")
    client = get_langgraph_client()

    try:
        state = await client.threads.get_state(session_id)
        values = state.get("values", {})

        return {
            "session_id": session_id,
            "stage": values.get("stage", ""),
            "outline": values.get("outline", ""),
            "outline_version": values.get("outline_version", 1),
            "current_draft": values.get("current_draft", ""),
            "final_draft": values.get("final_draft", ""),
            "research_result": values.get("research_result", {}),
            "quality_report": values.get("quality_report", {}),
            "tuning_messages": values.get("tuning_messages", []),
        }

    except Exception as e:
        logger.error(f"[Gateway] Get session failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# WebSocket for Tuning
# ============================================================================

@app.websocket("/ws/deep_mode/{session_id}")
async def websocket_tuning(websocket: WebSocket, session_id: str):
    """WebSocket 用于实时微调交互。"""
    principal = await authenticate_websocket(websocket)
    if principal is None:
        return
    from forge.async_jobs.repository import JobRepository

    if not await JobRepository().owns_session(
        session_id,
        principal.tenant_id,
        principal.subject,
    ):
        await websocket.close(code=4404, reason="会话不存在")
        return
    await websocket.accept()
    logger.info(
        "[WebSocket] Connection established: session=%s tenant=%s",
        session_id,
        principal.tenant_id,
    )

    client = get_langgraph_client()

    try:
        # 发送当前状态
        state = await client.threads.get_state(session_id)
        values = state.get("values", {})
        await websocket.send_json({
            "type": "stage_update",
            "session_id": session_id,
            "stage": values.get("stage", ""),
            "current_draft": values.get("current_draft", ""),
        })

        # 消息循环
        while True:
            data = await websocket.receive_json()
            message_type = data.get("type")

            if message_type == "tuning_message":
                user_message = data.get("content", "")
                logger.info(f"[WebSocket] Tuning request: {user_message[:50]}...")

                parent_headers = await _load_trace_headers(client, session_id)
                async with _child_trace(
                    "tuning_message",
                    parent_headers,
                    thread_id=session_id,
                    inputs={"message_length": len(user_message)},
                ) as (trace_run, trace_headers):
                    result = await client.runs.wait(
                        session_id,
                        DEEP_MODE_AGENT_ID,
                        command=Command(resume={"decision": "tuning", "tuning_request": user_message}),
                        headers=trace_headers,
                    )
                    if trace_run:
                        trace_run.end(outputs=_summarize_result(result))

                # 检查是否回到 interrupt
                if "__interrupt__" in result:
                    interrupt_value = result["__interrupt__"][0].get("value", {})
                    updated_draft = interrupt_value.get("draft", "")
                    tuning_messages = interrupt_value.get("tuning_messages", [])
                    quality_report = interrupt_value.get("quality_report", {})
                else:
                    updated_draft = result.get("current_draft", "")
                    tuning_messages = result.get("tuning_messages", [])
                    quality_report = result.get("quality_report", {})

                agent_response = (
                    tuning_messages[-1].get("response", "")
                    if tuning_messages else updated_draft
                )

                await websocket.send_json({
                    "type": "tuning_response",
                    "session_id": session_id,
                    "content": agent_response,
                    "agent_response": agent_response,
                    "updated_draft": updated_draft,
                    "quality_report": quality_report,
                    "tuning_messages": tuning_messages,
                })

            elif message_type == "finalize":
                content = data.get("content")
                parent_headers = await _load_trace_headers(client, session_id)

                if content:
                    await client.threads.update_state(
                        session_id,
                        {"current_draft": content},
                        headers=parent_headers or None,
                    )

                async with _child_trace(
                    "finalize",
                    parent_headers,
                    thread_id=session_id,
                    inputs={"has_user_content": bool(content)},
                ) as (trace_run, trace_headers):
                    result = await client.runs.wait(
                        session_id,
                        DEEP_MODE_AGENT_ID,
                        command=Command(resume={"decision": "finalize"}),
                        headers=trace_headers,
                    )
                    if trace_run:
                        trace_run.end(outputs=_summarize_result(result))

                await websocket.send_json({
                    "type": "finalized",
                    "session_id": session_id,
                    "status": "completed",
                    "final_draft": result.get("final_draft", ""),
                })
                break

    except WebSocketDisconnect:
        logger.info(f"[WebSocket] Disconnected: {session_id}")

    except Exception as e:
        logger.error(f"[WebSocket] Error: {e}")
        await websocket.send_json({
            "type": "error",
            "message": str(e),
        })

    finally:
        logger.info(f"[WebSocket] Connection closed: {session_id}")
