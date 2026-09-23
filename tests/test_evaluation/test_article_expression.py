from unittest.mock import AsyncMock, patch

import pytest

from forge.evaluation.article_expression import (
    ArticleExpressionEvaluator,
    ArticleExpressionResult,
)


class FakeMetric:
    def __init__(self, score: float, reason: str) -> None:
        self.score = score
        self.reason = reason
        self.test_cases = []

    async def a_measure(self, test_case, _show_indicator=False):
        self.test_cases.append(test_case)
        return self.score


@pytest.mark.asyncio
async def test_article_expression_weighted_score_and_ai_style_risk():
    metrics = {
        "structure": FakeMetric(0.8, "结构清楚"),
        "writing": FakeMetric(0.6, "部分句子冗长"),
        "naturalness": FakeMetric(0.4, "枚举和总结较模板化"),
    }
    evaluator = ArticleExpressionEvaluator(metrics=metrics, model_name="test-judge")

    result = await evaluator.evaluate("标题\n\n正文内容。", "wechat_article")

    assert result.overall_score == 0.63
    assert result.ai_style_risk == 0.6
    assert result.model == "test-judge"
    assert [issue["dimension"] for issue in result.issues] == ["writing", "naturalness"]


@pytest.mark.asyncio
async def test_article_expression_input_excludes_fact_context():
    metrics = {
        "structure": FakeMetric(0.9, "良好"),
        "writing": FakeMetric(0.9, "良好"),
        "naturalness": FakeMetric(0.9, "良好"),
    }
    evaluator = ArticleExpressionEvaluator(metrics=metrics)
    article = "这是最终文章，不包含额外评估上下文。"

    await evaluator.evaluate(article, "zhihu_article")

    for metric in metrics.values():
        test_case = metric.test_cases[0]
        assert test_case.actual_output == article
        assert "事实清单" not in test_case.input
        assert "RAG" not in test_case.input
        assert "来源" not in test_case.input


@pytest.mark.asyncio
async def test_article_expression_rejects_empty_article():
    metrics = {
        "structure": FakeMetric(0.9, "良好"),
        "writing": FakeMetric(0.9, "良好"),
        "naturalness": FakeMetric(0.9, "良好"),
    }
    evaluator = ArticleExpressionEvaluator(metrics=metrics)

    with pytest.raises(ValueError, match="最终文章不能为空"):
        await evaluator.evaluate("  ", "zhihu_article")


def test_deepeval_rubric_uses_supported_schema():
    rubrics = ArticleExpressionEvaluator._rubric("结构")

    assert rubrics[0].score_range == (0, 2)
    assert rubrics[-1].score_range == (9, 10)
    assert "结构" in rubrics[-1].expected_outcome


@pytest.mark.asyncio
async def test_queue_payload_contains_no_article_body():
    from forge.evaluation.article_queue import enqueue_article_expression_evaluation

    redis_client = AsyncMock()
    with patch(
        "forge.evaluation.article_queue._get_redis_client",
        return_value=redis_client,
    ):
        digest = await enqueue_article_expression_evaluation(
            session_id="7ca1a37a-cbd4-48e9-a3d8-78b137ba610d",
            final_article="仅用于计算摘要的正文",
            target_platform="zhihu_article",
        )

    payload = redis_client.lpush.await_args.args[1]
    assert "仅用于计算摘要的正文" not in payload
    assert digest in payload


@pytest.mark.asyncio
async def test_queue_failure_is_non_blocking():
    from forge.evaluation.article_queue import try_enqueue_article_expression_evaluation

    with patch(
        "forge.evaluation.article_queue.enqueue_article_expression_evaluation",
        new=AsyncMock(side_effect=RuntimeError("Redis unavailable")),
    ):
        queued = await try_enqueue_article_expression_evaluation(
            session_id="7ca1a37a-cbd4-48e9-a3d8-78b137ba610d",
            final_article="最终文章",
            target_platform="zhihu_article",
        )

    assert queued is False


@pytest.mark.asyncio
async def test_worker_skips_completed_same_article():
    from forge.evaluation.worker import process_article_finalized

    payload = {
        "event_type": "article_finalized",
        "session_id": "7ca1a37a-cbd4-48e9-a3d8-78b137ba610d",
        "article_hash": "queued-hash",
        "target_platform": "zhihu_article",
    }
    with (
        patch(
            "forge.evaluation.worker.get_final_article",
            new=AsyncMock(return_value="最终文章"),
        ),
        patch(
            "forge.evaluation.worker.get_completed_article_expression",
            new=AsyncMock(return_value={"status": "completed"}),
        ),
        patch("forge.evaluation.worker.ArticleExpressionEvaluator") as evaluator_class,
    ):
        await process_article_finalized(payload)

    evaluator_class.assert_not_called()


@pytest.mark.asyncio
async def test_worker_saves_expression_result():
    from forge.evaluation.worker import process_article_finalized

    result = ArticleExpressionResult(
        overall_score=0.8,
        structure_score=0.8,
        writing_score=0.8,
        naturalness_score=0.8,
        ai_style_risk=0.2,
        metric_reasons={"structure": "好", "writing": "好", "naturalness": "好"},
        issues=[],
        model="test-judge",
    )
    evaluator = AsyncMock()
    evaluator.model_name = "test-judge"
    evaluator.evaluate.return_value = result
    payload = {
        "event_type": "article_finalized",
        "session_id": "7ca1a37a-cbd4-48e9-a3d8-78b137ba610d",
        "article_hash": "queued-hash",
        "target_platform": "zhihu_article",
    }
    save_mock = AsyncMock()

    with (
        patch(
            "forge.evaluation.worker.get_final_article",
            new=AsyncMock(return_value="最终文章"),
        ),
        patch(
            "forge.evaluation.worker.get_completed_article_expression",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "forge.evaluation.worker.ArticleExpressionEvaluator",
            return_value=evaluator,
        ),
        patch("forge.evaluation.worker.save_article_expression", new=save_mock),
    ):
        await process_article_finalized(payload)

    saved = save_mock.await_args.args[2]
    assert saved["overall_score"] == 0.8
    assert saved["ai_style_risk"] == 0.2
