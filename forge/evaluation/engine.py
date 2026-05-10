"""评估引擎 - 改写场景专用评估。

核心功能：
- evaluate_session: 执行完整评估
- _run_summarization_eval: SummarizationScore评估（改写是否保留原文核心信息）
- _run_rubrics_eval: RubricsScore评估（改写质量维度）
- _run_style_evaluation: 风格质量LLM Judge（人性化程度）
- _calculate_overall: 综合评分（权重：Summarization 35% + Rubrics 30% + Human 35%）

评估模型：使用通义千问 qwen-max，避免依赖 OpenAI API。
"""

import logging
import re
from typing import Dict, Any, List, Optional

from forge.config import (
    EVAL_SUMMARIZATION_WEIGHT,
    EVAL_RUBRICS_WEIGHT,
    EVAL_HUMAN_WEIGHT,
    QWEN_API_KEY,
    QWEN_API_URL,
)
from forge.evaluation.probe_calculator import (
    calculate_node_effectiveness,
    calculate_loop_roi,
    get_aggregate_metrics,
)
from forge.evaluation.storage import EvaluationStorage, get_evaluation_storage

logger = logging.getLogger(__name__)

# 尝试导入RAGAS新版API
try:
    from ragas.dataset_schema import SingleTurnSample
    from ragas.metrics import SummarizationScore, RubricsScore
    from ragas.llms import LangchainLLMWrapper
    from langchain_openai import ChatOpenAI
    RAGAS_AVAILABLE = True
except ImportError:
    RAGAS_AVAILABLE = False
    logger.warning("[EvalEngine] RAGAS not available, will use LLM Judge fallback")

# 改写质量评分维度定义
REWRITE_RUBRICS = {
    "score1_description": "改写完全偏离原文核心观点，或有大量编造虚假信息，内容与原文无关。",
    "score2_description": "改写保留了部分核心观点，但有重大遗漏或关键信息丢失，风格转换过度导致内容失真。",
    "score3_description": "改写基本保留核心观点，风格转换适度，但有轻微信息偏差或表达不够自然。",
    "score4_description": "改写完整保留核心观点，表达方式有明显改变，信息准确，风格转换恰当。",
    "score5_description": "改写完美保留核心观点，表达方式原创且自然流畅，信息准确无误，风格转换出色。",
}


class EvaluationEngine:
    """评估引擎 - 执行改写场景专用评估。"""

    def __init__(self, storage: Optional[EvaluationStorage] = None):
        """初始化评估引擎。

        Args:
            storage: 存储实例（可选，默认使用全局实例）
        """
        self.storage = storage or get_evaluation_storage()
        self._judge_client = None
        self._evaluator_llm = None

    def _get_evaluator_llm(self):
        """获取评估LLM（使用通义千问qwen-max）。

        Returns:
            LangchainLLMWrapper包装的ChatOpenAI实例
        """
        if self._evaluator_llm is None:
            if not RAGAS_AVAILABLE:
                raise ImportError("RAGAS not available, cannot create evaluator LLM")
            self._evaluator_llm = LangchainLLMWrapper(ChatOpenAI(
                base_url=QWEN_API_URL,
                api_key=QWEN_API_KEY,
                model="qwen-max",
                timeout=60.0,
            ))
            logger.info("[EvalEngine] Evaluator LLM initialized: qwen-max")
        return self._evaluator_llm

    async def evaluate_session(
        self,
        session_id: str,
        probe_logs: List[Dict[str, Any]],
        original_text: Optional[str] = None,
        draft_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """执行完整评估。

        Args:
            session_id: 会话ID
            probe_logs: 探针日志列表
            original_text: 原始素材文本（用于改写评估）
            draft_text: 改写后的草稿文本（用于质量评估）

        Returns:
            评估结果字典，包含：
                - overall_score: 总体评分
                - summarization_score: 忠实度评分（0-1）
                - rubrics_score: 改写质量评分（1-5）
                - human_score: 人性化评分（1-10）
                - node_effectiveness: 节点效率分析
                - loop_roi: 循环ROI分析
                - metrics_detail: 详细指标
                - status: 状态
        """
        logger.info(f"[EvalEngine] Starting evaluation for session: {session_id}")

        result = {
            "session_id": session_id,
            "overall_score": None,
            "summarization_score": None,
            "rubrics_score": None,
            "human_score": None,
            "node_effectiveness": {},
            "loop_roi": {},
            "metrics_detail": {},
            "status": "pending",
        }

        try:
            # 1. 计算节点有效性
            result["node_effectiveness"] = calculate_node_effectiveness(probe_logs)

            # 2. 计算循环ROI
            result["loop_roi"] = calculate_loop_roi(probe_logs)

            # 3. 获取聚合指标
            result["metrics_detail"] = get_aggregate_metrics(probe_logs)

            # 4. 执行SummarizationScore评估（改写忠实度）
            if original_text and draft_text:
                try:
                    summ_result = await self._run_summarization_eval(original_text, draft_text)
                    result["summarization_score"] = summ_result.get("summarization_score")
                    if summ_result.get("raw_output"):
                        result["metrics_detail"]["summarization"] = summ_result["raw_output"]
                except Exception as e:
                    logger.error(f"[EvalEngine] Summarization evaluation failed: {e}")
                    # Fallback to LLM Judge
                    try:
                        fallback_result = await self._run_fallback_eval(original_text, draft_text)
                        result["summarization_score"] = fallback_result.get("faithfulness_score")
                        result["metrics_detail"]["fallback_used"] = True
                    except Exception as e2:
                        logger.error(f"[EvalEngine] Fallback evaluation also failed: {e2}")

            # 5. 执行RubricsScore评估（改写质量）
            if original_text and draft_text:
                try:
                    rubrics_result = await self._run_rubrics_eval(original_text, draft_text)
                    result["rubrics_score"] = rubrics_result.get("rubrics_score")
                    if rubrics_result.get("raw_output"):
                        result["metrics_detail"]["rubrics"] = rubrics_result["raw_output"]
                except Exception as e:
                    logger.error(f"[EvalEngine] Rubrics evaluation failed: {e}")

            # 6. 执行风格质量评估（人性化程度）
            if draft_text:
                try:
                    style_result = await self._run_style_evaluation(draft_text)
                    result["human_score"] = style_result.get("human_score")
                    if "style_detail" in style_result:
                        result["metrics_detail"]["style"] = style_result["style_detail"]
                except Exception as e:
                    logger.error(f"[EvalEngine] Style evaluation failed: {e}")

            # 7. 计算综合评分
            result["overall_score"] = self._calculate_overall(result)

            result["status"] = "completed"
            logger.info(f"[EvalEngine] Evaluation completed: overall={result['overall_score']}")

        except Exception as e:
            logger.error(f"[EvalEngine] Evaluation failed: {e}")
            result["status"] = "failed"
            result["error"] = str(e)

        # 8. 保存结果
        try:
            await self._save_result(session_id, result)
        except Exception as e:
            logger.error(f"[EvalEngine] Failed to save result: {e}")

        return result

    async def _run_summarization_eval(
        self,
        original_text: str,
        draft_text: str,
    ) -> Dict[str, Any]:
        """执行SummarizationScore评估。

        评估改写内容是否保留了原文的核心信息。
        使用RAGAS的SummarizationScore metric。

        Args:
            original_text: 原始素材文本
            draft_text: 改写后的草稿文本

        Returns:
            评估结果字典，包含summarization_score（0-1范围）
        """
        if not RAGAS_AVAILABLE:
            raise ImportError("RAGAS not available")

        # 截取文本长度，避免超出API限制
        original_trimmed = original_text[:2000] if len(original_text) > 2000 else original_text
        draft_trimmed = draft_text[:2000] if len(draft_text) > 2000 else draft_text

        # 原文过短时，SummarizationScore效果不佳，使用fallback
        if len(original_trimmed) < 200:
            logger.warning("[EvalEngine] Original text too short for SummarizationScore, using fallback")
            return await self._run_fallback_eval(original_text, draft_text)

        sample = SingleTurnSample(
            response=draft_trimmed,
            reference_contexts=[original_trimmed],
        )

        scorer = SummarizationScore(llm=self._get_evaluator_llm())

        try:
            score_result = await scorer.single_turn_ascore(sample)
            # RAGAS返回的是ScoreResult对象，需要提取实际分数
            if hasattr(score_result, 'value'):
                score = float(score_result.value)
            else:
                score = float(score_result) if score_result else 0.0

            logger.info(f"[EvalEngine] SummarizationScore: {score}")

            return {
                "summarization_score": round(score, 4),
                "raw_output": str(score_result) if score_result else None,
            }
        except Exception as e:
            logger.error(f"[EvalEngine] SummarizationScore execution failed: {e}")
            raise

    async def _run_rubrics_eval(
        self,
        original_text: str,
        draft_text: str,
    ) -> Dict[str, Any]:
        """执行RubricsScore评估。

        使用自定义评分规则评估改写质量维度。

        Args:
            original_text: 原始素材文本
            draft_text: 改写后的草稿文本

        Returns:
            评估结果字典，包含rubrics_score（1-5范围）
        """
        if not RAGAS_AVAILABLE:
            raise ImportError("RAGAS not available")

        original_trimmed = original_text[:2000] if len(original_text) > 2000 else original_text
        draft_trimmed = draft_text[:2000] if len(draft_text) > 2000 else draft_text

        sample = SingleTurnSample(
            response=draft_trimmed,
            reference=original_trimmed,
        )

        scorer = RubricsScore(rubrics=REWRITE_RUBRICS, llm=self._get_evaluator_llm())

        try:
            score_result = await scorer.single_turn_ascore(sample)
            # RAGAS返回的是ScoreResult对象，需要提取实际分数
            if hasattr(score_result, 'value'):
                score = float(score_result.value)
            else:
                score = float(score_result) if score_result else 0.0

            logger.info(f"[EvalEngine] RubricsScore: {score}")

            return {
                "rubrics_score": round(score, 2),
                "raw_output": str(score_result) if score_result else None,
            }
        except Exception as e:
            logger.error(f"[EvalEngine] RubricsScore execution failed: {e}")
            raise

    async def _run_fallback_eval(
        self,
        original_text: str,
        draft_text: str,
    ) -> Dict[str, Any]:
        """LLM Judge fallback评估（当RAGAS不可用时）。

        Args:
            original_text: 原始素材文本
            draft_text: 改写后的草稿文本

        Returns:
            评估结果字典
        """
        prompt = f"""请评估以下内容改写的忠实度。

评估维度：
忠实度（Faithfulness）：改写内容是否忠实于原始素材，保留核心观点，没有添加虚假信息或遗漏关键信息。

原始素材（前1000字）：
{original_text[:1000]}

改写内容（前2000字）：
{draft_text[:2000]}

请按0-1分进行评分（0表示完全不忠实，1表示完全忠实），并以以下格式返回：
Faithfulness评分: [分数]

简要说明评分理由。"""

        response = await self._call_judge_llm(prompt)
        faithfulness_score = self.parse_score(response, "Faithfulness")

        # 将1-10分转换为0-1分（用于兼容SummarizationScore范围）
        if faithfulness_score is not None:
            faithfulness_score = faithfulness_score / 10.0

        return {
            "faithfulness_score": faithfulness_score,
            "raw_response": response,
        }

    async def _run_style_evaluation(
        self,
        draft_text: str,
    ) -> Dict[str, Any]:
        """执行风格质量LLM Judge评估。

        评估人性化程度：自然度、多样性、情感表达。

        Args:
            draft_text: 改写后的草稿文本

        Returns:
            评估结果字典
        """
        prompt = f"""请评估以下内容的人性化程度。

评估维度：
1) 自然度：语言是否自然流畅，不显得机械化，避免"首先其次最后"等模板化表达
2) 多样性：句式和用词是否多样化，避免重复模式，长短句交替
3) 情感表达：是否有适当的情感色彩和个性化表达，如"说实话"、"有意思的是"等口语化表达

改写内容（前2000字）：
{draft_text[:2000]}

请按1-10分进行评分，并以以下格式返回：
Human评分: [分数]

简要说明评分理由。"""

        try:
            response = await self._call_judge_llm(prompt)
            human_score = self.parse_score(response, "Human")

            return {
                "human_score": human_score,
                "raw_response": response,
            }
        except Exception as e:
            logger.error(f"[EvalEngine] Style evaluation failed: {e}")
            return {
                "human_score": None,
                "error": str(e),
            }

    async def _call_judge_llm(self, prompt: str) -> str:
        """调用Judge LLM进行评估。

        Args:
            prompt: 评估提示

        Returns:
            LLM响应文本
        """
        if self._judge_client is None:
            from forge.tools.judge_llm_client import JudgeLLMClient
            self._judge_client = JudgeLLMClient()

        return await self._judge_client.judge(prompt)

    def parse_score(self, response: str, label: str) -> Optional[float]:
        """解析LLM响应中的分数。

        Args:
            response: LLM响应文本
            label: 分数标签（如"Faithfulness"、"Human"）

        Returns:
            分数值，如果未找到返回None
        """
        if not response:
            return None

        # 尝试多种匹配模式
        patterns = [
            # "Label评分: 8.5" 或 "Label评分：8.5"
            rf"{label}评分[:：]\s*(\d+\.?\d*)",
            # "Label: 8.5" 或 "Label：8.5"
            rf"{label}[:：]\s*(\d+\.?\d*)",
            # "Label评分 8.5"
            rf"{label}评分\s*(\d+\.?\d*)",
            # 带"分"后缀: "Label: 8.5分"
            rf"{label}[:：]\s*(\d+\.?\d*)分",
            # 带括号: "分数(1-10): 9"
            rf"{label}\([^)]*\)[:：]\s*(\d+\.?\d*)",
            # 中文标签支持
            rf"忠实度评分[:：]\s*(\d+\.?\d*)" if label.lower() == "faithfulness" else None,
            rf"人性化评分[:：]\s*(\d+\.?\d*)" if label.lower() == "human" else None,
        ]

        for pattern in patterns:
            if pattern is None:
                continue
            match = re.search(pattern, response, re.IGNORECASE)
            if match:
                try:
                    score = float(match.group(1))
                    return score
                except (ValueError, IndexError):
                    continue

        logger.warning(f"[EvalEngine] Could not parse score for label '{label}' in response")
        return None

    def _calculate_overall(self, result: Dict[str, Any]) -> Optional[float]:
        """计算综合评分。

        权重：
        - SummarizationScore（忠实度）: 35%
        - RubricsScore（改写质量）: 30%
        - Human Score（人性化）: 35%

        注意：
        - SummarizationScore范围：0-1，需要转换为1-10后计算
        - RubricsScore范围：1-5，需要转换为1-10后计算
        - Human Score范围：1-10

        Args:
            result: 评估结果字典

        Returns:
            综合评分（1-10），如果无法计算返回None
        """
        summarization = result.get("summarization_score")
        rubrics = result.get("rubrics_score")
        human = result.get("human_score")

        # 收集有效的评分（统一转换为1-10范围）
        valid_scores = []
        weights = []

        if summarization is not None:
            # SummarizationScore是0-1范围，转换为1-10
            normalized_summ = summarization * 10
            valid_scores.append(normalized_summ * EVAL_SUMMARIZATION_WEIGHT)
            weights.append(EVAL_SUMMARIZATION_WEIGHT)

        if rubrics is not None:
            # RubricsScore是1-5范围，转换为1-10
            normalized_rubrics = rubrics * 2
            valid_scores.append(normalized_rubrics * EVAL_RUBRICS_WEIGHT)
            weights.append(EVAL_RUBRICS_WEIGHT)

        if human is not None:
            # Human Score已经是1-10范围
            valid_scores.append(human * EVAL_HUMAN_WEIGHT)
            weights.append(EVAL_HUMAN_WEIGHT)

        if not valid_scores:
            return None

        # 计算加权平均
        total_weight = sum(weights)
        weighted_sum = sum(valid_scores)

        if total_weight > 0:
            return round(weighted_sum / total_weight, 2)
        return None

    async def _save_result(self, session_id: str, result: Dict[str, Any]) -> None:
        """保存评估结果到存储。

        Args:
            session_id: 会话ID
            result: 评估结果
        """
        await self.storage.save_evaluation_result(session_id, result)

    async def get_result(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取评估结果。

        Args:
            session_id: 会话ID

        Returns:
            评估结果字典，不存在返回None
        """
        return await self.storage.get_evaluation_result(session_id)


# 全局引擎实例
_eval_engine: Optional[EvaluationEngine] = None


def get_evaluation_engine() -> EvaluationEngine:
    """获取评估引擎实例。"""
    global _eval_engine
    if _eval_engine is None:
        _eval_engine = EvaluationEngine()
    return _eval_engine