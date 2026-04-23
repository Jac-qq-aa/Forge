# tests/test_storage/test_redis_client.py

"""Redis 客户端测试。"""

import pytest
import asyncio
from datetime import datetime
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

    @pytest.mark.asyncio
    async def test_update_session(self, redis_manager):
        """测试更新会话。"""
        mock_client = AsyncMock()
        redis_manager._client = mock_client

        await redis_manager.update_session(
            "test-session-1",
            {"stage": "tuning", "current_draft": "新草稿"}
        )
        mock_client.hset.assert_called_once()
        mock_client.expire.assert_called()

    @pytest.mark.asyncio
    async def test_delete_session(self, redis_manager):
        """测试删除会话。"""
        mock_client = AsyncMock()
        redis_manager._client = mock_client

        await redis_manager.delete_session("test-session-1")
        mock_client.delete.assert_called()

    @pytest.mark.asyncio
    async def test_datetime_deserialization(self, redis_manager):
        """测试 datetime 反序列化。"""
        data = {
            "last_heartbeat": "2024-01-15T10:30:00",
            "created_at": "2024-01-15T09:00:00",
        }
        deserialized = redis_manager._deserialize_data(data)

        assert isinstance(deserialized["last_heartbeat"], datetime)
        assert isinstance(deserialized["created_at"], datetime)