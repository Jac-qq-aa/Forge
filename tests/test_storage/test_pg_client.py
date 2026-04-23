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
    async def test_get_session(self, pg_manager):
        """测试获取会话。"""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_row = MagicMock()
        mock_row.__iter__ = lambda self: iter([
            ("id", "test-session-1"),
            ("stage", "tuning"),
        ])
        mock_conn.fetchrow.return_value = mock_row
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch('forge.storage.pg_client.get_pg_pool', return_value=mock_pool):
            result = await pg_manager.get_session("test-session-1")
            assert result is not None

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, pg_manager):
        """测试获取不存在的会话。"""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch('forge.storage.pg_client.get_pg_pool', return_value=mock_pool):
            result = await pg_manager.get_session("non-existent")
            assert result is None

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

    @pytest.mark.asyncio
    async def test_get_history_sessions(self, pg_manager):
        """测试获取历史列表。"""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch('forge.storage.pg_client.get_pg_pool', return_value=mock_pool):
            result = await pg_manager.get_history_sessions(limit=20)
            assert result == []

    @pytest.mark.asyncio
    async def test_row_to_dict(self, pg_manager):
        """测试行转字典。"""
        from uuid import UUID

        mock_row = {
            "id": UUID("12345678-1234-5678-1234-567812345678"),
            "stage": "tuning",
            "created_at": datetime(2024, 1, 15, 10, 30, 0),
        }

        result = pg_manager._row_to_dict(mock_row)

        assert result["id"] == "12345678-1234-5678-1234-567812345678"
        assert result["stage"] == "tuning"
        assert result["created_at"] == "2024-01-15T10:30:00"