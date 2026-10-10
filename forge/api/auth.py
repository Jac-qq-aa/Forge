from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash

from forge.domain.models import Identity

bearer = HTTPBearer(auto_error=False)
password_hash = PasswordHash.recommended()


def issue_token(*, user_id: UUID, secret: str, issuer: str, audience: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": str(user_id), "iss": issuer, "aud": audience, "iat": now, "exp": now + timedelta(hours=8)},
        secret,
        algorithm="HS256",
    )


async def current_identity(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Identity:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少访问令牌")
    settings = request.app.state.runtime.settings
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
        user_id = UUID(payload["sub"])
    except Exception as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "访问令牌无效") from exc

    database = request.app.state.runtime.database
    async with database.pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "SELECT set_config('app.user_id', $1, true)", str(user_id)
            )
            user = await connection.fetchrow(
                "SELECT id, account_type FROM users WHERE id=$1", user_id
            )
            if user is None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号不存在")
            membership = await connection.fetchrow(
                """
                SELECT tenant_id, role FROM memberships
                WHERE user_id=$1 AND active ORDER BY tenant_id LIMIT 1
                """,
                user_id,
            )
            spaces = await connection.fetch("SELECT id FROM knowledge_spaces")
    return Identity(
        user_id=user_id,
        tenant_id=membership["tenant_id"] if membership else None,
        account_type=user["account_type"],
        roles=frozenset({membership["role"]}) if membership else frozenset(),
        knowledge_space_ids=frozenset(row["id"] for row in spaces),
    )
