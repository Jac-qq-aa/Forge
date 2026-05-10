"""LangGraph Server 入口 - 导出编译后的 graph。

LangGraph Server 会调用此模块获取 graph 实例。
使用 checkpointer 支持 HITL interrupt 暂停和恢复。

简化设计：
- 不使用 tracing_context 链接 parent trace
- 每次调用自然产生独立 trace
- 观察视角：LangSmith Threads 或 LangGraph Studio
"""

import contextlib
import logging
from typing import AsyncGenerator

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from forge.deep_mode.graph_hil import build_hil_graph, DeepModeHILState
from forge.config import PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DATABASE

logger = logging.getLogger(__name__)

# 全局变量
_pool: AsyncConnectionPool = None
_checkpointer: AsyncPostgresSaver = None
_compiled_graph = None


def get_connection_string() -> str:
    """构建 PostgreSQL 连接字符串。"""
    return f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"


async def get_compiled_graph():
    """获取编译后的 graph（带 checkpointer）。"""
    global _pool, _checkpointer, _compiled_graph

    if _compiled_graph is None:
        # 创建连接池
        conn_string = get_connection_string()
        _pool = AsyncConnectionPool(
            conninfo=conn_string,
            min_size=2,
            max_size=10,
            open=False,
        )

        # 打开连接池
        await _pool.open()
        logger.info(f"[ServerGraph] Connection pool opened: {PG_HOST}:{PG_PORT}/{PG_DATABASE}")

        # 创建 checkpointer
        _checkpointer = AsyncPostgresSaver(_pool)

        # 初始化表结构
        try:
            await _checkpointer.setup()
            logger.info("[ServerGraph] Checkpointer tables initialized")
        except Exception as e:
            logger.warning(f"[ServerGraph] Setup failed (tables may exist): {e}")

        # 构建 graph
        graph = build_hil_graph()

        # 编译 graph
        _compiled_graph = graph.compile(checkpointer=_checkpointer)
        logger.info("[ServerGraph] Graph compiled with checkpointer")

    return _compiled_graph


@contextlib.asynccontextmanager
async def app(config):
    """Factory function - LangGraph Server 会调用此函数获取编译后的 graph。

    简化设计：不使用 tracing_context，让每次调用产生独立 trace。
    观察完整流程请在 LangSmith Threads 或 LangGraph Studio 中按 thread_id 查看。

    Args:
        config: LangGraph Server 配置

    Returns:
        编译后的 StateGraph，带 checkpointer 支持 HITL
    """
    logger.info(f"[ServerGraph] Serving graph for thread: {config.get('configurable', {}).get('thread_id', 'unknown')}")

    compiled_graph = await get_compiled_graph()
    yield compiled_graph


# 导出给 LangGraph Server 使用
__all__ = ["app"]