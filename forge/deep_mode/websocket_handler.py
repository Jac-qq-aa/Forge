# forge/deep_mode/websocket_handler.py

"""WebSocket 消息处理器 - 使用新的 LangGraph Workflow。"""

import logging
import json
from datetime import datetime
from fastapi import WebSocket, WebSocketDisconnect

from forge.deep_mode.session_manager import get_session_manager
from forge.deep_mode.workflow import run_tuning_agent

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

                # 获取当前草稿
                current_draft = session.get("current_draft") or session.get("draft_v1", "")

                # 运行 Tuning Agent
                response = await run_tuning_agent(current_draft, user_message)
                logger.info(f"[WebSocket] Agent response: {response[:200]}...")

                # 判断响应类型
                # 优先检查【回答】标记
                is_question_response = response.startswith("【回答】")

                # 如果没有【回答】标记，但响应很短且不包含完整文章结构，可能也是回答
                if not is_question_response:
                    # 检查是否是回答格式（没有完整文章结构）
                    response_length_ratio = len(response) / len(current_draft) if current_draft else 0
                    # 如果响应明显比原文短（<20%）且不包含换行段落，可能是回答
                    if response_length_ratio < 0.2 and '\n\n' not in response[:500]:
                        logger.info(f"[WebSocket] Short response without article structure, treating as question")
                        is_question_response = True

                if is_question_response:
                    # 提问类响应 - 不更新草稿，只添加对话历史
                    updated_draft = current_draft
                    # 移除【回答】前缀（如果存在）
                    if response.startswith("【回答】"):
                        display_response = response[4:]
                    else:
                        display_response = response
                    logger.info(f"[WebSocket] Question response, not updating draft")
                else:
                    # 修改类响应 - 检查是否是有效修改
                    # 如果返回内容明显比原文短（<30%），可能是片段而非完整文章
                    response_length_ratio = len(response) / len(current_draft) if current_draft else 0

                    if response_length_ratio < 0.3 and len(current_draft) > 200:
                        # 可能只返回了片段，警告并保持原文
                        logger.warning(f"[WebSocket] Response too short ({response_length_ratio:.1%} of original), may be fragment")
                        updated_draft = current_draft
                        display_response = f"⚠️ 修改可能不完整，原文已保留。\n\nAgent 回复：{response}\n\n请尝试更明确地描述修改需求，例如：'请修改第二段，返回完整的修改后文章'"
                    else:
                        # 有效修改，更新草稿
                        updated_draft = response
                        display_response = response

                # 更新会话状态
                new_history = session.get("tuning_history", [])
                new_history.append({
                    "role": "user",
                    "content": user_message,
                    "timestamp": datetime.now().isoformat(),
                })
                new_history.append({
                    "role": "agent",
                    "content": display_response,  # 显示的响应（已去除【回答】前缀）
                    "is_question": is_question_response,  # 标记是否为提问响应
                    "timestamp": datetime.now().isoformat(),
                })

                session = await session_manager.update_session(
                    session_id,
                    current_draft=updated_draft,  # 只有修改类响应才更新
                    tuning_history=new_history,
                )

                # 发送响应
                await websocket.send_json({
                    "type": "tuning_response",
                    "session_id": session_id,
                    "content": display_response,
                    "is_question": is_question_response,  # 前端可据此决定是否更新草稿区
                    "updated_draft": updated_draft,
                })

                # 刷新心跳
                await session_manager.heartbeat(session_id)

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

            elif message_type == "heartbeat":
                # 心跳检测 - 刷新 TTL
                await session_manager.heartbeat(session_id)
                await websocket.send_json({"type": "heartbeat_ack", "session_id": session_id})

            elif message_type == "ping":
                # 兼容旧的心跳
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info(f"[WebSocket] Connection disconnected: {session_id}")
        await session_manager.save_on_disconnect(session_id)

    except Exception as e:
        logger.error(f"[WebSocket] Error: {e}")
        await websocket.send_json({
            "type": "error",
            "message": str(e),
        })

    finally:
        logger.info(f"[WebSocket] Connection closed for session: {session_id}")