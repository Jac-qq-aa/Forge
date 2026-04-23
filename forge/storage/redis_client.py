# forge/storage/redis_client.py

"""Redis 客户端 - 活跃会话缓存层。"""

import json
import logging
import redis.asyncio as redis
from typing import Optional, Dict, Any, List
from datetime import datetime

from forge.config import (
    REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB,
    SESSION_TTL_SECONDS
)

logger = logging.getLogger(__name__)

# 全局 Redis 连接池
_redis_pool: Optional[redis.ConnectionPool] = None


def get_redis_pool() -> redis.ConnectionPool:
    """获取 Redis 连接池（单例）。"""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            db=REDIS_DB,
            decode_responses=True,
        )
        logger.info(f"[Redis] Connection pool created: {REDIS_HOST}:{REDIS_PORT}")
    return _redis_pool


async def get_redis_client() -> redis.Redis:
    """获取 Redis 客户端实例。"""
    return redis.Redis(connection_pool=get_redis_pool())


async def close_redis_pool():
    """关闭 Redis 连接池。"""
    global _redis_pool
    if _redis_pool:
        await _redis_pool.disconnect()
        _redis_pool = None
        logger.info("[Redis] Connection pool closed")


class RedisSessionManager:
    """Redis 会话管理器。"""

    def __init__(self):
        self._client: Optional[redis.Redis] = None

    async def _get_client(self) -> redis.Redis:
        if self._client is None:
            self._client = await get_redis_client()
        return self._client

    # ---- Session 操作 ----

    async def create_session(
        self,
        session_id: str,
        data: Dict[str, Any],
        ttl: int = SESSION_TTL_SECONDS
    ) -> bool:
        """创建会话，设置 TTL。"""
        client = await self._get_client()
        key = f"session:{session_id}"

        # 序列化 JSONB 字段
        serialized = self._serialize_data(data)

        await client.hset(key, mapping=serialized)
        await client.expire(key, ttl)
        logger.info(f"[Redis] Session created: {session_id}")
        return True

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话数据。"""
        client = await self._get_client()
        key = f"session:{session_id}"

        data = await client.hgetall(key)
        if not data:
            return None

        return self._deserialize_data(data)

    async def update_session(
        self,
        session_id: str,
        data: Dict[str, Any],
        refresh_ttl: bool = True
    ) -> bool:
        """更新会话，可选刷新 TTL。"""
        client = await self._get_client()
        key = f"session:{session_id}"

        serialized = self._serialize_data(data)
        await client.hset(key, mapping=serialized)

        if refresh_ttl:
            await client.expire(key, SESSION_TTL_SECONDS)

        return True

    async def delete_session(self, session_id: str) -> bool:
        """删除会话。"""
        client = await self._get_client()
        key = f"session:{session_id}"
        msg_key = f"session:{session_id}:messages"

        await client.delete(key, msg_key)
        logger.info(f"[Redis] Session deleted: {session_id}")
        return True

    async def exists_session(self, session_id: str) -> bool:
        """检查会话是否存在。"""
        client = await self._get_client()
        key = f"session:{session_id}"
        return await client.exists(key) > 0

    async def refresh_ttl(self, session_id: str) -> bool:
        """刷新会话 TTL（心跳）。"""
        client = await self._get_client()
        key = f"session:{session_id}"
        return await client.expire(key, SESSION_TTL_SECONDS)

    # ---- Messages 操作 ----

    async def append_message(
        self,
        session_id: str,
        message: Dict[str, Any]
    ) -> bool:
        """追加消息到队列。"""
        client = await self._get_client()
        key = f"session:{session_id}:messages"

        await client.rpush(key, json.dumps(message))
        await client.expire(key, SESSION_TTL_SECONDS)
        return True

    async def get_messages(
        self,
        session_id: str,
        start: int = 0,
        end: int = -1
    ) -> List[Dict[str, Any]]:
        """获取消息列表。"""
        client = await self._get_client()
        key = f"session:{session_id}:messages"

        raw_messages = await client.lrange(key, start, end)
        return [json.loads(m) for m in raw_messages]

    async def clear_messages(self, session_id: str) -> bool:
        """清空消息队列。"""
        client = await self._get_client()
        key = f"session:{session_id}:messages"
        await client.delete(key)
        return True

    # ---- 序列化/反序列化 ----

    def _serialize_data(self, data: Dict[str, Any]) -> Dict[str, str]:
        """序列化数据为 Redis Hash 格式。"""
        result = {}
        for key, value in data.items():
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                result[key] = json.dumps(value)
            elif isinstance(value, datetime):
                result[key] = value.isoformat()
            else:
                result[key] = str(value)
        return result

    def _deserialize_data(self, data: Dict[str, str]) -> Dict[str, Any]:
        """反序列化 Redis Hash 数据。"""
        result = {}
        json_fields = [
            "source_article", "outline", "metadata",
            "tuning_history", "rag_context"
        ]

        for key, value in data.items():
            if key in json_fields and value:
                try:
                    result[key] = json.loads(value)
                except json.JSONDecodeError:
                    result[key] = value
            elif key == "is_active" and value:
                result[key] = value.lower() == "true"
            elif key in ("outline_version", "lock_version") and value:
                result[key] = int(value)
            else:
                result[key] = value

        return result


# 全局实例
_redis_session_manager: Optional[RedisSessionManager] = None


def get_redis_session_manager() -> RedisSessionManager:
    """获取 Redis 会话管理器实例。"""
    global _redis_session_manager
    if _redis_session_manager is None:
        _redis_session_manager = RedisSessionManager()
    return _redis_session_manager