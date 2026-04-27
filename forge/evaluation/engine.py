"""评估引擎 - RAGAS + LLM Judge 评估。

核心功能：
- evaluate_session: 执行完整评估
- _run_ragas_evaluation: RAGAS评估（或LLM Judge fallback）
- _run_style_evaluation: 风格质量LLM Judge
- _calculate_overall: 综合评分（权重：Faithfulness 40% + Relevance 30% + Human 30%）
"""

import logging
import re
from typing import Dict, Any, List, Optional

from forge.config import (
    EVAL_FAITHFULNESS_WEIGHT,
    EVAL_RELEVANCE_WEIGHT,
    EVAL_HUMAN_WEIGHT,
)
from forge.evaluation.probe_calculator import (
    calculate_node_effectiveness,
    calculate_loop_roi,
    get_aggregate_metrics,
)
from forge.evaluation.storage import EvaluationStorage, get_evaluation_storage

logger = logging.getLogger(__name__)

# 尝试导入RAGAS，如果失败则使用LLM Judge fallback
try:
    from ragas import evaluate
    from ragas.metrics import faithfulness, answer_relevancy
    RAGAS_AVAILABLE = True
except ImportError:
    RAGAS_AVAILABLE = False
    logger.warning("[EvalEngine] RAGAS not available, will use LLM Judge fallback")


class EvaluationEngine:
    """评估引擎 - 执行RAGAS和LLM Judge评估。"""

    def __init__(self, storage: Optional[EvaluationStorage] = None):
        """初始化评估引擎。

        Args:
            storage: 存储实例（可选，默认使用全局实例）
        """
        self.storage = storage or get_evaluation_storage()
        self._judge_client = None

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
            original_text: 原始素材文本（用于RAGAS评估）
            draft_text: 改写后的草稿文本（用于风格评估）

        Returns:
            评估结果字典，包含：
                - overall_score: 总体评分
                - faithfulness_score: 事实一致性评分
                - relevance_score: 相关性评分
                - human_score: 人性化评分
                - node_effectiveness: 节点效率分析
                - loop_roi: 循环ROI分析
                - metrics_detail: 详细指标
                - status: 状态
        """
        logger.info(f"[EvalEngine] Starting evaluation for session: {session_id}")

        result = {
            "session_id": session_id,
            "overall_score": None,
            "faithfulness_score": None,
            "relevance_score": None,
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

            # 4. 执行RAGAS评估（如果有原始文本）
            if original_text and draft_text:
                try:
                    ragas_result = await self._run_ragas_evaluation(original_text, draft_text)
                    result["faithfulness_score"] = ragas_result.get("faithfulness_score")
                    result["relevance_score"] = ragas_result.get("relevance_score")
                except Exception as e:
                    logger.error(f"[EvalEngine] RAGAS evaluation failed: {e}")

            # 5. 执行风格质量评估（如果有草稿文本）
            if draft_text:
                try:
                    style_result = await self._run_style_evaluation(draft_text)
                    result["human_score"] = style_result.get("human_score")
                    if "style_detail" in style_result:
                        result["metrics_detail"]["style"] = style_result["style_detail"]
                except Exception as e:
                    logger.error(f"[EvalEngine] Style evaluation failed: {e}")

            # 6. 计算综合评分
            result["overall_score"] = self._calculate_overall(result)

            result["status"] = "completed"
            logger.info(f"[EvalEngine] Evaluation completed: overall={result['overall_score']}")

        except Exception as e:
            logger.error(f"[EvalEngine] Evaluation failed: {e}")
            result["status"] = "failed"
            result["error"] = str(e)

        # 7. 保存结果
        try:
            await self._save_result(session_id, result)
        except Exception as e:
            logger.error(f"[EvalEngine] Failed to save result: {e}")

        return result

    async def _run_ragas_evaluation(
        self,
        original_text: str,
        draft_text: str,
    ) -> Dict[str, Any]:
        """执行RAGAS评估或LLM Judge fallback。

        Args:
            original_text: 原始素材文本
            draft_text: 改写后的草稿文本

        Returns:
            评估结果字典
        """
        if RAGAS_AVAILABLE:
            try:
                return await self._run_ragas_native(original_text, draft_text)
            except Exception as e:
                logger.warning(f"[EvalEngine] RAGAS native failed, falling back to LLM Judge: {e}")

        # Fallback to LLM Judge
        return await self._run_llm_judge_evaluation(original_text, draft_text)

    async def _run_ragas_native(
        self,
        original_text: str,
        draft_text: str,
    ) -> Dict[str, Any]:
        """执行原生RAGAS评估。

        Args:
            original_text: 原始素材文本
            draft_text: 改写后的草稿文本

        Returns:
            RAGAS评估结果
        """
        from datasets import Dataset

        # 构建RAGAS数据集
        # RAGAS需要特定的数据格式
        data = {
            "question": [original_text[:500]],  # 使用原始文本作为"问题"上下文
            "answer": [draft_text[:2000]],     # 改写后的文本作为"答案"
            "contexts": [[original_text[:2000]]],  # 上下文
        }

        dataset = Dataset.from_dict(data)

        # 执行评估
        results = evaluate(
            dataset,
            metrics=[faithfulness, answer_relevancy],
        )

        return {
            "faithfulness_score": float(results.get("faithfulness", 0.0)),
            "relevance_score": float(results.get("answer_relevancy", 0.0)),
        }

    async def _run_llm_judge_evaluation(
        self,
        original_text: str,
        draft_text: str,
    ) -> Dict[str, Any]:
        """使用LLM Judge进行评估（RAGAS fallback）。

        Args:
            original_text: 原始素材文本
            draft_text: 改写后的草稿文本

        Returns:
            评估结果字典
        """
        prompt = f"""请评估以下内容改写的质量。评估维度：1) 忠实度（Faithfulness）：改写内容是否忠实于原始素材，没有添加虚假信息或遗漏关键信息
2) 相关性（Relevance）：改写内容是否保持与原始主题的相关性原始素材（前1000字）：
{original_text[:1000]}改写内容（前2000字）：
{draft_text[:2000]}请按1-10分进行评分，并以以下格式返回：Faithfulness评分: [分数]
Relevance评分: [分数]

简要说明评分理由。"""

        response = await self._call_judge_llm(prompt)

        faithfulness_score = self.parse_score(response, "Faithfulness")
        relevance_score = self.parse_score(response, "Relevance")

        return {
            "faithfulness_score": faithfulness_score,
            "relevance_score": relevance_score,
            "raw_response": response,
        }

    async def _run_style_evaluation(
        self,
        draft_text: str,
    ) -> Dict[str, Any]:
        """执行风格质量LLM Judge评估。

        Args:
            draft_text: 改写后的草稿文本

        Returns:
            评估结果字典
        """
        prompt = f"""请评估以下内容的人性化程度。评估维度：1) 自然度：语言是否自然流畅，不显得机械化
2) 多样性：句式和用词是否多样化，避免重复模式
3) 情感表达：是否有适当的情感色彩和个性化表达改写内容（前2000字）：
{draft_text[:2000]}请按1-10分进行评分，并以以下格式返回：Human评分: [分数]简要说明评分理由。"""

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
            分数值（1-10），如果未找到返回None
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
            rf"相关性评分[:：]\s*(\d+\.?\d*)" if label.lower() == "relevance" else None,
        ]

        for pattern in patterns:
            if pattern is None:
                continue
            match = re.search(pattern, response, re.IGNORECASE)
            if match:
                try:
                    score = float(match.group(1))
                    # 限制在1-10范围内
                    return min(max(score, 1.0), 10.0)
                except (ValueError, IndexError):
                    continue

        logger.warning(f"[EvalEngine] Could not parse score for label '{label}' in response")
        return None

    def _calculate_overall(self, result: Dict[str, Any]) -> Optional[float]:
        """计算综合评分。

        权重：
        - Faithfulness: 40%
        - Relevance: 30%
        - Human: 30%

        Args:
            result: 评估结果字典

        Returns:
            综合评分（1-10），如果无法计算返回None
        """
        faithfulness = result.get("faithfulness_score")
        relevance = result.get("relevance_score")
        human = result.get("human_score")

        # 收集有效的评分
        valid_scores = []
        weights = []

        if faithfulness is not None:
            valid_scores.append(faithfulness * EVAL_FAITHFULNESS_WEIGHT)
            weights.append(EVAL_FAITHFULNESS_WEIGHT)

        if relevance is not None:
            valid_scores.append(relevance * EVAL_RELEVANCE_WEIGHT)
            weights.append(EVAL_RELEVANCE_WEIGHT)

        if human is not None:
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