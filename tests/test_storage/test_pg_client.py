# tests/test_storage/test_pg_client.py

"""PostgreSQL 客户端测试。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from uuid import UUID

from forge.storage.pg_client import PGSessionManager


# 测试用的有效 UUID
TEST_SESSION_ID = "12345678-1234-5678-1234-567812345678"


def make_mock_pool(mock_conn):
    """创建正确配置的 mock 连接池。

    asyncpg 的 pool.acquire() 返回异步上下文管理器，
    需要用 MagicMock 而不是 AsyncMock 来模拟。
    """
    mock_pool = MagicMock()
    mock_acquire_ctx = MagicMock()
    mock_acquire_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = mock_acquire_ctx
    return mock_pool


@pytest.fixture
def pg_manager():
    """创建 PG 会话管理器。"""
    return PGSessionManager()


class TestPGSessionManager:
    """PGSessionManager 测试。"""

    @pytest.mark.asyncio
    async def test_create_session(self, pg_manager):
        """测试创建会话。"""
        mock_conn = AsyncMock()
        mock_pool = make_mock_pool(mock_conn)

        with patch('forge.storage.pg_client.get_pg_pool', AsyncMock(return_value=mock_pool)):
            result = await pg_manager.create_session(
                TEST_SESSION_ID,
                {
                    "source_article": {"title": "测试"},
                    "stage": "planning",
                }
            )
            assert result == True
            mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_session(self, pg_manager):
        """测试获取会话。"""
        mock_conn = AsyncMock()
        mock_row = MagicMock()
        mock_row.__iter__ = lambda self: iter([
            ("id", UUID(TEST_SESSION_ID)),
            ("stage", "tuning"),
        ])
        mock_conn.fetchrow.return_value = mock_row
        mock_pool = make_mock_pool(mock_conn)

        with patch('forge.storage.pg_client.get_pg_pool', AsyncMock(return_value=mock_pool)):
            result = await pg_manager.get_session(TEST_SESSION_ID)
            assert result is not None

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, pg_manager):
        """测试获取不存在的会话。"""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_pool = make_mock_pool(mock_conn)

        with patch('forge.storage.pg_client.get_pg_pool', AsyncMock(return_value=mock_pool)):
            result = await pg_manager.get_session("00000000-0000-0000-0000-000000000000")
            assert result is None

    @pytest.mark.asyncio
    async def test_append_message(self, pg_manager):
        """测试追加消息。"""
        mock_conn = AsyncMock()
        mock_pool = make_mock_pool(mock_conn)

        with patch('forge.storage.pg_client.get_pg_pool', AsyncMock(return_value=mock_pool)):
            result = await pg_manager.append_message(
                TEST_SESSION_ID,
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
        mock_conn = AsyncMock()
        mock_pool = make_mock_pool(mock_conn)

        with patch('forge.storage.pg_client.get_pg_pool', AsyncMock(return_value=mock_pool)):
            result = await pg_manager.finalize_session(
                TEST_SESSION_ID,
                "这是最终文章内容..."
            )
            assert result == True

    @pytest.mark.asyncio
    async def test_save_version(self, pg_manager):
        """测试保存版本。"""
        mock_conn = AsyncMock()
        mock_pool = make_mock_pool(mock_conn)

        with patch('forge.storage.pg_client.get_pg_pool', AsyncMock(return_value=mock_pool)):
            result = await pg_manager.save_version(
                TEST_SESSION_ID,
                version=1,
                draft="第一版草稿...",
                token_count=500,
            )
            assert result == True

    @pytest.mark.asyncio
    async def test_get_history_sessions(self, pg_manager):
        """测试获取历史列表。"""
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool = make_mock_pool(mock_conn)

        with patch('forge.storage.pg_client.get_pg_pool', AsyncMock(return_value=mock_pool)):
            result = await pg_manager.get_history_sessions(limit=20)
            assert result == []

    @pytest.mark.asyncio
    async def test_row_to_dict(self, pg_manager):
        """测试行转字典。"""
        mock_row = {
            "id": UUID("12345678-1234-5678-1234-567812345678"),
            "stage": "tuning",
            "created_at": datetime(2024, 1, 15, 10, 30, 0),
        }

        result = pg_manager._row_to_dict(mock_row)

        assert result["id"] == "12345678-1234-5678-1234-567812345678"
        assert result["stage"] == "tuning"
        assert result["created_at"] == "2024-01-15T10:30:00"

    @pytest.mark.asyncio
    async def test_update_session(self, pg_manager):
        """测试更新会话。"""
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "UPDATE 1"
        mock_pool = make_mock_pool(mock_conn)

        with patch('forge.storage.pg_client.get_pg_pool', AsyncMock(return_value=mock_pool)):
            result = await pg_manager.update_session(
                TEST_SESSION_ID,
                {
                    "stage": "tuning",
                    "current_draft": "新草稿内容",
                }
            )
            assert result == True

    @pytest.mark.asyncio
    async def test_update_session_with_optimistic_lock(self, pg_manager):
        """测试乐观锁更新。"""
        mock_conn = AsyncMock()
        # 乐观锁版本不匹配，更新失败
        mock_conn.execute.return_value = "UPDATE 0"
        mock_pool = make_mock_pool(mock_conn)

        with patch('forge.storage.pg_client.get_pg_pool', AsyncMock(return_value=mock_pool)):
            result = await pg_manager.update_session(
                TEST_SESSION_ID,
                {
                    "stage": "tuning",
                    "lock_version": 1,
                },
                increment_version=True
            )
            assert result == False  # 版本不匹配，更新失败

    @pytest.mark.asyncio
    async def test_get_messages(self, pg_manager):
        """测试获取消息列表。"""
        mock_conn = AsyncMock()
        mock_rows = [
            {"id": "msg-1", "role": "user", "content": "修改第二段"},
            {"id": "msg-2", "role": "agent", "content": "已修改"},
        ]
        mock_conn.fetch.return_value = mock_rows
        mock_pool = make_mock_pool(mock_conn)

        with patch('forge.storage.pg_client.get_pg_pool', AsyncMock(return_value=mock_pool)):
            result = await pg_manager.get_messages(TEST_SESSION_ID)
            assert len(result) == 2
            assert result[0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_get_versions(self, pg_manager):
        """测试获取版本列表。"""
        mock_conn = AsyncMock()
        mock_rows = [
            {"version": 1, "draft": "第一版"},
            {"version": 2, "draft": "第二版"},
        ]
        mock_conn.fetch.return_value = mock_rows
        mock_pool = make_mock_pool(mock_conn)

        with patch('forge.storage.pg_client.get_pg_pool', AsyncMock(return_value=mock_pool)):
            result = await pg_manager.get_versions(TEST_SESSION_ID)
            assert len(result) == 2
            assert result[0]["version"] == 1

    @pytest.mark.asyncio
    async def test_get_active_sessions(self, pg_manager):
        """测试获取活跃会话列表。"""
        mock_conn = AsyncMock()
        mock_rows = [
            {"id": "session-1", "stage": "tuning", "is_active": True},
            {"id": "session-2", "stage": "planning", "is_active": True},
        ]
        mock_conn.fetch.return_value = mock_rows
        mock_pool = make_mock_pool(mock_conn)

        with patch('forge.storage.pg_client.get_pg_pool', AsyncMock(return_value=mock_pool)):
            result = await pg_manager.get_active_sessions()
            assert len(result) == 2