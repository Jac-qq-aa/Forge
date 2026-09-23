"""为本地联调或运维生成 Forge HS256 访问令牌。"""

from __future__ import annotations

import argparse
import os
import time

import jwt
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="生成 Forge 访问令牌")
    parser.add_argument("--subject", required=True, help="用户唯一标识")
    parser.add_argument("--tenant", required=True, help="租户唯一标识")
    parser.add_argument("--scope", default="jobs", help="空格分隔的权限")
    parser.add_argument("--ttl", type=int, default=3600, help="有效期秒数")
    args = parser.parse_args()

    secret = os.getenv("FORGE_JWT_SECRET", "")
    if len(secret) < 32:
        raise SystemExit("FORGE_JWT_SECRET 必须至少为 32 字节")
    if args.ttl <= 0:
        raise SystemExit("--ttl 必须大于 0")

    now = int(time.time())
    token = jwt.encode(
        {
            "sub": args.subject,
            "tenant_id": args.tenant,
            "scope": args.scope,
            "iat": now,
            "exp": now + args.ttl,
            "iss": os.getenv("FORGE_JWT_ISSUER", "forge-auth"),
            "aud": os.getenv("FORGE_JWT_AUDIENCE", "forge-api"),
        },
        secret,
        algorithm="HS256",
    )
    print(token)


if __name__ == "__main__":
    main()
