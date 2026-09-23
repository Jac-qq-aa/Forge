"""把持久化任务翻译成既有 Gateway 工作流调用。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def execute_workflow_job(job: dict[str, Any]) -> dict[str, Any]:
    operation = job["operation"]
    payload = job["payload"]

    from forge.gateway.app import (
        CreateSessionRequest,
        FactReviewRequest,
        FinalizeRequest,
        OutlineActionRequest,
        execute_create_session,
        execute_fact_action,
        execute_finalize_session,
        execute_outline_action,
    )

    handlers = {
        "create_session": (CreateSessionRequest, execute_create_session),
        "fact_action": (FactReviewRequest, execute_fact_action),
        "outline_action": (OutlineActionRequest, execute_outline_action),
        "finalize": (FinalizeRequest, execute_finalize_session),
    }
    if operation not in handlers:
        raise ValueError(f"不支持的异步工作流操作: {operation}")

    request_type, handler = handlers[operation]
    result = await handler(request_type(**payload))
    await _sync_local_session(operation, payload, result)
    return result


async def _sync_local_session(
    operation: str,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """同步历史会话；失败不影响已完成的 LangGraph 任务结果。"""
    try:
        from forge.deep_mode.session_manager import get_session_manager

        manager = get_session_manager()
        session_id = result.get("session_id") or payload.get("session_id")
        if not session_id:
            return

        if operation == "create_session":
            await manager.create_session(
                source_article=payload.get("source_article", {}),
                user_input=payload.get("user_input", ""),
                session_id=session_id,
            )
        if result.get("stage") == "waiting_facts":
            await manager.update_session(
                session_id,
                stage="waiting_facts",
                research_result=result.get("research_result", {}),
            )
        elif result.get("stage") == "waiting_outline":
            await manager.update_session(
                session_id,
                stage="waiting_outline",
                outline=result.get("outline", ""),
                outline_version=result.get("outline_version", 1),
                research_result=result.get("research_result", {}),
            )
        elif result.get("stage") == "tuning":
            await manager.update_session(
                session_id,
                stage="tuning",
                current_draft=result.get("draft", ""),
                research_result=result.get("research_result", {}),
                quality_report=result.get("quality_report", {}),
            )
        if operation == "finalize":
            final_draft = payload.get("content") or result.get("final_draft", "")
            await manager.finalize_session(session_id, final_draft=final_draft)
            result["current_draft"] = final_draft
            result["final_draft"] = final_draft
    except Exception:
        logger.exception("[AsyncJobs] 同步本地会话失败 operation=%s", operation)
