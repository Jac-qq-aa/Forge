"""应用异步任务表迁移。"""

import asyncio
from pathlib import Path

import asyncpg

from forge.config import PG_DATABASE, PG_HOST, PG_PASSWORD, PG_PORT, PG_USER, validate_db_config


async def main() -> None:
    validate_db_config()
    migrations_dir = Path(__file__).resolve().parents[2] / "migrations"
    connection = await asyncpg.connect(
        host=PG_HOST,
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASSWORD,
        database=PG_DATABASE,
    )
    try:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS forge_schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        for sql_path in sorted(migrations_dir.glob("*.sql")):
            applied = await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM forge_schema_migrations WHERE filename = $1)",
                sql_path.name,
            )
            if applied:
                continue
            async with connection.transaction():
                await connection.execute(sql_path.read_text(encoding="utf-8"))
                await connection.execute(
                    "INSERT INTO forge_schema_migrations (filename) VALUES ($1)",
                    sql_path.name,
                )
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
