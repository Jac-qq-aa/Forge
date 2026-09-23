"""使用 DeepEval 评价最终文章的表达质量。

该模块只接收最终文章与目标平台，不接收事实清单、来源或 RAG 上下文。
事实正确性由深度生成工作流中的 Critic 独立负责。
"""

import asyncio
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional

# 禁用 DeepEval 匿名遥测；本项目不接入 Confident AI。
os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "1"

from deepeval.metrics import GEval
from deepeval.metrics.g_eval import Rubric
from deepeval.models import OpenAIModel
from deepeval.test_case import LLMTestCase, SingleTurnParams

from forge.config import JUDGE_API_KEY, JUDGE_API_URL, JUDGE_MODEL, LLM_TIMEOUT


METRIC_WEIGHTS = {
    "structure": 0.40,
    "writing": 0.35,
    "naturalness": 0.25,
}

METRIC_THRESHOLDS = {
    "structure": 0.70,
    "writing": 0.70,
    "naturalness": 0.70,
}


@dataclass(frozen=True)
class ArticleExpressionResult:
    """文章表达质量评估结果，所有分数范围均为 0.0-1.0。"""

    overall_score: float
    structure_score: float
    writing_score: float
    naturalness_score: float
    ai_style_risk: float
    metric_reasons: Dict[str, str]
    issues: list[Dict[str, str]]
    model: str
    status: str = "completed"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ArticleExpressionEvaluator:
    """封装 Forge 专用三维 G-Eval，隔离 DeepEval 的具体配置。"""

    def __init__(
        self,
        metrics: Optional[Mapping[str, Any]] = None,
        model_name: str = JUDGE_MODEL,
    ) -> None:
        self.model_name = model_name
        self.metrics = dict(metrics) if metrics is not None else self._build_metrics()
        expected = set(METRIC_WEIGHTS)
        if set(self.metrics) != expected:
            raise ValueError(f"评估指标必须且只能包含: {sorted(expected)}")

    async def evaluate(
        self,
        final_article: str,
        target_platform: str,
    ) -> ArticleExpressionResult:
        """并行执行结构、文笔和自然度评估。"""
        article = final_article.strip()
        if not article:
            raise ValueError("最终文章不能为空")

        platform = target_platform.strip() or "未指定平台"
        test_case = LLMTestCase(
            input=f"评价这篇面向“{platform}”发布的最终文章的表达质量。",
            actual_output=article,
        )

        metric_names = list(METRIC_WEIGHTS)
        await asyncio.wait_for(
            asyncio.gather(
                *(
                    self.metrics[name].a_measure(
                        test_case,
                        _show_indicator=False,
                    )
                    for name in metric_names
                )
            ),
            timeout=LLM_TIMEOUT,
        )

        scores: Dict[str, float] = {}
        reasons: Dict[str, str] = {}
        issues: list[Dict[str, str]] = []
        for name in metric_names:
            metric = self.metrics[name]
            if metric.score is None:
                raise RuntimeError(f"{name} 指标未返回分数")
            score = round(min(max(float(metric.score), 0.0), 1.0), 4)
            reason = str(metric.reason or "未返回评分理由")
            scores[name] = score
            reasons[name] = reason
            if score < METRIC_THRESHOLDS[name]:
                issues.append({"dimension": name, "reason": reason})

        overall = round(
            sum(scores[name] * METRIC_WEIGHTS[name] for name in metric_names),
            4,
        )
        naturalness = scores["naturalness"]
        return ArticleExpressionResult(
            overall_score=overall,
            structure_score=scores["structure"],
            writing_score=scores["writing"],
            naturalness_score=naturalness,
            ai_style_risk=round(1.0 - naturalness, 4),
            metric_reasons=reasons,
            issues=issues,
            model=self.model_name,
        )

    def _build_metrics(self) -> Dict[str, GEval]:
        if not JUDGE_API_KEY:
            raise ValueError("缺少 JUDGE_API_KEY 或 QWEN_API_KEY，无法运行文章表达质量评估")

        model = OpenAIModel(
            model=self.model_name,
            api_key=JUDGE_API_KEY,
            base_url=JUDGE_API_URL,
            temperature=0,
        )
        common_params = [SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT]

        return {
            "structure": GEval(
                name="文章整体结构与连贯性",
                evaluation_params=common_params,
                evaluation_steps=[
                    "识别文章中心主线，检查开头、主体和结尾是否各自承担明确作用。",
                    "检查章节与段落顺序、论述推进和上下文过渡是否自然。",
                    "找出重复、突然跳转或与主线脱节的段落，并在理由中给出具体文本证据。",
                    "只评价结构与连贯性，忽略事实、数字、引用和来源是否正确。",
                ],
                rubric=self._rubric("结构与连贯性"),
                model=model,
                threshold=METRIC_THRESHOLDS["structure"],
                async_mode=True,
            ),
            "writing": GEval(
                name="文章文笔与可读性",
                evaluation_params=common_params,
                evaluation_steps=[
                    "检查表达是否清晰、准确、具体，阅读负担是否符合目标平台。",
                    "检查句式变化、语言节奏、用词和段落密度是否自然。",
                    "找出冗余、堆砌、拗口或缺乏表现力的表达，并在理由中给出具体文本证据。",
                    "只评价文字表达，忽略事实、数字、引用和来源是否正确。",
                ],
                rubric=self._rubric("文笔与可读性"),
                model=model,
                threshold=METRIC_THRESHOLDS["writing"],
                async_mode=True,
            ),
            "naturalness": GEval(
                name="文章自然度与低 AI 模板感",
                evaluation_params=common_params,
                evaluation_steps=[
                    "检查是否存在套路化开头结尾、机械枚举和过度整齐的段落或句式。",
                    "检查是否滥用通用过渡词、空泛总结、口号和缺乏信息量的正确废话。",
                    "评价文本是否具有具体表达、自然变化和与平台匹配的鲜明语气。",
                    "高分代表表达自然、AI 模板感低；不要判断作者身份，也不要评价事实正确性。",
                    "对明显模板化问题在理由中给出具体文本证据。",
                ],
                rubric=self._rubric("自然度"),
                model=model,
                threshold=METRIC_THRESHOLDS["naturalness"],
                async_mode=True,
            ),
        }

    @staticmethod
    def _rubric(dimension: str) -> list[Rubric]:
        return [
            Rubric(
                score_range=(0, 2),
                expected_outcome=f"{dimension}存在严重问题，显著影响阅读",
            ),
            Rubric(
                score_range=(3, 4),
                expected_outcome=f"{dimension}较差，存在多处明显问题",
            ),
            Rubric(
                score_range=(5, 6),
                expected_outcome=f"{dimension}基本合格，但仍有清晰改进空间",
            ),
            Rubric(
                score_range=(7, 8),
                expected_outcome=f"{dimension}良好，仅有少量局部问题",
            ),
            Rubric(
                score_range=(9, 10),
                expected_outcome=f"{dimension}优秀，几乎没有可感知问题",
            ),
        ]
