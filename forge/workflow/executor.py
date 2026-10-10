from __future__ import annotations

from typing import Any
from uuid import UUID

from langgraph_sdk import get_client
from forge.infrastructure.telemetry import traced
from forge.infrastructure.langsmith_trace import current_headers, current_trace_id


class RemoteLangGraphExecutor:
    """通过 Agent Server 提交 run；调用方不在消息消费期间等待 Graph 完成。"""

    def __init__(
        self,
        *,
        server_url: str,
        assistant_id: str,
        client: Any | None = None,
    ) -> None:
        self._client = client or get_client(url=server_url)
        self._assistant_id = assistant_id

    @traced("langgraph.submit")
    async def start(self, *, job_id: UUID, payload: dict[str, Any]) -> dict[str, str]:
        thread_id = str(payload["conversation_id"])
        await self._client.threads.create(thread_id=thread_id, if_exists="do_nothing")
        existing = await self.find_submission(thread_id, job_id,
                                              execution_id=payload["_execution_id"])
        if existing is not None:
            return existing
        run = await self._client.runs.create(
            thread_id,
            self._assistant_id,
            input=payload,
            metadata={"job_id": str(job_id), "conversation_id": thread_id,
                      "trace_id": payload.get("trace_id"), "execution_id": payload["_execution_id"],
                      "langsmith_trace_id": current_trace_id()},
            config={"configurable": {"forge_job_id": str(job_id), "forge_trace_id": payload.get("trace_id"),
                                     "forge_trace_context": current_headers()}},
            headers=current_headers(),
            multitask_strategy="reject",
        )
        return {"thread_id": thread_id, "run_id": str(run["run_id"]),
                "langsmith_trace_id": current_trace_id() or ""}

    @traced("langgraph.resume")
    async def resume(self, *, job_id: UUID, payload: dict[str, Any]) -> dict[str, str]:
        thread_id = str(payload["conversation_id"])
        await self._client.threads.create(thread_id=thread_id, if_exists="do_nothing")
        run = await self._client.runs.create(
            thread_id, self._assistant_id,
            command={"resume": payload["resume_decision"]},
            metadata={"job_id": str(job_id), "conversation_id": thread_id,
                      "trace_id": payload.get("trace_id"), "resumed": True,
                      "execution_id": payload["_execution_id"], "langsmith_trace_id": current_trace_id()},
            config={"configurable": {"forge_job_id": str(job_id), "forge_trace_id": payload.get("trace_id"),
                                     "forge_trace_context": current_headers()}},
            headers=current_headers(),
            multitask_strategy="reject",
        )
        return {"thread_id": thread_id, "run_id": str(run["run_id"]),
                "langsmith_trace_id": current_trace_id() or ""}

    async def find_submission(
        self, thread_id: str, job_id: UUID, *, execution_id: str
    ) -> dict[str, str] | None:
        for run in await self._client.runs.list(thread_id, limit=50):
            metadata = run.get("metadata") or {}
            if metadata.get("job_id") == str(job_id) and metadata.get("execution_id") == execution_id:
                return {"thread_id": thread_id, "run_id": str(run["run_id"]),
                        "langsmith_trace_id": metadata.get("langsmith_trace_id") or ""}
        return None

    async def cancel(self, *, thread_id: str, run_id: str) -> None:
        await self._client.runs.cancel(thread_id, run_id, wait=False, action="interrupt")

    async def status(self, *, thread_id: str, run_id: str) -> dict[str, Any]:
        return dict(await self._client.runs.get(thread_id, run_id))

    async def state(self, *, thread_id: str) -> dict[str, Any]:
        state = await self._client.threads.get_state(thread_id)
        values = state.get("values") or {}
        if not isinstance(values, dict):
            raise RuntimeError("LangGraph thread state 不是对象")
        return {
            "values": values,
            "interrupts": [dict(item) for item in state.get("interrupts") or []],
            "next": list(state.get("next") or []),
        }

    async def close(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()
