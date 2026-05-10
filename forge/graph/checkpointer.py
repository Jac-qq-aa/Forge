"""LangGraph Checkpointer - 使用官方 langgraph-checkpoint-postgres。

为 Unified Workflow 提供状态持久化支持：
- AsyncPostgresSaver: 官方实现，自动处理 checkpoint 内部字段
- 专用表结构: checkpoints, checkpoint_writes, checkpoint_blobs, checkpoint_migrations
- 连接池管理: psycopg_pool.AsyncConnectionPool

使用方式：
```python
from forge.graph.checkpointer import get_checkpointer

# 获取 checkpointer（首次调用会初始化）
checkpointer = await get_checkpointer()

# 编译 workflow
workflow = graph.compile(checkpointer=checkpointer, interrupt_before=[...])
```
"""

import logging
from typing import Optional

import psycopg
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from forge.config import PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DATABASE

logger = logging.getLogger(__name__)

# 全局连接池和 checkpointer
_pool: Optional[AsyncConnectionPool] = None
_checkpointer: Optional[AsyncPostgresSaver] = None


def get_connection_string() -> str:
    """构建 PostgreSQL 连接字符串。"""
    return f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"


async def get_checkpointer() -> AsyncPostgresSaver:
    """获取 AsyncPostgresSaver 单例。

    首次调用时会初始化连接池和表结构。

    Returns:
        AsyncPostgresSaver 实例
    """
    global _pool, _checkpointer

    if _checkpointer is None:
        conn_string = get_connection_string()

        # 创建连接池（open=False，延迟打开）
        _pool = AsyncConnectionPool(
            conninfo=conn_string,
            min_size=2,
            max_size=10,
            open=False,
        )

        # 打开连接池
        await _pool.open()
        logger.info(f"[Checkpointer] Connection pool opened: {PG_HOST}:{PG_PORT}/{PG_DATABASE}")

        # 创建 checkpointer
        _checkpointer = AsyncPostgresSaver(_pool)

        # 初始化表结构
        # 注意：CREATE INDEX CONCURRENTLY 不能在事务内运行
        # 使用 autocommit 连接执行 setup
        try:
            async with await psycopg.AsyncConnection.connect(
                conn_string,
                autocommit=True,  # autocommit 模式，不在事务内
            ) as conn:
                # 手动执行 migrations
                await _run_migrations(conn)
            logger.info("[Checkpointer] Tables initialized")
        except Exception as e:
            logger.warning(f"[Checkpointer] Setup failed (tables may already exist): {e}")

    return _checkpointer


async def _run_migrations(conn: psycopg.AsyncConnection) -> None:
    """运行数据库 migrations。

    Args:
        conn: autocommit 模式的异步连接
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with conn.cursor() as cur:
        # 检查当前版本
        try:
            result = await cur.execute(
                "SELECT v FROM checkpoint_migrations ORDER BY v DESC LIMIT 1"
            )
            row = await result.fetchone()
            version = row["v"] if row else -1
        except psycopg.errors.UndefinedTable:
            # 表不存在，从头开始
            version = -1

        # 执行未执行的 migrations
        migrations = AsyncPostgresSaver.MIGRATIONS
        for v in range(version + 1, len(migrations)):
            migration = migrations[v]

            # Migration 5 是空的（SELECT 1），跳过
            if migration.strip() == "SELECT 1;":
                await cur.execute("INSERT INTO checkpoint_migrations (v) VALUES (%s)", (v))
                continue

            await cur.execute(migration)
            await cur.execute("INSERT INTO checkpoint_migrations (v) VALUES (%s)", (v))
            logger.debug(f"[Checkpointer] Migration {v} executed")

    logger.info(f"[Checkpointer] Migrations complete (version {len(migrations) - 1})")


async def close_checkpointer():
    """关闭连接池。

    应用关闭时调用，清理资源。
    """
    global _pool, _checkpointer

    if _pool is not None:
        await _pool.close()
        logger.info("[Checkpointer] Connection pool closed")

    _pool = None
    _checkpointer = None


# ============================================================================
# 导出
# ============================================================================

__all__ = [
    "get_checkpointer",
    "close_checkpointer",
    "get_connection_string",
]