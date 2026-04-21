# forge/deep_mode/websocket_handler.py

"""WebSocket 消息处理器。"""

import logging
import json
from datetime import datetime
from fastapi import WebSocket

from forge.deep_mode.session_manager import get_session_manager
from forge.deep_mode.agents.react_agent import run_react_agent

logger = logging.getLogger(__name__)


async def handle_websocket_connection(websocket: WebSocket, session_id: str):
    """处理 WebSocket 连接。

    消息类型：
    - tuning_message: 用户发送微调请求
    - tuning_response: Agent 返回响应
    - stage_update: 状态变化通知
    - error: 错误消息
    """
    await websocket.accept()
    logger.info(f"[WebSocket] Connection established for session: {session_id}")

    try:
        # 加载会话状态
        session_manager = get_session_manager()
        session = await session_manager.load_session(session_id)

        # 发送当前状态
        await websocket.send_json({
            "type": "stage_update",
            "session_id": session_id,
            "stage": session.get("stage"),
            "current_draft": session.get("current_draft") or session.get("draft_v1", ""),
            "tuning_history": session.get("tuning_history", []),
        })

        # 消息循环
        while True:
            data = await websocket.receive_json()
            message_type = data.get("type")

            if message_type == "tuning_message":
                # 处理用户微调请求
                user_message = data.get("content", "")
                logger.info(f"[WebSocket] User message: {user_message[:50]}...")

                # 运行 ReAct Agent
                result = await run_react_agent(session_id, user_message)

                # 发送响应
                await websocket.send_json({
                    "type": "tuning_response",
                    "session_id": session_id,
                    "content": result["response"],
                    "updated_draft": result["updated_draft"],
                })

            elif message_type == "finalize":
                # 定稿
                session = await session_manager.finalize_session(session_id)
                await websocket.send_json({
                    "type": "finalized",
                    "session_id": session_id,
                    "status": "completed",
                    "final_draft": session.get("final_draft"),
                })
                break  # 结束连接

            elif message_type == "ping":
                # 心跳
                await websocket.send_json({"type": "pong"})

    except Exception as e:
        logger.error(f"[WebSocket] Error: {e}")
        await websocket.send_json({
            "type": "error",
            "message": str(e),
        })

    finally:
        logger.info(f"[WebSocket] Connection closed for session: {session_id}")