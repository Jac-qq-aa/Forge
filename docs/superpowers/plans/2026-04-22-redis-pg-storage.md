# Redis + PostgreSQL 存储架构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Deep Mode 会话存储从 SQLite 改为 Redis + PostgreSQL 双存储架构，支持多实例共享、高并发性能、历史记录列表功能。

**Architecture:** Redis 作为活跃会话缓存层（30分钟 TTL + AOF 持久化），PostgreSQL 作为持久层存储历史会话、消息和版本。关键节点双写，异常断开时从 PG 恢复。

**Tech Stack:** Redis 5.0+, asyncpg 0.29+, PostgreSQL 14+

---

## 文件结构

```
forge/
├── config.py                         # 新增 Redis/PG 配置
├── storage/
│   ├── __init__.py                   # 新建目录
│   ├── redis_client.py               # Redis 连接管理
│   └── pg_client.py                  # PostgreSQL 连接管理
├── deep_mode/
│   ├── session_manager.py            # 重写：双存储 SessionManager
│   ├── session_state.py              # 更新：数据结构定义
│   ├── websocket_handler.py          # 修改：心跳、断开保存
│   └── workflow.py                   # 修改：适配新接口
└── web/
    └── app.py                        # 修改：历史列表 API

migrations/
└── 001_redis_pg_storage.sql          # 新建：PG 表结构迁移

tests/
└── test_storage/
    ├── test_redis_client.py          # 新建
    └── test_pg_client.py             # 新建
```

---

## Task 1: 添加 Redis 和 PostgreSQL 配置

**Files:**
- Modify: `forge/config.py`

- [ ] **Step 1: 添加 Redis 配置**

```python
# 在 forge/config.py 末尾添加

# ============================================================================
# Redis Configuration
# ============================================================================

REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))

# Session TTL (30 minutes)
SESSION_TTL_SECONDS: int = int(os.getenv("SESSION_TTL_SECONDS", "1800"))

# ============================================================================
# PostgreSQL Configuration
# ============================================================================

PG_HOST: str = os.getenv("PG_HOST", "localhost")
PG_PORT: int = int(os.getenv("PG_PORT", "5432"))
PG_USER: str = os.getenv("PG_USER", "forge")
PG_PASSWORD: str = os.getenv("PG_PASSWORD", "")
PG_DATABASE: str = os.getenv("PG_DATABASE", "forge")
```

- [ ] **Step 2: Commit 配置更改**

```bash
git add forge/config.py
git commit -m "feat: add Redis and PostgreSQL configuration

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: 创建 Redis 客户端模块

**Files:**
- Create: `forge/storage/__init__.py`
- Create: `forge/storage/redis_client.py`
- Create: `tests/test_storage/__init__.py`
- Create: `tests/test_storage/test_redis_client.py`

- [ ] **Step 1: 创建 storage 目录和 __init__.py**

```bash
mkdir -p forge/storage tests/test_storage
touch forge/storage/__init__.py tests/test_storage/__init__.py
```

- [ ] **Step 2: 创建 Redis 客户端模块**

```python
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
```

- [ ] **Step 3: 创建 Redis 测试文件**

```python
# tests/test_storage/test_redis_client.py

"""Redis 客户端测试。"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from forge.storage.redis_client import RedisSessionManager


@pytest.fixture
def redis_manager():
    """创建 Redis 会话管理器。"""
    return RedisSessionManager()


class TestRedisSessionManager:
    """RedisSessionManager 测试。"""

    @pytest.mark.asyncio
    async def test_serialize_data(self, redis_manager):
        """测试序列化。"""
        data = {
            "stage": "tuning",
            "source_article": {"title": "测试", "text": "内容"},
            "outline_version": 2,
            "is_active": True,
        }
        serialized = redis_manager._serialize_data(data)

        assert serialized["stage"] == "tuning"
        assert serialized["source_article"] == '{"title": "测试", "text": "内容"}'
        assert serialized["outline_version"] == "2"
        assert serialized["is_active"] == "True"

    @pytest.mark.asyncio
    async def test_deserialize_data(self, redis_manager):
        """测试反序列化。"""
        data = {
            "stage": "tuning",
            "source_article": '{"title": "测试", "text": "内容"}',
            "outline_version": "2",
            "is_active": "true",
        }
        deserialized = redis_manager._deserialize_data(data)

        assert deserialized["stage"] == "tuning"
        assert deserialized["source_article"] == {"title": "测试", "text": "内容"}
        assert deserialized["outline_version"] == 2
        assert deserialized["is_active"] == True

    @pytest.mark.asyncio
    async def test_create_session(self, redis_manager):
        """测试创建会话。"""
        mock_client = AsyncMock()
        redis_manager._client = mock_client

        await redis_manager.create_session(
            "test-session-1",
            {"stage": "planning", "source_article": {"title": "测试"}}
        )

        mock_client.hset.assert_called_once()
        mock_client.expire.assert_called()

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, redis_manager):
        """测试获取不存在的会话。"""
        mock_client = AsyncMock()
        mock_client.hgetall.return_value = {}
        redis_manager._client = mock_client

        result = await redis_manager.get_session("non-existent")

        assert result is None

    @pytest.mark.asyncio
    async def test_append_and_get_messages(self, redis_manager):
        """测试消息追加和获取。"""
        mock_client = AsyncMock()
        mock_client.lrange.return_value = [
            '{"role": "user", "content": "修改第二段"}',
            '{"role": "agent", "content": "已修改"}',
        ]
        redis_manager._client = mock_client

        await redis_manager.append_message(
            "test-session-1",
            {"role": "user", "content": "修改第二段"}
        )
        mock_client.rpush.assert_called_once()

        messages = await redis_manager.get_messages("test-session-1")
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
```

- [ ] **Step 4: 运行测试验证**

```bash
pytest tests/test_storage/test_redis_client.py -v
```

Expected: PASS（使用 mock，不需要真实 Redis）

- [ ] **Step 5: Commit Redis 客户端模块**

```bash
git add forge/storage/__init__.py forge/storage/redis_client.py tests/test_storage/
git commit -m "feat: add Redis client for active session caching

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: 创建 PostgreSQL 客户端模块

**Files:**
- Create: `forge/storage/pg_client.py`
- Create: `tests/test_storage/test_pg_client.py`
- Create: `migrations/001_redis_pg_storage.sql`

- [ ] **Step 1: 创建 PostgreSQL 迁移脚本**

```sql
-- migrations/001_redis_pg_storage.sql

-- Deep Mode 会话存储迁移

-- 会话元数据表
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_article JSONB NOT NULL,
    user_input TEXT,
    stage VARCHAR(20) NOT NULL,
    outline JSONB,
    outline_version INT DEFAULT 0,
    rag_context TEXT,
    current_draft TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    last_heartbeat TIMESTAMP,
    lock_version INT DEFAULT 1,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    finalized_at TIMESTAMP,
    final_draft TEXT
);

-- 消息历史表
CREATE TABLE IF NOT EXISTS session_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    role VARCHAR(10) NOT NULL,
    content TEXT NOT NULL,
    is_question BOOLEAN DEFAULT FALSE,
    token_count INT,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 文章版本表
CREATE TABLE IF NOT EXISTS session_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    version INT NOT NULL,
    draft TEXT NOT NULL,
    token_count INT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_sessions_stage ON sessions(stage);
CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(is_active, last_heartbeat);
CREATE INDEX IF NOT EXISTS idx_messages_session ON session_messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_versions_session ON session_versions(session_id, version);

-- 更新触发器
CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER sessions_update_ts
    BEFORE UPDATE ON sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_timestamp();
```

- [ ] **Step 2: 创建 PostgreSQL 客户端模块**

```python
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
        _pg_pool = asyncpg.create_pool(
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

    async def _get_conn(self) -> asyncpg.Connection:
        pool = await get_pg_pool()
        return pool.acquire()

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
                        last_heartbeat = $6,
                        is_active = $7,
                        lock_version = lock_version + 1
                    WHERE id = $1 AND lock_version = $8
                    """,
                    UUID(session_id),
                    data.get("stage"),
                    json.dumps(data.get("outline")) if data.get("outline") else None,
                    data.get("outline_version"),
                    data.get("current_draft"),
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
                        last_heartbeat = $6,
                        is_active = $7
                    WHERE id = $1
                    """,
                    UUID(session_id),
                    data.get("stage"),
                    json.dumps(data.get("outline")) if data.get("outline") else None,
                    data.get("outline_version"),
                    data.get("current_draft"),
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
                SELECT id, role, content, is_question, token_count,
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
                SELECT id, version, draft, token_count, created_at
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
        return result


# 全局实例
_pg_session_manager: Optional[PGSessionManager] = None


def get_pg_session_manager() -> PGSessionManager:
    """获取 PG 会话管理器实例。"""
    global _pg_session_manager
    if _pg_session_manager is None:
        _pg_session_manager = PGSessionManager()
    return _pg_session_manager
```

- [ ] **Step 3: 创建 PostgreSQL 测试文件**

```python
# tests/test_storage/test_pg_client.py

"""PostgreSQL 客户端测试。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from forge.storage.pg_client import PGSessionManager


@pytest.fixture
def pg_manager():
    """创建 PG 会话管理器。"""
    return PGSessionManager()


class TestPGSessionManager:
    """PGSessionManager 测试。"""

    @pytest.mark.asyncio
    async def test_create_session(self, pg_manager):
        """测试创建会话。"""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch('forge.storage.pg_client.get_pg_pool', return_value=mock_pool):
            result = await pg_manager.create_session(
                "test-session-1",
                {
                    "source_article": {"title": "测试"},
                    "stage": "planning",
                }
            )
            assert result == True
            mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_append_message(self, pg_manager):
        """测试追加消息。"""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch('forge.storage.pg_client.get_pg_pool', return_value=mock_pool):
            result = await pg_manager.append_message(
                "test-session-1",
                {
                    "role": "user",
                    "content": "修改第二段",
                    "is_question": False,
                }
            )
            assert result == True

    @pytest.mark.asyncio
    async def test_finalize_session(self, pg_manager):
        """测试定稿。"""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch('forge.storage.pg_client.get_pg_pool', return_value=mock_pool):
            result = await pg_manager.finalize_session(
                "test-session-1",
                "这是最终文章内容..."
            )
            assert result == True

    @pytest.mark.asyncio
    async def test_save_version(self, pg_manager):
        """测试保存版本。"""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch('forge.storage.pg_client.get_pg_pool', return_value=mock_pool):
            result = await pg_manager.save_version(
                "test-session-1",
                version=1,
                draft="第一版草稿...",
                token_count=500,
            )
            assert result == True
```

- [ ] **Step 4: 运行测试验证**

```bash
pytest tests/test_storage/test_pg_client.py -v
```

Expected: PASS

- [ ] **Step 5: Commit PostgreSQL 客户端模块**

```bash
git add forge/storage/pg_client.py tests/test_storage/test_pg_client.py migrations/
git commit -m "feat: add PostgreSQL client for persistent session storage

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: 重写 SessionManager 为双存储架构

**Files:**
- Modify: `forge/deep_mode/session_manager.py`
- Modify: `forge/deep_mode/session_state.py`

- [ ] **Step 1: 读取现有 session_manager.py**

```bash
cat forge/deep_mode/session_manager.py
```

- [ ] **Step 2: 重写 SessionManager**

```python
# forge/deep_mode/session_manager.py

"""会话管理器 - Redis + PostgreSQL 双存储架构。"""

import logging
import uuid
from typing import Optional, Dict, Any, List
from datetime import datetime

from forge.storage.redis_client import get_redis_session_manager
from forge.storage.pg_client import get_pg_session_manager

logger = logging.getLogger(__name__)


class SessionManager:
    """双存储会话管理器。

    Redis：活跃会话缓存（30分钟 TTL）
    PostgreSQL：持久化存储（历史记录）
    """

    def __init__(self):
        self.redis = get_redis_session_manager()
        self.pg = get_pg_session_manager()

    # ---- 创建会话 ----

    async def create_session(
        self,
        source_article: Dict[str, str],
        user_input: str = "",
        session_id: str = None
    ) -> Dict[str, Any]:
        """创建新会话（双写）。"""
        if session_id is None:
            session_id = str(uuid.uuid4())

        session_data = {
            "session_id": session_id,
            "source_article": source_article,
            "user_input": user_input,
            "stage": "planning",
            "outline": None,
            "outline_version": 0,
            "rag_context": None,
            "current_draft": None,
            "tuning_history": [],
            "created_at": datetime.now().isoformat(),
        }

        # 双写：Redis + PG
        await self.redis.create_session(session_id, session_data)
        await self.pg.create_session(session_id, session_data)

        logger.info(f"[SessionManager] Session created: {session_id}")
        return session_data

    # ---- 加载会话 ----

    async def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """加载会话（优先 Redis，降级 PG）。"""
        # 优先从 Redis 获取
        session = await self.redis.get_session(session_id)
        if session:
            # 补充消息历史
            messages = await self.redis.get_messages(session_id)
            session["tuning_history"] = messages
            logger.info(f"[SessionManager] Session loaded from Redis: {session_id}")
            return session

        # Redis 无数据，尝试从 PG 恢复
        session = await self.pg.get_session(session_id)
        if session:
            # 检查是否活跃
            if session.get("is_active"):
                # 从 PG 获取消息历史
                messages = await self.pg.get_messages(session_id)
                session["tuning_history"] = messages

                # 恢复到 Redis
                await self.redis.create_session(session_id, session)
                for msg in messages:
                    await self.redis.append_message(session_id, msg)

                logger.info(f"[SessionManager] Session restored from PG: {session_id}")
                return session

        logger.warning(f"[SessionManager] Session not found: {session_id}")
        return None

    # ---- 更新会话 ----

    async def update_session(
        self,
        session_id: str,
        **updates
    ) -> Dict[str, Any]:
        """更新会话（双写）。"""
        # 获取当前状态
        session = await self.load_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        # 合并更新
        updated_data = {**session, **updates}

        # 更新 Redis
        await self.redis.update_session(session_id, updates)

        # 关键节点写入 PG
        stage = updates.get("stage")
        if stage in ("waiting_outline", "tuning", "completed"):
            await self.pg.update_session(session_id, updated_data)

        # 保存版本（如果草稿更新）
        if "current_draft" in updates and updates["current_draft"]:
            version = session.get("outline_version", 0) + 1
            await self.pg.save_version(
                session_id,
                version=version,
                draft=updates["current_draft"],
            )

        logger.info(f"[SessionManager] Session updated: {session_id}")
        return updated_data

    # ---- 消息操作 ----

    async def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        is_question: bool = False,
        metadata: Dict[str, Any] = None
    ) -> bool:
        """追加消息（双写）。"""
        message = {
            "role": role,
            "content": content,
            "is_question": is_question,
            "metadata": metadata,
            "timestamp": datetime.now().isoformat(),
        }

        # 双写
        await self.redis.append_message(session_id, message)
        await self.pg.append_message(session_id, message)

        # 刷新 TTL（心跳）
        await self.redis.refresh_ttl(session_id)

        return True

    # ---- 定稿 ----

    async def finalize_session(self, session_id: str) -> Dict[str, Any]:
        """定稿会话。"""
        session = await self.load_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        final_draft = session.get("current_draft", "")

        # PG 定稿
        await self.pg.finalize_session(session_id, final_draft)

        # Redis 清理
        await self.redis.delete_session(session_id)

        logger.info(f"[SessionManager] Session finalized: {session_id}")
        return {
            "session_id": session_id,
            "final_draft": final_draft,
            "status": "completed",
        }

    # ---- 心跳 ----

    async def heartbeat(self, session_id: str) -> bool:
        """更新心跳时间。"""
        await self.redis.refresh_ttl(session_id)
        # PG 也更新（可选）
        session = await self.redis.get_session(session_id)
        if session:
            await self.pg.update_session(session_id, {"last_heartbeat": datetime.now()})
        return True

    # ---- 断开保存 ----

    async def save_on_disconnect(self, session_id: str) -> bool:
        """WebSocket 断开时保存状态。"""
        session = await self.redis.get_session(session_id)
        if session:
            messages = await self.redis.get_messages(session_id)
            session["tuning_history"] = messages

            # 更新 PG
            await self.pg.update_session(
                session_id,
                {
                    "stage": session.get("stage"),
                    "current_draft": session.get("current_draft"),
                    "is_active": False,
                }
            )
            logger.info(f"[SessionManager] Session saved on disconnect: {session_id}")
        return True

    # ---- 历史列表 ----

    async def get_history_sessions(
        self,
        limit: int = 20,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """获取历史会话列表。"""
        return await self.pg.get_history_sessions(limit, offset)

    async def get_session_messages(
        self,
        session_id: str
    ) -> List[Dict[str, Any]]:
        """获取会话完整消息历史。"""
        return await self.pg.get_messages(session_id)

    # ---- 大纲版本 ----

    async def increment_outline_version(self, session_id: str) -> int:
        """增加大纲版本号。"""
        session = await self.load_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        new_version = session.get("outline_version", 0) + 1
        await self.update_session(session_id, outline_version=new_version)
        return new_version


# 全局实例
_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """获取会话管理器实例。"""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager
```

- [ ] **Step 3: 更新 session_state.py**

```python
# forge/deep_mode/session_state.py

"""会话状态数据结构定义。"""

from typing import TypedDict, List, Dict, Any, Optional
from datetime import datetime


class SourceArticle(TypedDict):
    """源文章结构。"""
    title: str
    text: str
    url: Optional[str]


class TuningMessage(TypedDict):
    """微调对话消息。"""
    role: str  # "user" | "agent"
    content: str
    is_question: bool
    timestamp: str
    metadata: Optional[Dict[str, Any]]


class OutlineSection(TypedDict, total=False):
    """大纲章节结构。"""
    id: str
    title: str
    keywords: List[str]
    word_count: int
    subsections: List[Dict[str, Any]]


class Outline(TypedDict, total=False):
    """结构化大纲。"""
    sections: List[OutlineSection]
    total_word_count: int
    tone: str
    target_audience: str


class DeepModeSession(TypedDict, total=False):
    """深度生成会话状态。"""
    session_id: str
    source_article: SourceArticle
    user_input: str
    stage: str
    outline: Optional[Outline]
    outline_version: int
    rag_context: Optional[str]
    current_draft: Optional[str]
    tuning_history: List[TuningMessage]
    is_active: bool
    last_heartbeat: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]
    finalized_at: Optional[datetime]
    final_draft: Optional[str]


# Stage 常量
STAGE_PLANNING = "planning"
STAGE_WAITING_OUTLINE = "waiting_outline"
STAGE_EXECUTING = "executing"
STAGE_TUNING = "tuning"
STAGE_COMPLETED = "completed"
```

- [ ] **Step 4: Commit SessionManager 重写**

```bash
git add forge/deep_mode/session_manager.py forge/deep_mode/session_state.py
git commit -m "refactor: rewrite SessionManager with Redis+PG dual storage

- Redis for active session caching (30min TTL)
- PostgreSQL for persistent storage
- Dual-write on critical nodes
- Session restore from PG on Redis miss

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: 修改 WebSocket Handler

**Files:**
- Modify: `forge/deep_mode/websocket_handler.py`

- [ ] **Step 1: 修改 websocket_handler.py 添加心跳和断开保存**

```python
# forge/deep_mode/websocket_handler.py

"""WebSocket 消息处理器 - 使用新的 LangGraph Workflow。"""

import logging
import json
from datetime import datetime
from fastapi import WebSocket, WebSocketDisconnect

from forge.deep_mode.session_manager import get_session_manager
from forge.deep_mode.workflow import run_tuning_agent

logger = logging.getLogger(__name__)


async def handle_websocket_connection(websocket: WebSocket, session_id: str):
    """处理 WebSocket 连接。

    消息类型：
    - tuning_message: 用户发送微调请求
    - tuning_response: Agent 返回响应
    - stage_update: 状态变化通知
    - heartbeat: 心跳检测
    - error: 错误消息
    """
    await websocket.accept()
    logger.info(f"[WebSocket] Connection established for session: {session_id}")

    session_manager = get_session_manager()

    try:
        # 加载会话状态（支持从 PG 恢复）
        session = await session_manager.load_session(session_id)

        if not session:
            await websocket.send_json({
                "type": "error",
                "message": f"Session not found: {session_id}",
            })
            await websocket.close()
            return

        # 发送当前状态
        await websocket.send_json({
            "type": "stage_update",
            "session_id": session_id,
            "stage": session.get("stage"),
            "current_draft": session.get("current_draft") or session.get("draft_v1", ""),
            "tuning_history": session.get("tuning_history", []),
        })

        # 消息循环
        while True:
            data = await websocket.receive_json()
            message_type = data.get("type")

            if message_type == "tuning_message":
                # 处理用户微调请求
                user_message = data.get("content", "")
                logger.info(f"[WebSocket] User message: {user_message[:50]}...")

                # 获取当前草稿
                current_draft = session.get("current_draft") or session.get("draft_v1", "")

                # 运行 Tuning Agent
                response = await run_tuning_agent(current_draft, user_message)
                logger.info(f"[WebSocket] Agent response: {response[:200]}...")

                # 判断响应类型
                is_question_response = response.startswith("【回答】")

                if not is_question_response:
                    response_length_ratio = len(response) / len(current_draft) if current_draft else 0
                    if response_length_ratio < 0.2 and '\n\n' not in response[:500]:
                        logger.info(f"[WebSocket] Short response without article structure")
                        is_question_response = True

                if is_question_response:
                    updated_draft = current_draft
                    display_response = response[4:] if response.startswith("【回答】") else response
                    logger.info(f"[WebSocket] Question response, not updating draft")
                else:
                    response_length_ratio = len(response) / len(current_draft) if current_draft else 0

                    if response_length_ratio < 0.3 and len(current_draft) > 200:
                        logger.warning(f"[WebSocket] Response too short")
                        updated_draft = current_draft
                        display_response = f"⚠️ 修改可能不完整。\n\nAgent 回复：{response}"
                    else:
                        updated_draft = response
                        display_response = response

                # 追加消息（双写 Redis + PG）
                await session_manager.append_message(
                    session_id,
                    role="user",
                    content=user_message,
                )
                await session_manager.append_message(
                    session_id,
                    role="agent",
                    content=display_response,
                    is_question=is_question_response,
                )

                # 更新会话状态
                session = await session_manager.update_session(
                    session_id,
                    current_draft=updated_draft,
                )

                # 发送响应
                await websocket.send_json({
                    "type": "tuning_response",
                    "session_id": session_id,
                    "content": display_response,
                    "is_question": is_question_response,
                    "updated_draft": updated_draft,
                })

            elif message_type == "finalize":
                # 定稿
                session = await session_manager.finalize_session(session_id)
                await websocket.send_json({
                    "type": "finalized",
                    "session_id": session_id,
                    "status": "completed",
                    "final_draft": session.get("final_draft"),
                })
                break

            elif message_type == "heartbeat":
                # 心跳检测
                await session_manager.heartbeat(session_id)
                await websocket.send_json({"type": "heartbeat_ack"})

            elif message_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        # WebSocket 断开时保存状态
        logger.info(f"[WebSocket] Connection disconnected: {session_id}")
        await session_manager.save_on_disconnect(session_id)

    except Exception as e:
        logger.error(f"[WebSocket] Error: {e}")
        await session_manager.save_on_disconnect(session_id)
        await websocket.send_json({
            "type": "error",
            "message": str(e),
        })

    finally:
        logger.info(f"[WebSocket] Connection closed for session: {session_id}")
```

- [ ] **Step 2: Commit WebSocket Handler 修改**

```bash
git add forge/deep_mode/websocket_handler.py
git commit -m "feat: add heartbeat and disconnect save to WebSocket handler

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: 修改 app.py 添加历史列表 API

**Files:**
- Modify: `forge/web/app.py`

- [ ] **Step 1: 读取 app.py 当前内容**

```bash
head -100 forge/web/app.py
```

- [ ] **Step 2: 在 app.py 添加历史 API 端点**

在现有 deep_mode API 端点后添加：

```python
# 在 forge/web/app.py 的 deep_mode 端点部分添加

from forge.deep_mode.session_manager import get_session_manager

# ---- 历史记录 API ----

@app.get("/api/deep_mode/history")
async def get_deep_mode_history(
    limit: int = 20,
    offset: int = 0
):
    """获取历史会话列表（类似 ChatGPT）。"""
    session_manager = get_session_manager()
    sessions = await session_manager.get_history_sessions(limit, offset)
    return {"sessions": sessions, "total": len(sessions)}


@app.get("/api/deep_mode/session/{session_id}/messages")
async def get_session_messages(session_id: str):
    """获取会话完整消息历史。"""
    session_manager = get_session_manager()
    messages = await session_manager.get_session_messages(session_id)
    return {"session_id": session_id, "messages": messages}


@app.post("/api/deep_mode/session/{session_id}/restore")
async def restore_session(session_id: str):
    """恢复中断的会话。"""
    session_manager = get_session_manager()
    session = await session_manager.load_session(session_id)
    if not session:
        return {"error": "Session not found"}
    return {"session": session, "restored": True}
```

- [ ] **Step 3: Commit API 端点添加**

```bash
git add forge/web/app.py
git commit -m "feat: add history list and restore APIs for Deep Mode

- GET /api/deep_mode/history - session history list
- GET /api/deep_mode/session/{id}/messages - full message history
- POST /api/deep_mode/session/{id}/restore - restore interrupted session

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: 添加依赖和启动配置

**Files:**
- Modify: `requirements.txt` 或 `pyproject.toml`
- Modify: `run_web.py`（启动时初始化连接池）

- [ ] **Step 1: 添加依赖**

```bash
# 在 requirements.txt 添加
echo "redis>=5.0.0" >> requirements.txt
echo "asyncpg>=0.29.0" >> requirements.txt
```

- [ ] **Step 2: 修改 run_web.py 添加启动/关闭钩子**

```python
# 在 run_web.py 添加生命周期管理

from contextlib import asynccontextmanager
from fastapi import FastAPI

from forge.storage.redis_client import close_redis_pool
from forge.storage.pg_client import close_pg_pool, get_pg_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理。"""
    # 启动时初始化 PG 连接池
    await get_pg_pool()
    yield
    # 关闭时清理连接池
    await close_redis_pool()
    await close_pg_pool()


# 在 FastAPI 应用创建时使用
app = FastAPI(lifespan=lifespan)
```

- [ ] **Step 3: Commit 依赖和启动配置**

```bash
git add requirements.txt run_web.py
git commit -m "feat: add Redis/asyncpg deps and lifespan hooks

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 8: 整合测试

**Files:**
- 无新文件，运行集成测试

- [ ] **Step 1: 安装新依赖**

```bash
pip install redis asyncpg
```

- [ ] **Step 2: 运行所有测试**

```bash
pytest tests/test_storage/ -v
```

- [ ] **Step 3: 启动服务验证**

```bash
# 确保 Redis 和 PostgreSQL 已运行
python run_web.py
```

验证：
- 访问 `/api/deep_mode/history` 返回空列表或历史数据
- 创建新会话后，检查 Redis 和 PG 都有数据

- [ ] **Step 4: Final Commit**

```bash
git add -A
git commit -m "feat: complete Redis+PG dual storage architecture

- Redis for active session caching (30min TTL, AOF persistence)
- PostgreSQL for persistent storage (sessions, messages, versions)
- Dual-write on critical nodes
- Session restore from PG on disconnect
- History list API (ChatGPT style)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## 验收清单

- [ ] Redis 客户端模块可用，支持会话 CRUD
- [ ] PostgreSQL 客户端模块可用，支持持久化存储
- [ ] SessionManager 双写机制正确
- [ ] WebSocket 断开时保存状态到 PG
- [ ] `/api/deep_mode/history` 返回历史会话列表
- [ ] `/api/deep_mode/session/{id}/messages` 返回消息历史
- [ ] 会话可从 PG 恢复到 Redis
- [ ] 所有测试通过