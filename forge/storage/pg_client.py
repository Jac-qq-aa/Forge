# forge/storage/pg_client.py

"""PostgreSQL 客户端 - 持久化存储层。"""

import json
import logging
import asyncpg
from typing import Optional, Dict, Any, List
from datetime import datetime
from uuid import UUID

from forge.config import PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DATABASE

logger = logging.getLogger(__name__)

# 全局连接池
_pg_pool: Optional[asyncpg.Pool] = None


async def get_pg_pool() -> asyncpg.Pool:
    """获取 PostgreSQL 连接池（单例）。"""
    global _pg_pool
    if _pg_pool is None:
        _pg_pool = await asyncpg.create_pool(
            host=PG_HOST,
            port=PG_PORT,
            user=PG_USER,
            password=PG_PASSWORD,
            database=PG_DATABASE,
            min_size=2,
            max_size=10,
        )
        logger.info(f"[PG] Connection pool created: {PG_HOST}:{PG_PORT}/{PG_DATABASE}")
    return _pg_pool


async def close_pg_pool():
    """关闭 PostgreSQL 连接池。"""
    global _pg_pool
    if _pg_pool:
        await _pg_pool.close()
        _pg_pool = None
        logger.info("[PG] Connection pool closed")


class PGSessionManager:
    """PostgreSQL 会话管理器。"""

    # ---- Session 操作 ----

    async def create_session(
        self,
        session_id: str,
        data: Dict[str, Any]
    ) -> bool:
        """创建会话记录。"""
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO sessions (
                    id, source_article, user_input, stage,
                    outline, outline_version, rag_context,
                    current_draft, is_active, last_heartbeat
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                UUID(session_id),
                json.dumps(data.get("source_article", {})),
                data.get("user_input"),
                data.get("stage", "planning"),
                json.dumps(data.get("outline")) if data.get("outline") else None,
                data.get("outline_version", 0),
                data.get("rag_context"),
                data.get("current_draft"),
                True,
                datetime.now(),
            )
        logger.info(f"[PG] Session created: {session_id}")
        return True

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话记录。"""
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM sessions WHERE id = $1
                """,
                UUID(session_id),
            )
        if not row:
            return None
        return self._row_to_dict(row)

    async def update_session(
        self,
        session_id: str,
        data: Dict[str, Any],
        increment_version: bool = False
    ) -> bool:
        """更新会话记录。"""
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            # 乐观锁检查
            if increment_version:
                result = await conn.execute(
                    """
                    UPDATE sessions SET
                        stage = $2,
                        outline = $3,
                        outline_version = $4,
                        current_draft = $5,
                        rag_context = $6,
                        user_input = $7,
                        last_heartbeat = $8,
                        is_active = $9,
                        lock_version = lock_version + 1
                    WHERE id = $1 AND lock_version = $10
                    """,
                    UUID(session_id),
                    data.get("stage"),
                    json.dumps(data.get("outline")) if data.get("outline") else None,
                    data.get("outline_version"),
                    data.get("current_draft"),
                    data.get("rag_context"),
                    data.get("user_input"),
                    datetime.now(),
                    data.get("is_active", True),
                    data.get("lock_version", 1),
                )
            else:
                result = await conn.execute(
                    """
                    UPDATE sessions SET
                        stage = $2,
                        outline = $3,
                        outline_version = $4,
                        current_draft = $5,
                        rag_context = $6,
                        user_input = $7,
                        last_heartbeat = $8,
                        is_active = $9
                    WHERE id = $1
                    """,
                    UUID(session_id),
                    data.get("stage"),
                    json.dumps(data.get("outline")) if data.get("outline") else None,
                    data.get("outline_version"),
                    data.get("current_draft"),
                    data.get("rag_context"),
                    data.get("user_input"),
                    datetime.now(),
                    data.get("is_active", True),
                )
        return result == "UPDATE 1"

    async def finalize_session(
        self,
        session_id: str,
        final_draft: str
    ) -> bool:
        """定稿会话。"""
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE sessions SET
                    stage = 'completed',
                    final_draft = $2,
                    finalized_at = $3,
                    is_active = FALSE
                WHERE id = $1
                """,
                UUID(session_id),
                final_draft,
                datetime.now(),
            )
        logger.info(f"[PG] Session finalized: {session_id}")
        return True

    async def get_active_sessions(self) -> List[Dict[str, Any]]:
        """获取所有活跃会话（用于恢复）。"""
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM sessions
                WHERE is_active = TRUE
                ORDER BY last_heartbeat DESC
                """
            )
        return [self._row_to_dict(row) for row in rows]

    async def get_history_sessions(
        self,
        limit: int = 20,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """获取历史会话列表。"""
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, source_article, stage, created_at, finalized_at
                FROM sessions
                ORDER BY created_at DESC
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
        return [self._row_to_dict(row) for row in rows]

    # ---- Messages 操作 ----

    async def append_message(
        self,
        session_id: str,
        message: Dict[str, Any]
    ) -> bool:
        """追加消息。"""
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO session_messages (
                    session_id, role, content, is_question,
                    token_count, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6)
                """,
                UUID(session_id),
                message.get("role"),
                message.get("content"),
                message.get("is_question", False),
                message.get("token_count"),
                json.dumps(message.get("metadata")) if message.get("metadata") else None,
            )
        return True

    async def get_messages(
        self,
        session_id: str
    ) -> List[Dict[str, Any]]:
        """获取会话所有消息。"""
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, session_id, role, content, is_question, token_count,
                       metadata, created_at
                FROM session_messages
                WHERE session_id = $1
                ORDER BY created_at ASC
                """,
                UUID(session_id),
            )
        return [self._row_to_dict(row) for row in rows]

    # ---- Versions 操作 ----

    async def save_version(
        self,
        session_id: str,
        version: int,
        draft: str,
        token_count: Optional[int] = None
    ) -> bool:
        """保存文章版本。"""
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO session_versions (
                    session_id, version, draft, token_count
                ) VALUES ($1, $2, $3, $4)
                """,
                UUID(session_id),
                version,
                draft,
                token_count,
            )
        logger.info(f"[PG] Version saved: {session_id} v{version}")
        return True

    async def get_versions(
        self,
        session_id: str
    ) -> List[Dict[str, Any]]:
        """获取会话所有版本。"""
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, session_id, version, draft, token_count, created_at
                FROM session_versions
                WHERE session_id = $1
                ORDER BY version ASC
                """,
                UUID(session_id),
            )
        return [self._row_to_dict(row) for row in rows]

    # ---- 辅助方法 ----

    def _row_to_dict(self, row: asyncpg.Record) -> Dict[str, Any]:
        """转换数据库行到字典。"""
        result = dict(row)
        # 处理 UUID
        if "id" in result and isinstance(result["id"], UUID):
            result["id"] = str(result["id"])
        if "session_id" in result and isinstance(result["session_id"], UUID):
            result["session_id"] = str(result["session_id"])
        # 处理 datetime
        for key in ["created_at", "updated_at", "finalized_at", "last_heartbeat"]:
            if key in result and result[key] is not None:
                result[key] = result[key].isoformat()
        return result


# 全局实例
_pg_session_manager: Optional[PGSessionManager] = None


def get_pg_session_manager() -> PGSessionManager:
    """获取 PG 会话管理器实例。"""
    global _pg_session_manager
    if _pg_session_manager is None:
        _pg_session_manager = PGSessionManager()
    return _pg_session_manager