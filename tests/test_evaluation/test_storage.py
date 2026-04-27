"""评估存储层测试。"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import UUID


def make_async_pool_mock(conn_mock):
    """创建一个支持异步上下文管理器的pool mock。"""
    mock_pool = MagicMock()
    # acquire()返回一个异步上下文管理器
    async_context = MagicMock()
    async_context.__aenter__ = AsyncMock(return_value=conn_mock)
    async_context.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = async_context
    return mock_pool


class MockRow:
    """模拟asyncpg.Record的行数据。"""
    def __init__(self, **kwargs):
        self._data = kwargs

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data.keys())

    def keys(self):
        return self._data.keys()

    def __repr__(self):
        return f"MockRow({self._data})"


class TestEvaluationStorage:
    """测试 EvaluationStorage 类。"""

    @pytest.mark.asyncio
    async def test_save_probe_log(self):
        """测试保存probe log。"""
        from forge.evaluation.storage import EvaluationStorage

        mock_conn = AsyncMock()
        # Mock获取现有logs的结果（空列表）
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock()

        mock_pool = make_async_pool_mock(mock_conn)

        storage = EvaluationStorage()
        storage._pool = mock_pool

        # 使用有效的UUID格式
        session_id = "550e8400-e29b-41d4-a716-446655440000"
        payload = {
            "session_id": session_id,
            "node_name": "editor",
            "timestamp": 1234567890,
            "input_metrics": {"ai_score": 0.0},
            "output_metrics": {"ai_score": 0.0, "draft_length": 100},
            "duration_ms": 3500,
            "loop_type": None,
            "loop_iteration": 0,
            "metadata": {},
        }

        await storage.save_probe_log(payload)

        # 验证execute被调用
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_session_probe_logs(self):
        """测试获取session的所有probe logs。"""
        from forge.evaluation.storage import EvaluationStorage

        # 创建mock行数据
        mock_rows = [
            MockRow(
                id=UUID("550e8400-e29b-41d4-a716-446655440001"),
                session_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
                node_name="editor",
                node_sequence=1,
                timestamp=datetime(2025, 1, 1, 10, 0, 0),
                input_metrics={"ai_score": 0.0},
                output_metrics={"draft_length": 100},
                duration_ms=3500,
                loop_type=None,
                loop_iteration=0,
                metadata={},
            ),
            MockRow(
                id=UUID("550e8400-e29b-41d4-a716-446655440002"),
                session_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
                node_name="ai_detector",
                node_sequence=2,
                timestamp=datetime(2025, 1, 1, 10, 0, 5),
                input_metrics={"draft_length": 100},
                output_metrics={"ai_score": 0.85},
                duration_ms=500,
                loop_type=None,
                loop_iteration=0,
                metadata={},
            ),
        ]

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=mock_rows)

        mock_pool = make_async_pool_mock(mock_conn)

        storage = EvaluationStorage()
        storage._pool = mock_pool

        session_id = "550e8400-e29b-41d4-a716-446655440000"
        logs = await storage.get_session_probe_logs(session_id)

        assert len(logs) == 2
        assert logs[0]["node_name"] == "editor"
        assert logs[1]["node_name"] == "ai_detector"

    @pytest.mark.asyncio
    async def test_save_evaluation_result(self):
        """测试保存评估结果。"""
        from forge.evaluation.storage import EvaluationStorage

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_pool = make_async_pool_mock(mock_conn)

        storage = EvaluationStorage()
        storage._pool = mock_pool

        session_id = "550e8400-e29b-41d4-a716-446655440000"
        result = {
            "overall_score": 0.85,
            "faithfulness_score": 0.90,
            "relevance_score": 0.80,
            "human_score": 0.75,
            "metrics_detail": {"ai_detection": {"ai_score": 0.25}},
            "node_effectiveness": {"editor": {"gain": 0.15}},
            "loop_roi": {"humanize_loop": {"roi": 0.13}},
            "status": "completed",
        }

        await storage.save_evaluation_result(session_id, result)

        mock_conn.execute.assert_called()

    @pytest.mark.asyncio
    async def test_get_evaluation_result(self):
        """测试获取评估结果。"""
        from forge.evaluation.storage import EvaluationStorage

        mock_row = MockRow(
            id=UUID("550e8400-e29b-41d4-a716-446655440003"),
            session_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
            overall_score=0.85,
            faithfulness_score=0.90,
            relevance_score=0.80,
            human_score=0.75,
            metrics_detail={"ai_detection": {"ai_score": 0.25}},
            node_effectiveness={},
            loop_roi={},
            created_at=datetime(2025, 1, 1, 10, 0, 0),
            evaluated_at=datetime(2025, 1, 1, 10, 5, 0),
            status="completed",
            error_message=None,
        )

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=mock_row)

        mock_pool = make_async_pool_mock(mock_conn)

        storage = EvaluationStorage()
        storage._pool = mock_pool

        session_id = "550e8400-e29b-41d4-a716-446655440000"
        result = await storage.get_evaluation_result(session_id)

        assert result["overall_score"] == 0.85
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_get_evaluation_result_not_found(self):
        """测试评估结果不存在时返回None。"""
        from forge.evaluation.storage import EvaluationStorage

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)

        mock_pool = make_async_pool_mock(mock_conn)

        storage = EvaluationStorage()
        storage._pool = mock_pool

        session_id = "550e8400-e29b-41d4-a716-446655440000"
        result = await storage.get_evaluation_result(session_id)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_evaluation_stats(self):
        """测试获取评估统计数据。"""
        from forge.evaluation.storage import EvaluationStorage

        mock_rows = [
            MockRow(
                session_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
                overall_score=0.85,
                faithfulness_score=0.90,
                relevance_score=0.80,
                human_score=0.75,
                status="completed",
                created_at=datetime(2025, 1, 1, 10, 0, 0),
            ),
            MockRow(
                session_id=UUID("550e8400-e29b-41d4-a716-446655440001"),
                overall_score=0.72,
                faithfulness_score=0.85,
                relevance_score=0.70,
                human_score=0.65,
                status="completed",
                created_at=datetime(2025, 1, 2, 10, 0, 0),
            ),
        ]

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=mock_rows)

        mock_pool = make_async_pool_mock(mock_conn)

        storage = EvaluationStorage()
        storage._pool = mock_pool

        stats = await storage.get_evaluation_stats(limit=10)

        assert len(stats) == 2
        assert stats[0]["overall_score"] == 0.85


class TestConvenienceFunctions:
    """测试便捷函数。"""

    @pytest.mark.asyncio
    async def test_save_probe_log_function(self):
        """测试便捷save_probe_log函数。"""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch('forge.evaluation.storage.get_evaluation_storage') as mock_get:
            from forge.evaluation.storage import save_probe_log

            storage = MagicMock()
            storage._pool = mock_pool
            storage.save_probe_log = AsyncMock()
            mock_get.return_value = storage

            payload = {
                "session_id": "550e8400-e29b-41d4-a716-446655440000",
                "node_name": "editor",
                "timestamp": 1234567890,
                "input_metrics": {},
                "output_metrics": {},
                "duration_ms": 1000,
            }

            await save_probe_log(payload)

            storage.save_probe_log.assert_called_once_with(payload)

    @pytest.mark.asyncio
    async def test_get_session_probe_logs_function(self):
        """测试便捷get_session_probe_logs函数。"""
        with patch('forge.evaluation.storage.get_evaluation_storage') as mock_get:
            from forge.evaluation.storage import get_session_probe_logs

            storage = MagicMock()
            storage.get_session_probe_logs = AsyncMock(return_value=[{"node_name": "editor"}])
            mock_get.return_value = storage

            logs = await get_session_probe_logs("550e8400-e29b-41d4-a716-446655440000")

            assert len(logs) == 1
            storage.get_session_probe_logs.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_evaluation_result_function(self):
        """测试便捷save_evaluation_result函数。"""
        with patch('forge.evaluation.storage.get_evaluation_storage') as mock_get:
            from forge.evaluation.storage import save_evaluation_result

            storage = MagicMock()
            storage.save_evaluation_result = AsyncMock()
            mock_get.return_value = storage

            result = {"overall_score": 0.85, "status": "completed"}
            await save_evaluation_result("550e8400-e29b-41d4-a716-446655440000", result)

            storage.save_evaluation_result.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_evaluation_result_function(self):
        """测试便捷get_evaluation_result函数。"""
        with patch('forge.evaluation.storage.get_evaluation_storage') as mock_get:
            from forge.evaluation.storage import get_evaluation_result

            storage = MagicMock()
            storage.get_evaluation_result = AsyncMock(return_value={"overall_score": 0.85})
            mock_get.return_value = storage

            result = await get_evaluation_result("550e8400-e29b-41d4-a716-446655440000")

            assert result["overall_score"] == 0.85
            storage.get_evaluation_result.assert_called_once()