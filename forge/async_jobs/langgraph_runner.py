"""驱动一个 LangGraph 执行片段，并以 Checkpoint 作为恢复依据。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from langgraph.types import Command
from langgraph_sdk import get_client

from .state_machine import ExecutionLeaseLost, StaleApproval


@dataclass(frozen=True)
class SegmentOutcome:
    kind: str
    stage: str
    revision_round: int = 0
    interrupt_id: str | None = None
    content_version: str | None = None
    pending_approval: dict[str, Any] | None = None
    result: dict[str, Any] | None = None


class LangGraphRunController:
    """隐藏 LangGraph Server 的启动、加入、恢复、核对和取消细节。"""

    def __init__(self, client=None) -> None:
        self._client = client or get_client(
            url=os.getenv("LANGGRAPH_SERVER_URL", "http://langgraph:2024")
        )
        self._assistant_id = os.getenv("DEEP_MODE_AGENT_ID", "deep_mode_agent")

    async def execute_segment(
        self,
        *,
        task: dict[str, Any],
        event: dict[str, Any],
        approval: dict[str, Any] | None,
        record_run: Callable[[str], Awaitable[bool]],
    ) -> SegmentOutcome:
        thread_id = str(task["thread_id"])
        await self._client.threads.create(
            thread_id=thread_id,
            if_exists="do_nothing",
            metadata={"generation_task_id": task["id"]},
        )

        active_run_id = task.get("active_run_id")
        if active_run_id:
            run = await self._client.runs.get(thread_id, active_run_id)
            if run["status"] in {"pending", "running"}:
                await self._join_with_deadline(task, active_run_id)
                return await self.inspect_thread(thread_id)
            if run["status"] in {"success", "interrupted"}:
                outcome = await self.inspect_thread(thread_id)
                if outcome.kind != "incomplete":
                    return outcome

        before = await self.inspect_thread(thread_id)
        action = event["action"]
        if before.kind in {"completed", "waiting_approval"}:
            if action == "resume" and before.kind == "waiting_approval":
                if before.interrupt_id != event.get("interrupt_id"):
                    raise StaleApproval("Checkpoint 中的 interrupt 已经变化")
                if before.content_version != event.get("content_version"):
                    raise StaleApproval("Checkpoint 中的审批内容版本已经变化")
            elif action != "resume" or before.kind == "completed":
                return before

        run_kwargs: dict[str, Any] = {
            "metadata": {
                "task_id": task["id"],
                "event_id": event["id"],
                "execution_id": event["execution_id"],
                "action": action,
            },
            "multitask_strategy": "reject",
            "on_disconnect": "continue",
            "on_completion": "keep",
            "durability": "sync",
        }
        if action == "start" and not before.result:
            payload = task["payload"]
            source_article = dict(payload.get("source_article", {}))
            if not source_article.get("text"):
                from forge.scraping.article_fetcher import fetch_article_content

                fetched = await fetch_article_content(
                    source_article.get("url") or payload.get("article_id", "")
                )
                if fetched.get("text"):
                    source_article["text"] = fetched["text"]
                if fetched.get("title") and not source_article.get("title"):
                    source_article["title"] = fetched["title"]
            run_kwargs["input"] = {
                "session_id": thread_id,
                "memory_owner_id": payload.get("memory_owner_id") or task["actor_id"],
                "source_article": source_article,
                "user_input": payload.get("user_input", ""),
                "target_platform": payload.get("target_platform", "zhihu_article"),
            }
        elif action == "resume":
            if approval is None:
                raise StaleApproval("恢复事件缺少审批记录")
            run_kwargs["command"] = Command(resume=approval["payload"])
        else:
            run_kwargs["input"] = None

        run = await self._client.runs.create(
            thread_id,
            self._assistant_id,
            **run_kwargs,
        )
        run_id = str(run["run_id"])
        if not await record_run(run_id):
            await self.cancel_run(thread_id, run_id)
            raise ExecutionLeaseLost("记录 LangGraph run 前执行租约已失效")
        await self._join_with_deadline(task, run_id)
        return await self.inspect_thread(thread_id)

    async def inspect_thread(self, thread_id: str) -> SegmentOutcome:
        try:
            state = await self._client.threads.get_state(thread_id)
        except Exception as exc:
            if "not found" in str(exc).lower() or "404" in str(exc):
                return SegmentOutcome("incomplete", "queued")
            raise
        values = state.get("values") or {}
        if not isinstance(values, dict):
            values = {}
        interrupt = self._find_interrupt(state)
        revision_round = self._revision_round(values)
        if interrupt is not None:
            interrupt_id = str(interrupt.get("id") or "")
            value = interrupt.get("value") or {}
            if not isinstance(value, dict):
                value = {"value": value}
            stage = str(
                value.get("stage")
                or values.get("stage")
                or self._stage_for_interrupt(value.get("type"))
            )
            content_version = self._content_version(state, interrupt_id, value)
            return SegmentOutcome(
                "waiting_approval",
                stage,
                revision_round,
                interrupt_id,
                content_version,
                value,
                values,
            )
        if not state.get("next") and (
            values.get("stage") == "completed" or values.get("final_draft")
        ):
            return SegmentOutcome(
                "completed",
                "completed",
                revision_round,
                result=self._result_projection(thread_id, values),
            )
        return SegmentOutcome(
            "incomplete",
            str(values.get("stage") or "running"),
            revision_round,
            result=values or None,
        )

    async def cancel_run(self, thread_id: str, run_id: str | None) -> None:
        if not run_id:
            return
        try:
            run = await self._client.runs.get(thread_id, run_id)
            if run["status"] in {"pending", "running"}:
                await self._client.runs.cancel(
                    thread_id,
                    run_id,
                    wait=True,
                    action="interrupt",
                )
        except Exception as exc:
            if "not found" not in str(exc).lower() and "404" not in str(exc):
                raise

    async def _join_with_deadline(self, task: dict[str, Any], run_id: str) -> None:
        deadline = task.get("execution_deadline_at")
        timeout = None
        if deadline:
            parsed = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
            timeout = max(0.1, (parsed - datetime.now(timezone.utc)).total_seconds())
        try:
            if timeout is None:
                await self._client.runs.join(str(task["thread_id"]), run_id)
            else:
                await asyncio.wait_for(
                    self._client.runs.join(str(task["thread_id"]), run_id),
                    timeout=timeout,
                )
        except asyncio.TimeoutError:
            await self.cancel_run(str(task["thread_id"]), run_id)
            raise TimeoutError("生成任务超过执行时间上限")

    @staticmethod
    def _find_interrupt(state: dict[str, Any]) -> dict[str, Any] | None:
        interrupts = state.get("interrupts") or []
        if interrupts:
            return interrupts[0]
        for task in state.get("tasks") or []:
            task_interrupts = task.get("interrupts") or []
            if task_interrupts:
                return task_interrupts[0]
        return None

    @staticmethod
    def _content_version(
        state: dict[str, Any], interrupt_id: str, value: dict[str, Any]
    ) -> str:
        checkpoint = state.get("checkpoint") or {}
        source = json.dumps(
            {
                "checkpoint_id": checkpoint.get("checkpoint_id"),
                "interrupt_id": interrupt_id,
                "value": value,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    @staticmethod
    def _revision_round(values: dict[str, Any]) -> int:
        report = values.get("quality_report") or {}
        return int(report.get("revision_cycles") or values.get("revision_round") or 0)

    @staticmethod
    def _stage_for_interrupt(interrupt_type: Any) -> str:
        return {
            "fact_approval": "waiting_facts",
            "outline_approval": "waiting_outline",
            "content_approval": "waiting_content",
        }.get(str(interrupt_type), "waiting_approval")

    @staticmethod
    def _result_projection(thread_id: str, values: dict[str, Any]) -> dict[str, Any]:
        return {
            "thread_id": thread_id,
            "stage": values.get("stage", "completed"),
            "final_draft": values.get("final_draft") or values.get("current_draft", ""),
            "outline": values.get("outline", ""),
            "outline_version": values.get("outline_version", 0),
            "quality_report": values.get("quality_report", {}),
        }
