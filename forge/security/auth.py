"""基于签名 JWT 的租户身份认证。"""

from __future__ import annotations

import os
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request, WebSocket, status


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: str
    scopes: frozenset[str]

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def _jwt_secret() -> str:
    secret = os.getenv("FORGE_JWT_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FORGE_JWT_SECRET 未配置或长度小于 32 字节",
        )
    return secret


def _extract_token_from_headers(authorization: str | None, cookie_token: str | None) -> str:
    if authorization:
        scheme, _, credentials = authorization.partition(" ")
        if scheme.lower() == "bearer" and credentials:
            return credentials
    if cookie_token:
        return cookie_token
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="缺少访问令牌",
        headers={"WWW-Authenticate": "Bearer"},
    )


def decode_access_token(token: str) -> Principal:
    try:
        payload = jwt.decode(
            token,
            _jwt_secret(),
            algorithms=["HS256"],
            audience=os.getenv("FORGE_JWT_AUDIENCE", "forge-api"),
            issuer=os.getenv("FORGE_JWT_ISSUER", "forge-auth"),
            options={"require": ["exp", "iat", "sub", "tenant_id"]},
        )
    except HTTPException:
        raise
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="访问令牌无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    subject = str(payload["sub"])
    tenant_id = str(payload["tenant_id"])
    if not subject or not tenant_id or len(tenant_id) > 128:
        raise HTTPException(status_code=401, detail="访问令牌缺少有效租户身份")

    raw_scopes = payload.get("scope", "")
    if isinstance(raw_scopes, str):
        scopes = frozenset(raw_scopes.split())
    elif isinstance(raw_scopes, list):
        scopes = frozenset(str(item) for item in raw_scopes)
    else:
        scopes = frozenset()
    return Principal(subject=subject, tenant_id=tenant_id, scopes=scopes)


def access_token_from_request(request: Request) -> str:
    return _extract_token_from_headers(
        request.headers.get("Authorization"),
        request.cookies.get("forge_access_token"),
    )


async def require_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        principal = decode_access_token(access_token_from_request(request))
        request.state.principal = principal
    return principal


async def require_admin(
    principal: Principal = Depends(require_principal),
) -> Principal:
    if not principal.has_scope("admin"):
        raise HTTPException(status_code=403, detail="需要 admin 权限")
    return principal


async def authenticate_websocket(websocket: WebSocket) -> Principal | None:
    try:
        token = access_token_from_websocket(websocket)
        return decode_access_token(token)
    except HTTPException:
        await websocket.close(code=4401, reason="未认证")
        return None


def access_token_from_websocket(websocket: WebSocket) -> str:
    return _extract_token_from_headers(
        websocket.headers.get("Authorization"),
        websocket.cookies.get("forge_access_token"),
    )
