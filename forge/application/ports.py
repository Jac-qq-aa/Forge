from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, Sequence
from uuid import UUID

from forge.domain.models import ArticleFact, Evidence, Identity


class ModelGateway(Protocol):
    async def structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def text(self, *, system: str, prompt: str) -> str: ...


class EvidenceSearch(Protocol):
    async def web(self, query: str, *, limit: int = 5) -> Sequence[Evidence]: ...

    async def semantic(
        self,
        query: str,
        *,
        identity: Identity,
        limit: int = 8,
        scope: str = "all",
    ) -> Sequence[Evidence]: ...


class ReadOnlyQuery(Protocol):
    async def ask(self, question: str, *, identity: Identity) -> Sequence[Evidence]: ...


class ProgressReporter(Protocol):
    async def report(
        self,
        *,
        conversation_id: UUID,
        identity: Identity,
        stage: str,
        code: str,
        message: str,
    ) -> None: ...


class ArticleStore(Protocol):
    async def create_conversation(self, identity: Identity) -> UUID: ...

    async def append_message(
        self, conversation_id: UUID, identity: Identity, role: str, content: str
    ) -> UUID | None: ...

    async def workspace(self, conversation_id: UUID, identity: Identity) -> dict[str, Any]: ...

    async def apply_fact_change(
        self,
        conversation_id: UUID,
        identity: Identity,
        operation: str,
        fact: ArticleFact | None,
        fact_id: UUID | None,
        expected_version: int,
    ) -> dict[str, Any]: ...

    async def finalize(self, conversation_id: UUID, identity: Identity) -> dict[str, Any]: ...

    async def save_embedding(
        self,
        *,
        chunk_id: UUID,
        vector: list[float],
        model: str,
        dimensions: int,
        identity: Identity,
    ) -> None: ...


class AuthorizedDatabase(Protocol):
    def authorized(self, identity: Identity) -> AbstractAsyncContextManager[Any]: ...


class JobStore(Protocol):
    async def enqueue(
        self,
        *,
        job_id: UUID,
        conversation_id: UUID,
        kind: str,
        payload: dict[str, Any],
        stage: str | None = None,
        actor_id: UUID | None = None,
        trace_id: UUID | None = None,
    ) -> bool: ...

    async def get(self, job_id: UUID) -> dict[str, Any] | None: ...

    async def mark_submitted(
        self,
        *,
        job_id: UUID,
        remote_thread_id: str,
        remote_run_id: str,
        langsmith_trace_id: str = "",
        execution_id: str = "",
    ) -> bool: ...

    async def finish(
        self,
        *,
        job_id: UUID,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool: ...

    async def admit(self, job_id: UUID) -> None: ...

    async def mark_running(self, job_id: UUID) -> None: ...

    async def reconcilable(self, *, limit: int = 100) -> Sequence[dict[str, Any]]: ...


class WorkflowExecutor(Protocol):
    async def find_submission(self, thread_id: str, job_id: UUID) -> dict[str, str] | None: ...
    async def start(self, *, job_id: UUID, payload: dict[str, Any]) -> dict[str, str]: ...
    async def cancel(self, *, thread_id: str, run_id: str) -> None: ...
    async def status(self, *, thread_id: str, run_id: str) -> dict[str, Any]: ...
    async def state(self, *, thread_id: str) -> dict[str, Any]: ...


class MessagePublisher(Protocol):
    async def publish(self, message: dict[str, Any]) -> str: ...

    async def close(self) -> None: ...
