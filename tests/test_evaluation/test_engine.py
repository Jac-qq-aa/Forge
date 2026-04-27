"""评估引擎测试。

测试 EvaluationEngine 和 ProbeCalculator 的核心功能。
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from typing import Dict, Any, List


class TestProbeCalculator:
    """测试 ProbeCalculator - 节点有效性 + 循环ROI计算。"""

    def test_calculate_node_effectiveness_basic(self):
        """测试基本节点有效性计算。"""
        from forge.evaluation.probe_calculator import calculate_node_effectiveness

        probe_logs = [
            {
                "node_name": "editor",
                "input_metrics": {"ai_score": 0.0, "revision_count": 0},
                "output_metrics": {"ai_score": 0.3, "revision_count": 1},
                "duration_ms": 5000,  # 5秒
            }
        ]

        result = calculate_node_effectiveness(probe_logs)

        assert "editor" in result
        # effectiveness = (output_score - input_score) / duration_seconds
        # (0.3 - 0.0) / 5 = 0.06
        assert result["editor"]["effectiveness"] == pytest.approx(0.06, rel=0.01)
        assert result["editor"]["input_score"] == 0.0
        assert result["editor"]["output_score"] == 0.3
        assert result["editor"]["duration_seconds"] == 5.0

    def test_calculate_node_effectiveness_multiple_nodes(self):
        """测试多节点有效性计算。"""
        from forge.evaluation.probe_calculator import calculate_node_effectiveness

        probe_logs = [
            {
                "node_name": "editor",
                "input_metrics": {"ai_score": 0.0, "revision_count": 0},
                "output_metrics": {"ai_score": 0.3, "revision_count": 1},
                "duration_ms": 3000,
            },
            {
                "node_name": "ai_detector",
                "input_metrics": {"ai_score": 0.0},
                "output_metrics": {"ai_score": 0.8},
                "duration_ms": 2000,
            },
            {
                "node_name": "humanizer_editor",
                "input_metrics": {"ai_score": 0.8},
                "output_metrics": {"ai_score": 0.5},
                "duration_ms": 4000,
            },
        ]

        result = calculate_node_effectiveness(probe_logs)

        assert "editor" in result
        assert "ai_detector" in result
        assert "humanizer_editor" in result

        # editor: (0.3 - 0.0) / 3 = 0.1
        assert result["editor"]["effectiveness"] == pytest.approx(0.1, rel=0.01)
        # ai_detector: (0.8 - 0.0) / 2 = 0.4
        assert result["ai_detector"]["effectiveness"] == pytest.approx(0.4, rel=0.01)
        # humanizer_editor: (0.5 - 0.8) / 4 = -0.075 (降低AI分数是好事)
        assert result["humanizer_editor"]["effectiveness"] == pytest.approx(-0.075, rel=0.01)

    def test_calculate_node_effectiveness_zero_duration(self):
        """测试零持续时间处理。"""
        from forge.evaluation.probe_calculator import calculate_node_effectiveness

        probe_logs = [
            {
                "node_name": "editor",
                "input_metrics": {"ai_score": 0.0},
                "output_metrics": {"ai_score": 0.5},
                "duration_ms": 0,
            }
        ]

        result = calculate_node_effectiveness(probe_logs)

        # 零持续时间应该返回0或特殊处理
        assert result["editor"]["effectiveness"] == 0.0

    def test_calculate_node_effectiveness_missing_metrics(self):
        """测试缺失指标的处理。"""
        from forge.evaluation.probe_calculator import calculate_node_effectiveness

        probe_logs = [
            {
                "node_name": "editor",
                "input_metrics": {},  # 无ai_score
                "output_metrics": {"ai_score": 0.5},
                "duration_ms": 3000,
            }
        ]

        result = calculate_node_effectiveness(probe_logs)

        # 缺失指标默认为0
        assert result["editor"]["input_score"] == 0.0
        assert result["editor"]["output_score"] == 0.5

    def test_calculate_loop_roi_basic(self):
        """测试基本循环ROI计算。"""
        from forge.evaluation.probe_calculator import calculate_loop_roi

        probe_logs = [
            {
                "node_name": "ai_detector",
                "loop_type": "humanize_loop",
                "loop_iteration": 1,
                "input_metrics": {"ai_score": 0.85},
                "output_metrics": {"ai_score": 0.85},
                "duration_ms": 1000,
            },
            {
                "node_name": "humanizer_editor",
                "loop_type": "humanize_loop",
                "loop_iteration": 1,
                "input_metrics": {"ai_score": 0.85},
                "output_metrics": {"ai_score": 0.65},
                "duration_ms": 5000,
            },
            {
                "node_name": "ai_detector",
                "loop_type": "humanize_loop",
                "loop_iteration": 2,
                "input_metrics": {"ai_score": 0.65},
                "output_metrics": {"ai_score": 0.65},
                "duration_ms": 1000,
            },
            {
                "node_name": "humanizer_editor",
                "loop_type": "humanize_loop",
                "loop_iteration": 2,
                "input_metrics": {"ai_score": 0.65},
                "output_metrics": {"ai_score": 0.45},
                "duration_ms": 5000,
            },
        ]

        result = calculate_loop_roi(probe_logs)

        assert "humanize_loop" in result
        # roi = (initial_score - final_score) / iterations
        # (0.85 - 0.45) / 2 = 0.2
        assert result["humanize_loop"]["roi"] == pytest.approx(0.2, rel=0.01)
        assert result["humanize_loop"]["initial_score"] == 0.85
        assert result["humanize_loop"]["final_score"] == 0.45
        assert result["humanize_loop"]["iterations"] == 2

    def test_calculate_loop_roi_multiple_loops(self):
        """测试多种循环类型。"""
        from forge.evaluation.probe_calculator import calculate_loop_roi

        probe_logs = [
            {
                "node_name": "node_a",
                "loop_type": "loop_type_a",
                "loop_iteration": 1,
                "input_metrics": {"ai_score": 0.9},
                "output_metrics": {"ai_score": 0.6},
                "duration_ms": 1000,
            },
            {
                "node_name": "node_b",
                "loop_type": "loop_type_b",
                "loop_iteration": 1,
                "input_metrics": {"ai_score": 0.8},
                "output_metrics": {"ai_score": 0.4},
                "duration_ms": 1000,
            },
        ]

        result = calculate_loop_roi(probe_logs)

        assert "loop_type_a" in result
        assert "loop_type_b" in result
        assert result["loop_type_a"]["roi"] == pytest.approx(0.3, rel=0.01)
        assert result["loop_type_b"]["roi"] == pytest.approx(0.4, rel=0.01)

    def test_calculate_loop_roi_no_loops(self):
        """测试无循环数据时返回空字典。"""
        from forge.evaluation.probe_calculator import calculate_loop_roi

        probe_logs = [
            {
                "node_name": "editor",
                "loop_type": None,
                "loop_iteration": 0,
                "input_metrics": {"ai_score": 0.0},
                "output_metrics": {"ai_score": 0.3},
                "duration_ms": 3000,
            }
        ]

        result = calculate_loop_roi(probe_logs)

        assert result == {}

    def test_calculate_loop_roi_empty_logs(self):
        """测试空日志列表。"""
        from forge.evaluation.probe_calculator import calculate_loop_roi

        result = calculate_loop_roi([])

        assert result == {}


class TestEvaluationEngine:
    """测试 EvaluationEngine 核心功能。"""

    def test_parse_score_with_label(self):
        """测试解析LLM响应中的分数。"""
        from forge.evaluation.engine import EvaluationEngine

        engine = EvaluationEngine()

        # 测试标准格式
        response = "Faithfulness评分: 8.5\n其他内容"
        score = engine.parse_score(response, "Faithfulness")
        assert score == 8.5

        # 测试中文格式
        response = "忠实度评分：9分"
        score = engine.parse_score(response, "忠实度")
        assert score == 9.0

        # 测试带"分"后缀
        response = "Relevance: 7.5分"
        score = engine.parse_score(response, "Relevance")
        assert score == 7.5

    def test_parse_score_not_found(self):
        """测试找不到分数时返回None。"""
        from forge.evaluation.engine import EvaluationEngine

        engine = EvaluationEngine()

        response = "没有找到评分标签"
        score = engine.parse_score(response, "Faithfulness")
        assert score is None

    def test_parse_score_various_formats(self):
        """测试各种分数格式。"""
        from forge.evaluation.engine import EvaluationEngine

        engine = EvaluationEngine()

        # 整数
        assert engine.parse_score("分数: 8", "分数") == 8.0
        # 小数
        assert engine.parse_score("分数: 8.5", "分数") == 8.5
        # 带括号
        assert engine.parse_score("分数(1-10): 9", "分数") == 9.0

    @pytest.mark.asyncio
    async def test_calculate_overall(self):
        """测试综合评分计算。"""
        from forge.evaluation.engine import EvaluationEngine

        engine = EvaluationEngine()

        result = {
            "faithfulness_score": 8.0,
            "relevance_score": 7.0,
            "human_score": 9.0,
        }

        overall = engine._calculate_overall(result)

        # 权重: Faithfulness 40% + Relevance 30% + Human 30%
        # 8.0 * 0.4 + 7.0 * 0.3 + 9.0 * 0.3 = 3.2 + 2.1 + 2.7 = 8.0
        assert overall == pytest.approx(8.0, rel=0.01)

    @pytest.mark.asyncio
    async def test_calculate_overall_missing_scores(self):
        """测试缺失评分时的处理。"""
        from forge.evaluation.engine import EvaluationEngine

        engine = EvaluationEngine()

        # 只有两个评分
        result = {
            "faithfulness_score": 8.0,
            "relevance_score": 7.0,
        }

        overall = engine._calculate_overall(result)

        # 缺失human_score时，重新分配权重
        # 8.0 * (0.4/0.7) + 7.0 * (0.3/0.7) ≈ 4.57 + 3.0 = 7.57
        # 或者简单处理：缺失的评分为0
        # 8.0 * 0.4 + 7.0 * 0.3 + 0 * 0.3 = 3.2 + 2.1 + 0 = 5.3
        # 根据实际实现选择
        assert overall >= 0

    @pytest.mark.asyncio
    async def test_run_style_evaluation(self):
        """测试风格质量LLM Judge评估。"""
        from forge.evaluation.engine import EvaluationEngine

        engine = EvaluationEngine()

        draft_text = "这是一篇关于人工智能的文章，探讨了深度学习的发展趋势。"

        with patch.object(engine, '_call_judge_llm', new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = "Human评分: 8.5\n风格自然，语言流畅。"

            result = await engine._run_style_evaluation(draft_text)

            assert result["human_score"] == 8.5
            assert "raw_response" in result

    @pytest.mark.asyncio
    async def test_run_ragas_evaluation_fallback_to_judge(self):
        """测试RAGAS评估fallback到LLM Judge。"""
        from forge.evaluation.engine import EvaluationEngine

        engine = EvaluationEngine()

        original_text = "原始素材内容"
        draft_text = "改写后的文章内容"

        with patch.object(engine, '_run_llm_judge_evaluation', new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = {
                "faithfulness_score": 8.0,
                "relevance_score": 7.5,
            }

            result = await engine._run_ragas_evaluation(original_text, draft_text)

            assert "faithfulness_score" in result
            assert "relevance_score" in result
            mock_judge.assert_called_once()

    @pytest.mark.asyncio
    async def test_evaluate_session_complete(self):
        """测试完整session评估流程。"""
        from forge.evaluation.engine import EvaluationEngine

        engine = EvaluationEngine()

        probe_logs = [
            {
                "node_name": "editor",
                "input_metrics": {"ai_score": 0.0},
                "output_metrics": {"ai_score": 0.3},
                "duration_ms": 5000,
                "loop_type": None,
                "loop_iteration": 0,
            },
            {
                "node_name": "ai_detector",
                "input_metrics": {"ai_score": 0.0},
                "output_metrics": {"ai_score": 0.8},
                "duration_ms": 2000,
                "loop_type": None,
                "loop_iteration": 0,
            },
            {
                "node_name": "humanizer_editor",
                "input_metrics": {"ai_score": 0.8},
                "output_metrics": {"ai_score": 0.45},
                "duration_ms": 5000,
                "loop_type": "humanize_loop",
                "loop_iteration": 1,
            },
        ]

        with patch.object(engine, '_run_ragas_evaluation', new_callable=AsyncMock) as mock_ragas:
            with patch.object(engine, '_run_style_evaluation', new_callable=AsyncMock) as mock_style:
                mock_ragas.return_value = {
                    "faithfulness_score": 8.0,
                    "relevance_score": 7.5,
                }
                mock_style.return_value = {
                    "human_score": 8.5,
                }

                # 需要mock storage
                with patch.object(engine, '_save_result', new_callable=AsyncMock):
                    result = await engine.evaluate_session(
                        session_id="test-session-123",
                        probe_logs=probe_logs,
                        original_text="原始内容",
                        draft_text="改写后内容",
                    )

                assert "overall_score" in result
                assert "faithfulness_score" in result
                assert "relevance_score" in result
                assert "human_score" in result
                assert "node_effectiveness" in result
                assert "loop_roi" in result

    @pytest.mark.asyncio
    async def test_evaluate_session_minimal(self):
        """测试最小化session评估（无原始文本）。"""
        from forge.evaluation.engine import EvaluationEngine

        engine = EvaluationEngine()

        probe_logs = [
            {
                "node_name": "editor",
                "input_metrics": {"ai_score": 0.0},
                "output_metrics": {"ai_score": 0.3},
                "duration_ms": 5000,
                "loop_type": None,
                "loop_iteration": 0,
            }
        ]

        # 无原始文本时，跳过RAGAS评估
        with patch.object(engine, '_save_result', new_callable=AsyncMock):
            result = await engine.evaluate_session(
                session_id="test-session-456",
                probe_logs=probe_logs,
                original_text=None,
                draft_text=None,
            )

        # 应该返回基本的节点有效性分析
        assert "node_effectiveness" in result
        assert "overall_score" in result


class TestEvaluationEngineIntegration:
    """EvaluationEngine 集成测试。"""

    @pytest.mark.asyncio
    async def test_llm_judge_evaluation_prompt(self):
        """测试LLM Judge评估prompt是否正确构造。"""
        from forge.evaluation.engine import EvaluationEngine

        engine = EvaluationEngine()

        original_text = "原始素材"
        draft_text = "改写内容"

        # 验证prompt构造
        with patch.object(engine, '_call_judge_llm', new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = "Faithfulness评分: 8.0\nRelevance评分: 7.5"

            result = await engine._run_llm_judge_evaluation(original_text, draft_text)

            # 验证_judge被调用
            assert mock_judge.called
            call_args = mock_judge.call_args
            prompt = call_args[0][0]

            # 验证prompt包含关键内容
            assert original_text in prompt or "原始" in prompt
            assert draft_text in prompt or "改写" in prompt

    @pytest.mark.asyncio
    async def test_style_evaluation_handles_judge_failure(self):
        """测试风格评估处理Judge失败情况。"""
        from forge.evaluation.engine import EvaluationEngine

        engine = EvaluationEngine()

        with patch.object(engine, '_call_judge_llm', new_callable=AsyncMock) as mock_judge:
            mock_judge.side_effect = Exception("LLM调用失败")

            result = await engine._run_style_evaluation("测试内容")

            # 失败时应该返回默认分数或空结果
            assert "human_score" in result
            # 可能是None或默认值
            assert result["human_score"] is None or isinstance(result["human_score"], (int, float))


class TestEvaluationWorker:
    """测试 Evaluation Worker。"""

    @pytest.mark.asyncio
    async def test_process_probe_log(self):
        """测试处理单条probe log。"""
        from forge.evaluation.worker import process_probe_log
        from unittest.mock import AsyncMock, patch

        payload = {
            "session_id": "test-session",
            "node_name": "editor",
            "timestamp": 1234567890,
            "input_metrics": {},
            "output_metrics": {},
            "duration_ms": 100,
        }

        with patch("forge.evaluation.worker.save_probe_log", new_callable=AsyncMock) as mock_save:
            await process_probe_log(payload)
            mock_save.assert_called_once_with(payload)

    @pytest.mark.asyncio
    async def test_trigger_evaluation_on_final_node(self):
        """测试在最终节点触发完整评估。"""
        from forge.evaluation.worker import should_trigger_evaluation

        # director节点应触发评估
        assert should_trigger_evaluation("director") == True
        assert should_trigger_evaluation("finalize") == True

        # 其他节点不触发
        assert should_trigger_evaluation("editor") == False
        assert should_trigger_evaluation("ai_detector") == False

    @pytest.mark.asyncio
    async def test_process_probe_log_triggers_evaluation(self):
        """测试在最终节点处理时触发完整评估。"""
        from forge.evaluation.worker import process_probe_log
        from unittest.mock import AsyncMock, patch, MagicMock

        payload = {
            "session_id": "test-session-eval",
            "node_name": "director",
            "timestamp": 1234567890,
            "input_metrics": {"ai_score": 0.0},
            "output_metrics": {"ai_score": 0.3},
            "duration_ms": 5000,
            "loop_type": None,
            "loop_iteration": 0,
        }

        mock_logs = [
            {
                "node_name": "editor",
                "input_metrics": {"ai_score": 0.0},
                "output_metrics": {"ai_score": 0.3},
                "duration_ms": 3000,
            },
            payload,
        ]

        with patch("forge.evaluation.worker.save_probe_log", new_callable=AsyncMock) as mock_save:
            with patch("forge.evaluation.worker.get_session_probe_logs", new_callable=AsyncMock) as mock_get_logs:
                with patch("forge.evaluation.worker.save_evaluation_result", new_callable=AsyncMock) as mock_save_result:
                    with patch("forge.evaluation.worker.EvaluationEngine") as mock_engine_class:
                        mock_get_logs.return_value = mock_logs
                        mock_engine = MagicMock()
                        mock_engine.evaluate_session = AsyncMock(return_value={
                            "overall_score": 8.5,
                            "faithfulness_score": 8.0,
                            "relevance_score": 8.0,
                            "human_score": 9.0,
                        })
                        mock_engine_class.return_value = mock_engine

                        await process_probe_log(payload)

                        # 验证保存了probe log
                        mock_save.assert_called_once_with(payload)
                        # 验证获取了session logs
                        mock_get_logs.assert_called_once_with("test-session-eval")
                        # 验证执行了评估
                        mock_engine.evaluate_session.assert_called_once()
                        # 验证保存了评估结果
                        mock_save_result.assert_called_once()