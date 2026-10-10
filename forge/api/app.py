from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from forge.api.auth import current_identity, issue_token, password_hash
from forge.api.schemas import (
    FactChangeRequest,
    JobAccepted,
    LoginRequest,
    MessageRequest,
    RegisterRequest,
    TokenResponse,
    WorkflowDecisionRequest,
)
from forge.application.commands import ConversationCommands
from forge.bootstrap import build_runtime
from forge.domain.models import Identity
from forge.infrastructure.telemetry import span, trace_context


def _request_trace_id(request: Request) -> UUID:
    supplied = request.headers.get("x-request-id", "").strip()
    if not supplied:
        return uuid4()
    try:
        return UUID(supplied)
    except ValueError:
        return uuid5(NAMESPACE_URL, supplied)


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime = await build_runtime()
    app.state.runtime = runtime
    app.state.commands = ConversationCommands(
        runtime.database, runtime.store, runtime.jobs
    )
    try:
        yield
    finally:
        await runtime.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Forge", version="2.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.trace_id = _request_trace_id(request)
        with trace_context(trace_id=str(request.state.trace_id)):
            async with span("api.request", category="http", method=request.method) as details:
                response = await call_next(request)
                route = request.scope.get("route")
                details["route"] = route.path if route is not None else "unmatched"
                details["http_status"] = response.status_code
                details["status"] = "failed" if response.status_code >= 500 else "rejected" if response.status_code >= 400 else "succeeded"
        response.headers["X-Trace-ID"] = str(request.state.trace_id)
        return response

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/internal/auth/verify", status_code=status.HTTP_204_NO_CONTENT)
    async def verify_gateway_auth(
        _identity: Annotated[Identity, Depends(current_identity)],
    ) -> None:
        return None

    @app.post("/v1/auth/token", response_model=TokenResponse)
    async def login(body: LoginRequest, request: Request) -> TokenResponse:
        runtime = request.app.state.runtime
        async with runtime.database.pool.acquire() as connection:
            user = await connection.fetchrow(
                "SELECT id, password_hash FROM users WHERE lower(email)=lower($1)", body.email
            )
        if user is None or not password_hash.verify(body.password, user["password_hash"]):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "邮箱或密码错误")
        return TokenResponse(
            access_token=issue_token(
                user_id=user["id"], secret=runtime.settings.jwt_secret,
                issuer=runtime.settings.jwt_issuer, audience=runtime.settings.jwt_audience,
            )
        )

    @app.post("/v1/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
    async def register(body: RegisterRequest, request: Request) -> TokenResponse:
        runtime = request.app.state.runtime
        async with runtime.database.pool.acquire() as connection:
            async with connection.transaction():
                exists = await connection.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM users WHERE lower(email)=lower($1))", body.email
                )
                if exists:
                    raise HTTPException(status.HTTP_409_CONFLICT, "邮箱已注册")
                user_id = await connection.fetchval(
                    "INSERT INTO users (email, password_hash, account_type) VALUES ($1,$2,'guest') RETURNING id",
                    body.email.strip().lower(), password_hash.hash(body.password),
                )
                await connection.execute("SELECT set_config('app.user_id', $1, true)", str(user_id))
                await connection.execute(
                    "INSERT INTO knowledge_spaces (owner_id, scope, name) VALUES ($1,'personal','个人历史知识库')",
                    user_id,
                )
        return TokenResponse(
            access_token=issue_token(
                user_id=user_id, secret=runtime.settings.jwt_secret,
                issuer=runtime.settings.jwt_issuer, audience=runtime.settings.jwt_audience,
            )
        )

    @app.post("/v1/conversations", status_code=status.HTTP_201_CREATED)
    async def create_conversation(
        request: Request, identity: Annotated[Identity, Depends(current_identity)]
    ) -> dict[str, UUID]:
        conversation_id = await request.app.state.commands.create_conversation(identity)
        return {"conversation_id": conversation_id}

    @app.get("/v1/conversations/{conversation_id}")
    async def workspace(
        conversation_id: UUID,
        request: Request,
        identity: Annotated[Identity, Depends(current_identity)],
    ) -> dict:
        try:
            return await request.app.state.runtime.store.workspace(conversation_id, identity)
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.post("/v1/conversations/{conversation_id}/messages", response_model=JobAccepted)
    async def submit_message(
        conversation_id: UUID,
        body: MessageRequest,
        request: Request,
        identity: Annotated[Identity, Depends(current_identity)],
    ) -> JobAccepted:
        try:
            job_id = await request.app.state.commands.submit_message(
                conversation_id=conversation_id,
                identity=identity,
                content=body.content,
                source_text=body.source_text,
                platform=body.platform,
                idempotency_key=body.idempotency_key,
                trace_id=request.state.trace_id,
            )
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return JobAccepted(job_id=job_id)

    @app.patch("/v1/conversations/{conversation_id}/facts")
    async def change_fact(
        conversation_id: UUID,
        body: FactChangeRequest,
        request: Request,
        identity: Annotated[Identity, Depends(current_identity)],
    ) -> dict:
        try:
            return await request.app.state.commands.edit_fact(
                conversation_id=conversation_id,
                identity=identity,
                operation=body.operation,
                fact=body.fact,
                fact_id=body.fact_id,
                expected_version=body.expected_version,
            )
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @app.post("/v1/conversations/{conversation_id}/decisions", response_model=JobAccepted)
    async def resolve_workflow_pause(
        conversation_id: UUID,
        body: WorkflowDecisionRequest,
        request: Request,
        identity: Annotated[Identity, Depends(current_identity)],
    ) -> JobAccepted:
        try:
            await request.app.state.commands.resume_pause(
                conversation_id=conversation_id, job_id=body.job_id, identity=identity,
                decision=body.decision, expected_fact_version=body.fact_version,
            )
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return JobAccepted(job_id=body.job_id, status="pending")

    @app.post("/v1/conversations/{conversation_id}/revise", response_model=JobAccepted)
    async def revise_from_facts(
        conversation_id: UUID,
        request: Request,
        identity: Annotated[Identity, Depends(current_identity)],
    ) -> JobAccepted:
        try:
            job_id = await request.app.state.commands.revise_from_facts(
                conversation_id=conversation_id, identity=identity
            )
        except (LookupError, ValueError, RuntimeError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return JobAccepted(job_id=job_id)

    @app.post("/v1/conversations/{conversation_id}/finalize", response_model=JobAccepted)
    async def finalize(
        conversation_id: UUID,
        request: Request,
        identity: Annotated[Identity, Depends(current_identity)],
    ) -> JobAccepted:
        try:
            job_id = await request.app.state.commands.finalize(
                conversation_id=conversation_id, identity=identity
            )
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return JobAccepted(job_id=job_id)

    @app.get("/v1/conversations/{conversation_id}/jobs/{job_id}/review")
    async def review_job(
        conversation_id: UUID, job_id: UUID, request: Request,
        identity: Annotated[Identity, Depends(current_identity)], after: int = 0,
    ) -> dict:
        try:
            return await request.app.state.runtime.telemetry.review(conversation_id, job_id, identity, after=after)
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.get("/v1/conversations/{conversation_id}/trace")
    async def trace_timeline(
        conversation_id: UUID, request: Request,
        identity: Annotated[Identity, Depends(current_identity)], after: int = 0,
    ) -> dict:
        try:
            return await request.app.state.runtime.telemetry.timeline(conversation_id, identity, after=after)
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.get("/v1/conversations/{conversation_id}/events")
    async def events(
        conversation_id: UUID,
        request: Request,
        identity: Annotated[Identity, Depends(current_identity)],
        after: int = 0,
    ) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            cursor = after
            while True:
                if await request.is_disconnected():
                    return
                async with request.app.state.runtime.database.authorized(identity) as connection:
                    rows = await connection.fetch(
                        """
                        SELECT e.sequence, e.event_type, e.payload, e.created_at
                        FROM workflow_events e JOIN conversations c ON c.id=e.conversation_id
                        WHERE e.conversation_id=$1 AND c.owner_id=$2 AND e.sequence>$3
                        ORDER BY e.sequence LIMIT 100
                        """,
                        conversation_id, identity.user_id, cursor,
                    )
                if rows:
                    for row in rows:
                        cursor = row["sequence"]
                        payload = json.dumps(dict(row), ensure_ascii=False, default=str)
                        yield f"id: {cursor}\nevent: {row['event_type']}\ndata: {payload}\n\n"
                else:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


app = create_app()
