# forge/evolution/engine.py

"""LLM驱动的Prompt优化引擎。

核心职责：
- 分析微调反馈模式，识别常见问题
- 提取高质量案例特征
- 对比新旧模板效果
"""

import json
import logging
from typing import Dict, Any, List, Optional

from forge.tools.llm_client import LLMClient
from .config import get_evolution_config

logger = logging.getLogger(__name__)


# ============================================================================
# 分析Prompt模板
# ============================================================================

ANALYSIS_SYSTEM_PROMPT = """你是文章生成系统的优化专家。

你的任务是分析用户在微调阶段的反馈，识别常见问题模式，并提出Prompt模板改进建议。

## 分析维度

1. **长度问题** - 用户反馈"太长"、"太短"、"精简"等
2. **语气问题** - 用户反馈"太口语化"、"太正式"、"不够专业"等
3. **结构问题** - 用户反馈"段落不清"、"逻辑混乱"、"开头不吸引人"等
4. **内容问题** - 用户反馈"偏离主题"、"缺少XX内容"、"观点不明确"等
5. **风格问题** - 用户反馈"不够生动"、"太平淡"、"缺乏真实感"等

## 输出格式

请严格按以下JSON格式输出：
{
  "patterns": [
    {
      "type": "length_issue",
      "frequency": 0.6,
      "example_feedbacks": ["太长了", "精简一下"],
      "affected_articles": 6
    }
  ],
  "root_cause_analysis": "当前prompt要求800-1200字，但用户实际偏好500-800字...",
  "recommendations": [
    {
      "change_type": "modify_length_requirement",
      "current_value": "800-1200字",
      "suggested_value": "500-800字",
      "reason": "60%用户反馈过长"
    }
  ],
  "prompt_changes": {
    "system_prompt_delta": "增加：'用户偏好简洁表达'",
    "user_prompt_delta": "将'800-1200字'改为'500-800字'"
  }
}

注意：只输出JSON，不要额外文字"""

ANALYSIS_USER_PROMPT_TEMPLATE = """请分析以下 {count} 篇文章的微调反馈：

## 当前Prompt模板
- System Prompt: {current_system_prompt}
- User Prompt Template: {current_user_prompt}

## 微调反馈数据
{feedback_data}

请分析并输出改进建议。"""

EXTRACT_CASE_SYSTEM_PROMPT = """你是内容质量分析专家。

从高质量文章中提取关键特征，形成可供后续参考的案例摘要。

输出格式：
{
  "key_changes": [
    "初稿段落过长，用户要求精简后改为短段落结构",
    "开头增加了口语化引入'说实话'"
  ],
  "style_features": {
    "tone": "口语化但有深度",
    "structure": "打破三段论，用疑问结尾",
    "opening_pattern": "说实话/有意思的是"
  },
  "summary": "职场吐槽风格，开头口语化引入，短段落叙述，疑问结尾"
}

注意：只输出JSON"""

EXTRACT_CASE_USER_PROMPT = """请分析以下高质量文章：

## 初版草稿
{original_draft}

## 定稿版本
{final_draft}

## 微调对话历史
{tuning_history}

请提取关键特征。"""


class EvolutionEngine:
    """LLM驱动的Prompt优化引擎."""

    def __init__(self):
        self.llm = LLMClient()
        self.config = get_evolution_config()

    async def analyze_feedback_patterns(
        self,
        tuning_histories: List[Dict],
        quality_scores: List[float],
        prompt_template: Dict,
    ) -> Optional[Dict]:
        """分析反馈模式，识别常见问题.

        Args:
            tuning_histories: 多篇文章的微调对话列表
            quality_scores: 对应的质量评分列表
            prompt_template: 当前使用的模板

        Returns:
            分析结果字典，包含:
            - patterns: 问题模式列表
            - root_cause_analysis: 根因分析
            - recommendations: 改进建议
            - prompt_changes: Prompt修改建议
        """
        logger.info(f"[EvolutionEngine] Analyzing {len(tuning_histories)} feedback histories")

        # 格式化反馈数据
        feedback_data = self._format_feedback_data(tuning_histories, quality_scores)

        # 构建分析prompt
        user_prompt = ANALYSIS_USER_PROMPT_TEMPLATE.format(
            count=len(tuning_histories),
            current_system_prompt=prompt_template.get("system_prompt", "")[:500],
            current_user_prompt=prompt_template.get("user_prompt_template", "")[:500],
            feedback_data=feedback_data,
        )

        try:
            response = await self.llm.chat_with_retry(user_prompt, ANALYSIS_SYSTEM_PROMPT)

            # 解析JSON响应
            result = self._parse_json_response(response)

            if result and self._validate_analysis_result(result):
                logger.info(f"[EvolutionEngine] Analysis completed, found {len(result.get('patterns', []))} patterns")
                return result
            else:
                logger.warning("[EvolutionEngine] Analysis result validation failed")
                return None

        except Exception as e:
            logger.error(f"[EvolutionEngine] analyze_feedback_patterns failed: {e}")
            return None

    def _format_feedback_data(
        self,
        tuning_histories: List[Dict],
        quality_scores: List[float],
    ) -> str:
        """格式化反馈数据用于分析.

        Args:
            tuning_histories: 微调对话列表
            quality_scores: 质量评分列表

        Returns:
            格式化的文本
        """
        parts = []

        for i, (history, score) in enumerate(zip(tuning_histories, quality_scores), 1):
            parts.append(f"### 文章{i} (质量评分: {score:.2f})")

            # 提取用户反馈（只取用户消息）
            user_feedbacks = []
            for msg in history:
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if content and len(content) < 200:  # 只取简短反馈
                        user_feedbacks.append(content)

            if user_feedbacks:
                parts.append("用户反馈:")
                for fb in user_feedbacks[:5]:  # 最多5条
                    parts.append(f"  - {fb}")
            else:
                parts.append("用户反馈: 无明确修改请求")

            parts.append("")

        return "\n".join(parts)

    async def extract_quality_case(
        self,
        session: Dict,
        tuning_history: List[Dict],
    ) -> Optional[Dict]:
        """从高质量文章中提取精炼案例.

        Args:
            session: 会话数据（包含初稿和定稿）
            tuning_history: 微调对话历史

        Returns:
            案例特征字典，包含:
            - key_changes: 关键修改点
            - style_features: 风格特征
            - summary: 案例摘要
        """
        logger.info("[EvolutionEngine] Extracting quality case features")

        original_draft = session.get("draft_v1", "")
        final_draft = session.get("final_draft") or session.get("current_draft", "")

        if not original_draft or not final_draft:
            logger.warning("[EvolutionEngine] Missing drafts for case extraction")
            return None

        # 格式化微调历史
        tuning_summary = self._format_tuning_history(tuning_history)

        user_prompt = EXTRACT_CASE_USER_PROMPT.format(
            original_draft=original_draft[:1000],  # 截断避免过长
            final_draft=final_draft[:1000],
            tuning_history=tuning_summary,
        )

        try:
            response = await self.llm.chat_with_retry(user_prompt, EXTRACT_CASE_SYSTEM_PROMPT)

            result = self._parse_json_response(response)

            if result:
                logger.info(f"[EvolutionEngine] Case extracted: {result.get('summary', '')[:50]}...")
                return result
            else:
                return None

        except Exception as e:
            logger.error(f"[EvolutionEngine] extract_quality_case failed: {e}")
            return None

    def _format_tuning_history(self, tuning_history: List[Dict]) -> str:
        """格式化微调历史.

        Args:
            tuning_history: 微调对话列表

        Returns:
            格式化的文本
        """
        parts = []

        for msg in tuning_history[:10]:  # 最多10条
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:200]  # 截断

            if role == "user":
                parts.append(f"用户: {content}")
            elif role == "agent":
                is_question = msg.get("is_question", False)
                if is_question:
                    parts.append(f"Agent(回答): {content[:100]}...")
                else:
                    parts.append(f"Agent(修改): [文章已修改]")

        return "\n".join(parts) if parts else "无微调对话"

    async def compare_template_effectiveness(
        self,
        old_template_id: str,
        new_template_id: str,
        n_samples: int = 20,
    ) -> Optional[Dict]:
        """对比新旧模板的效果差异.

        Args:
            old_template_id: 旧模板ID
            new_template_id: 新模板ID
            n_samples: 样本数量

        Returns:
            对比结果字典
        """
        from .storage import get_evolution_storage

        storage = get_evolution_storage()

        # 获取模板数据
        old_template = await storage.get_template_by_id(old_template_id)
        new_template = await storage.get_template_by_id(new_template_id)

        if not old_template or not new_template:
            logger.warning("[EvolutionEngine] Template not found for comparison")
            return None

        # 提取统计数据
        old_stats = {
            "avg_quality_score": old_template.get("avg_quality_score", 0),
            "avg_human_score": old_template.get("avg_human_score", 0),
            "avg_revision_count": old_template.get("avg_revision_count", 0),
            "sample_count": old_template.get("sample_count", 0),
        }

        new_stats = {
            "avg_quality_score": new_template.get("avg_quality_score", 0),
            "avg_human_score": new_template.get("avg_human_score", 0),
            "avg_revision_count": new_template.get("avg_revision_count", 0),
            "sample_count": new_template.get("sample_count", 0),
        }

        # 计算差异
        result = {
            "old_template_id": old_template_id,
            "new_template_id": new_template_id,
            "old_stats": old_stats,
            "new_stats": new_stats,
            "quality_score_delta": new_stats["avg_quality_score"] - old_stats["avg_quality_score"],
            "human_score_delta": new_stats["avg_human_score"] - old_stats["avg_human_score"],
            "revision_count_delta": old_stats["avg_revision_count"] - new_stats["avg_revision_count"],  # 负向指标，越少越好
            "recommendation": None,
        }

        # 生成建议
        if result["quality_score_delta"] > 0.05:
            result["recommendation"] = "new_template_better"
        elif result["quality_score_delta"] < -0.05:
            result["recommendation"] = "consider_rollback"
        else:
            result["recommendation"] = "no_significant_change"

        logger.info(f"[EvolutionEngine] Comparison: delta={result['quality_score_delta']:.3f}")
        return result

    def _parse_json_response(self, response: str) -> Optional[Dict]:
        """解析LLM的JSON响应.

        Args:
            response: LLM返回的文本

        Returns:
            解析后的字典，解析失败返回None
        """
        try:
            # 尝试直接解析
            return json.loads(response)
        except json.JSONDecodeError:
            # 尝试提取JSON部分
            import re
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass

            logger.warning(f"[EvolutionEngine] Failed to parse JSON: {response[:100]}...")
            return None

    def _validate_analysis_result(self, result: Dict) -> bool:
        """验证分析结果结构.

        Args:
            result: 分析结果字典

        Returns:
            True 如果结构有效
        """
        required_keys = ["patterns", "prompt_changes"]

        for key in required_keys:
            if key not in result:
                logger.warning(f"[EvolutionEngine] Missing required key: {key}")
                return False

        return True

    def apply_prompt_changes(
        self,
        current_template: Dict,
        changes: Dict,
    ) -> Dict:
        """应用Prompt修改建议.

        Args:
            current_template: 当前模板
            changes: 修改建议

        Returns:
            新模板内容
        """
        new_system_prompt = current_template.get("system_prompt", "")
        new_user_prompt = current_template.get("user_prompt_template", "")

        # 应用system_prompt_delta
        system_delta = changes.get("system_prompt_delta", "")
        if system_delta:
            # 添加新内容（delta是增量）
            new_system_prompt = f"{new_system_prompt}\n\n{system_delta}"

        # 应用user_prompt_delta
        user_delta = changes.get("user_prompt_delta", "")
        if user_delta:
            # 这里需要更智能的处理，比如替换特定文本
            # 简单实现：作为补充添加
            new_user_prompt = f"{new_user_prompt}\n\n补充要求：{user_delta}"

        return {
            "system_prompt": new_system_prompt,
            "user_prompt_template": new_user_prompt,
        }


# 全局实例
_evolution_engine: Optional[EvolutionEngine] = None


def get_evolution_engine() -> EvolutionEngine:
    """获取引擎实例。"""
    global _evolution_engine
    if _evolution_engine is None:
        _evolution_engine = EvolutionEngine()
    return _evolution_engine