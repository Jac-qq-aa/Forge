# forge/deep_mode/websocket_handler.py

"""WebSocket 消息处理器 - 使用 LangGraph HITL StateGraph。"""

import logging
import json
from datetime import datetime
from fastapi import WebSocket, WebSocketDisconnect

from forge.deep_mode.session_manager import get_session_manager
from forge.deep_mode.graph_hil import approve_content, get_current_state

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

        # 检查session是否存在
        if session is None:
            logger.error(f"[WebSocket] Session not found: {session_id}")
            await websocket.send_json({
                "type": "error",
                "message": f"Session not found: {session_id}",
            })
            await websocket.close()
            return

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
                # 处理用户微调请求（通过 HITL StateGraph）
                user_message = data.get("content", "")
                logger.info(f"[WebSocket] User tuning request: {user_message[:50]}...")

                try:
                    # 使用 HITL StateGraph 的 approve_content 进入 tuning 节点
                    result = await approve_content(
                        thread_id=session_id,
                        tuning_request=user_message,
                    )

                    # 获取更新后的状态
                    current_state = await get_current_state(session_id)
                    updated_draft = current_state.get("current_draft", "")
                    tuning_messages = current_state.get("tuning_messages", [])

                    # 判断响应类型（基于最后一条 tuning message）
                    is_question_response = False
                    display_response = updated_draft

                    if tuning_messages:
                        last_msg = tuning_messages[-1]
                        if last_msg.get("is_question"):
                            is_question_response = True
                            display_response = last_msg.get("response", "")
                            # 移除【回答】前缀
                            if display_response.startswith("【回答】"):
                                display_response = display_response[4:]

                    logger.info(f"[WebSocket] Tuning done: is_question={is_question_response}, draft_len={len(updated_draft)}")

                    # 更新会话状态（同步到 session_manager）
                    new_history = session.get("tuning_history", [])
                    new_history.append({
                        "role": "user",
                        "content": user_message,
                        "timestamp": datetime.now().isoformat(),
                    })
                    new_history.append({
                        "role": "agent",
                        "content": display_response,
                        "is_question": is_question_response,
                        "timestamp": datetime.now().isoformat(),
                    })

                    session = await session_manager.update_session(
                        session_id,
                        current_draft=updated_draft,
                        tuning_history=new_history,
                        stage=current_state.get("stage", "tuning"),
                    )

                    # 发送响应
                    await websocket.send_json({
                        "type": "tuning_response",
                        "session_id": session_id,
                        "content": display_response,
                        "is_question": is_question_response,
                        "updated_draft": updated_draft,
                    })

                    await session_manager.heartbeat(session_id)

                except Exception as e:
                    logger.error(f"[WebSocket] Tuning failed: {e}")
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Tuning failed: {e}",
                    })

            elif message_type == "finalize":
                # 定稿（通过 HITL StateGraph）
                user_content = data.get("content")

                try:
                    # 如果用户提供了编辑内容，先更新状态
                    if user_content:
                        logger.info(f"[WebSocket] Finalize with user edited content: {len(user_content)} chars")
                        session = await session_manager.update_session(
                            session_id,
                            current_draft=user_content
                        )

                    # 使用 HITL StateGraph 的 approve_content 定稿
                    result = await approve_content(
                        thread_id=session_id,
                        tuning_request=None,  # 不传 tuning_request 表示定稿
                    )

                    # 获取最终状态
                    final_state = await get_current_state(session_id)
                    final_draft = final_state.get("final_draft", "") or final_state.get("current_draft", "")

                    # 同步到 session_manager
                    session = await session_manager.finalize_session(
                        session_id,
                        final_draft=user_content or final_draft,
                    )

                    await websocket.send_json({
                        "type": "finalized",
                        "session_id": session_id,
                        "status": "completed",
                        "final_draft": final_draft,
                        "current_draft": user_content or final_draft,
                    })

                    # === 自进化处理 ===
                    try:
                        from forge.evolution import get_quality_knowledge_manager
                        from forge.evaluation.storage import get_evaluation_storage

                        eval_storage = get_evaluation_storage()
                        eval_result = await eval_storage.get_evaluation_result(session_id)

                        quality_kb = get_quality_knowledge_manager()
                        if eval_result and await quality_kb.should_archive_as_quality_case(session, eval_result):
                            tuning_history = session.get("tuning_history", [])
                            case_id = await quality_kb.archive_case(session, tuning_history, eval_result)
                            if case_id:
                                logger.info(f"[WebSocket] Quality case archived: {case_id}")

                    except Exception as e:
                        logger.warning(f"[WebSocket] Evolution processing failed (non-critical): {e}")

                    break

                except Exception as e:
                    logger.error(f"[WebSocket] Finalize failed: {e}")
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Finalize failed: {e}",
                    })

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
