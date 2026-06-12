"""LangGraph Server 入口 - 导出编译后的 graph。

LangGraph Server 会调用此模块获取 graph 实例。
使用 checkpointer 支持 HITL interrupt 暂停和恢复。

Trace 设计：
- Gateway 负责 LangSmith trace stitching
- LangGraph Server 接收 Gateway 传入的 tracing headers
"""

import contextlib
import logging
from typing import Any, Dict, List, Optional

import langsmith as ls
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from forge.deep_mode.graph_hil import build_hil_graph
from forge.config import PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DATABASE

logger = logging.getLogger(__name__)

# 全局变量
_pool: AsyncConnectionPool = None
_checkpointer: AsyncPostgresSaver = None
_compiled_graph = None


def get_connection_string() -> str:
    """构建 PostgreSQL 连接字符串。"""
    return f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"


def _normalize_tags(value: Any) -> Optional[List[str]]:
    """LangGraph API may pass LangSmith tags as a list or comma-separated string."""
    if not value:
        return None
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return None


def _normalize_metadata(value: Any) -> Optional[Dict[str, Any]]:
    """LangGraph API usually parses baggage metadata into a dict."""
    if isinstance(value, dict):
        return dict(value)
    return None


def _get_langsmith_context(config: Dict[str, Any]) -> Dict[str, Any]:
    """Extract LangSmith parent trace context forwarded by Gateway.

    LangGraph API forwards the `langsmith-trace` header into config.configurable.
    Without explicitly entering `tracing_context(parent=...)` here, graph/node/LLM
    spans can still appear as independent root traces after interrupt/resume.
    """
    configurable = (config or {}).get("configurable", {}) or {}
    parent_trace = configurable.get("langsmith-trace")
    if not parent_trace:
        return {}

    parent_headers = {"langsmith-trace": str(parent_trace)}
    project_name = configurable.get("langsmith-project")
    tags = _normalize_tags(configurable.get("langsmith-tags"))
    metadata = _normalize_metadata(configurable.get("langsmith-metadata"))

    context: Dict[str, Any] = {"parent": parent_headers}
    if project_name:
        context["project_name"] = str(project_name)
    if tags:
        context["tags"] = tags
    if metadata:
        context["metadata"] = metadata

    return context


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

    Gateway 会传入 LangSmith tracing headers，用于把多次 interrupt/resume 串到同一 trace tree。

    Args:
        config: LangGraph Server 配置

    Returns:
        编译后的 StateGraph，带 checkpointer 支持 HITL
    """
    logger.info(f"[ServerGraph] Serving graph for thread: {config.get('configurable', {}).get('thread_id', 'unknown')}")

    compiled_graph = await get_compiled_graph()
    trace_context = _get_langsmith_context(config)

    if trace_context:
        logger.info(
            "[ServerGraph] Applying LangSmith parent trace context for thread: %s",
            config.get("configurable", {}).get("thread_id", "unknown"),
        )
        with ls.tracing_context(**trace_context):
            yield compiled_graph
    else:
        yield compiled_graph


# 导出给 LangGraph Server 使用
__all__ = ["app"]
