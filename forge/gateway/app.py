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
from typing import AsyncIterator, Dict, Any, Optional, Tuple
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import langsmith as ls
from langgraph_sdk import get_client
from langgraph_sdk.schema import Command

from dotenv import load_dotenv
load_dotenv()

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
    if not url:
        return {"title": "", "text": ""}

    # 判断平台
    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    if "zhihu.com" in domain:
        from forge.tools.zhihu_scraper import ZhihuScraper
        try:
            scraper = ZhihuScraper(headless=True)
            async with scraper:
                result = await scraper.scrape_article(url)
                return {
                    "title": result.get("title", ""),
                    "text": result.get("text", ""),
                }
        except Exception as e:
            logger.warning(f"[Gateway] Zhihu scrape failed: {e}")
            return {"title": "", "text": ""}

    elif "mp.weixin.qq.com" in domain or "weixin.qq.com" in domain:
        from forge.tools.wechat_scraper import WechatScraper
        try:
            scraper = WechatScraper(headless=True)
            async with scraper:
                result = await scraper.scrape_article(url)
                return {
                    "title": result.get("title", ""),
                    "text": result.get("text", ""),
                }
        except Exception as e:
            logger.warning(f"[Gateway] WeChat scrape failed: {e}")
            return {"title": "", "text": ""}

    else:
        logger.warning(f"[Gateway] Unknown platform for URL: {url}")
        return {"title": "", "text": ""}

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


class OutlineActionRequest(BaseModel):
    session_id: str
    action: str  # "accept" / "modify"
    modification: Optional[str] = None


class FinalizeRequest(BaseModel):
    session_id: str
    content: Optional[str] = None


class UpdateOutlineRequest(BaseModel):
    session_id: str
    outline: str
    outline_version: Optional[int] = None


# ============================================================================
# FastAPI App
# ============================================================================

app = FastAPI(
    title="Forge Gateway API",
    description="Gateway for LangGraph Server with LangSmith trace stitching",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# API Routes
# ============================================================================

@app.get("/")
async def root():
    return {"message": "Forge Gateway API", "langgraph_server": LANGGRAPH_SERVER_URL}


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


@app.post("/api/deep_mode/create_session")
async def create_session(request: CreateSessionRequest):
    """创建深度生成会话。"""
    client = get_langgraph_client()

    try:
        # 创建 thread
        thread = await client.threads.create()
        thread_id = thread["thread_id"]
        logger.info(f"[Gateway] Thread created: {thread_id}")

        # 如果 source_article.text 为空，自动抓取内容
        source_article = dict(request.source_article)
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
            "source_article": source_article,
            "user_input": request.user_input or "",
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


@app.post("/api/deep_mode/outline_action")
async def outline_action(request: OutlineActionRequest):
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

            if interrupt_type == "content_approval":
                return {
                    "session_id": thread_id,
                    "stage": "tuning",
                    "draft": interrupt_value.get("draft", ""),
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


@app.post("/api/deep_mode/update_outline")
async def update_outline(request: UpdateOutlineRequest):
    """同步用户编辑后的大纲到 LangGraph 线程状态。"""
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


@app.post("/api/deep_mode/finalize")
async def finalize_session(request: FinalizeRequest):
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


@app.get("/api/deep_mode/session/{session_id}")
async def get_session(session_id: str):
    """获取会话状态。"""
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
    await websocket.accept()
    logger.info(f"[WebSocket] Connection established: {session_id}")

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
                else:
                    updated_draft = result.get("current_draft", "")

                await websocket.send_json({
                    "type": "tuning_response",
                    "session_id": session_id,
                    "content": updated_draft,
                    "updated_draft": updated_draft,
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
