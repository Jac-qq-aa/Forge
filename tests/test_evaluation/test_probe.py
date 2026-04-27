"""节点探针测试。"""

import pytest
import json
from unittest.mock import MagicMock, patch


class TestExtractKeyMetrics:
    """测试 extract_key_metrics 函数。"""

    def test_extract_basic_metrics(self):
        """测试提取基本指标。"""
        from forge.evaluation.probe import extract_key_metrics

        test_draft = "这是一段测试文字"  # 7个字符
        state = {
            "session_id": "test-123",
            "ai_score": 0.85,
            "revision_count": 1,
            "humanize_revisions": 2,
            "rewritten_draft": test_draft,
            "target_platform": "zhihu_article",
        }

        metrics = extract_key_metrics(state)

        assert metrics["ai_score"] == 0.85
        assert metrics["revision_count"] == 1
        assert metrics["humanize_revisions"] == 2
        assert metrics["draft_length"] == len(test_draft)
        assert metrics["draft_text"] == test_draft

    def test_extract_with_current_draft(self):
        """测试使用 current_draft 字段。"""
        from forge.evaluation.probe import extract_key_metrics

        test_draft = "深度模式草稿内容"  # 7个字符
        state = {
            "current_draft": test_draft,
            "rewritten_draft": None,
        }

        metrics = extract_key_metrics(state)

        assert metrics["draft_length"] == len(test_draft)
        assert metrics["draft_text"] == test_draft

    def test_extract_draft_text_truncated(self):
        """测试 draft_text 截断到1000字符。"""
        from forge.evaluation.probe import extract_key_metrics

        # 创建一个超过1000字符的文本
        # 每个中文字符是1个字符，用ASCII更可靠
        long_text = "a" * 1500  # 1500个字符
        state = {
            "rewritten_draft": long_text,
        }

        metrics = extract_key_metrics(state)

        # 截断到1000字符
        assert len(metrics["draft_text"]) == 1000
        assert metrics["draft_length"] == 1500

    def test_extract_empty_state(self):
        """测试空状态。"""
        from forge.evaluation.probe import extract_key_metrics

        state = {}

        metrics = extract_key_metrics(state)

        assert metrics["ai_score"] == 0.0
        assert metrics["revision_count"] == 0
        assert metrics["draft_length"] == 0
        assert metrics["draft_text"] == ""


class TestProbeNode:
    """测试 probe_node 函数。"""

    @patch("forge.evaluation.probe._get_redis_client")
    def test_probe_node_pushes_to_queue(self, mock_get_client):
        """测试探针push数据到Redis队列。"""
        from forge.evaluation.probe import probe_node

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        state_before = {
            "session_id": "session-abc",
            "ai_score": 0.0,
            "revision_count": 0,
        }
        state_after = {
            "session_id": "session-abc",
            "ai_score": 0.0,
            "revision_count": 1,
            "rewritten_draft": "改写后的内容",
            "target_platform": "zhihu_article",
        }

        probe_node(
            node_name="editor",
            state_before=state_before,
            state_after=state_after,
            duration_ms=3500,
        )

        # 验证lpush被调用
        mock_client.lpush.assert_called_once()
        call_args = mock_client.lpush.call_args
        assert call_args[0][0] == "forge:evaluation:queue"

        # 解析payload验证内容
        payload = json.loads(call_args[0][1])
        assert payload["session_id"] == "session-abc"
        assert payload["node_name"] == "editor"
        assert payload["duration_ms"] == 3500
        assert payload["input_metrics"]["revision_count"] == 0
        assert payload["output_metrics"]["revision_count"] == 1

    @patch("forge.evaluation.probe._get_redis_client")
    def test_probe_node_with_loop_info(self, mock_get_client):
        """测试带循环信息的探针。"""
        from forge.evaluation.probe import probe_node

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        state_before = {"session_id": "test", "ai_score": 0.85}
        state_after = {"session_id": "test", "ai_score": 0.65}

        probe_node(
            node_name="humanizer_editor",
            state_before=state_before,
            state_after=state_after,
            duration_ms=2800,
            loop_info={"loop_type": "humanize_loop", "iteration": 1},
        )

        mock_client.lpush.assert_called_once()
        payload = json.loads(mock_client.lpush.call_args[0][1])
        assert payload["loop_type"] == "humanize_loop"
        assert payload["loop_iteration"] == 1

    @patch("forge.evaluation.probe._get_redis_client")
    def test_probe_node_handles_redis_failure(self, mock_get_client):
        """测试Redis失败时静默处理。"""
        from forge.evaluation.probe import probe_node

        mock_client = MagicMock()
        mock_client.lpush.side_effect = Exception("Redis connection failed")
        mock_get_client.return_value = mock_client

        state_before = {"session_id": "test"}
        state_after = {"session_id": "test", "ai_score": 0.5}

        # 应该不抛出异常，静默处理
        probe_node(
            node_name="editor",
            state_before=state_before,
            state_after=state_after,
            duration_ms=1000,
        )

        # 验证lpush被调用（即使失败）
        mock_client.lpush.assert_called_once()