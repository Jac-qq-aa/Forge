"""Gateway API - 纯透传代理，使用 LangGraph SDK 调用 LangGraph Server。

架构：
┌─────────────────┐    ┌─────────────────────────────┐
│   Gateway API   │───▶│   LangGraph Server (2024)   │
│   (FastAPI)     │SDK │   deep_mode_agent           │
│   (8001)        │    │   Thread 视角观察            │
│   纯透传代理     │    │   LangSmith / Studio        │
└─────────────────┘    └─────────────────────────────┘

简化设计：
- Gateway 不创建 trace，不存储 headers
- 直接透传 SDK 调用，只认 thread_id
- 观察视角：LangSmith Threads 或 LangGraph Studio

Thread 视角：
- create_session、outline_action、finalize 产生独立 Trace
- 通过 Thread 视角按时间串联完整业务流
- 在 LangSmith Threads 列表搜索 thread_id
- 或在 LangGraph Studio 输入 thread_id
"""

import logging
from typing import Dict, Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from langgraph_sdk import get_client
from langgraph_sdk.schema import Command

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

# ============================================================================
# LangGraph SDK Client
# ============================================================================

LANGGRAPH_SERVER_URL = "http://localhost:2024"
_client = None


def get_langgraph_client():
    """获取 LangGraph SDK Client。"""
    global _client
    if _client is None:
        _client = get_client(url=LANGGRAPH_SERVER_URL)
        logger.info(f"[Gateway] LangGraph SDK client initialized: {LANGGRAPH_SERVER_URL}")
    return _client


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
    description="Gateway for LangGraph Server - 纯透传代理，Thread 视角观察",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# API Routes - 纯透传
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
    """创建深度生成会话 - 纯透传。"""
    client = get_langgraph_client()

    try:
        # 创建 thread
        thread = await client.threads.create()
        thread_id = thread["thread_id"]
        logger.info(f"[Gateway] Thread created: {thread_id}")

        # 运行到第一个 interrupt
        result = await client.runs.wait(
            thread_id,
            "deep_mode_agent",
            input={
                "session_id": thread_id,
                "source_article": request.source_article,
                "user_input": request.user_input or "",
            },
        )

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
    """大纲操作 - 纯透传。"""
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

        # Resume 执行
        result = await client.runs.wait(
            thread_id,
            "deep_mode_agent",
            command=Command(resume=resume_value),
        )

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
        state = await client.threads.get_state(thread_id)
        values = state.get("values", {})
        outline_version = request.outline_version
        if outline_version is None:
            outline_version = values.get("outline_version", 0) + 1

        await client.threads.update_state(
            thread_id,
            {
                "outline": request.outline,
                "outline_version": outline_version,
            },
        )

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
    """定稿会话 - 纯透传。"""
    client = get_langgraph_client()
    thread_id = request.session_id

    try:
        if request.content:
            await client.threads.update_state(thread_id, {"current_draft": request.content})

        # Resume 执行 finalize
        result = await client.runs.wait(
            thread_id,
            "deep_mode_agent",
            command=Command(resume={"decision": "finalize"}),
        )

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
# WebSocket for Tuning - 纯透传
# ============================================================================

@app.websocket("/ws/deep_mode/{session_id}")
async def websocket_tuning(websocket: WebSocket, session_id: str):
    """WebSocket 用于实时微调交互 - 纯透传。"""
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

                # Resume tuning
                result = await client.runs.wait(
                    session_id,
                    "deep_mode_agent",
                    command=Command(resume={"decision": "tuning", "tuning_request": user_message}),
                )

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

                if content:
                    await client.threads.update_state(session_id, {"current_draft": content})

                result = await client.runs.wait(
                    session_id,
                    "deep_mode_agent",
                    command=Command(resume={"decision": "finalize"}),
                )

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
