# forge/evolution/quality_aggregator.py

"""质量评分聚合器。

计算综合质量评分，用于判断是否入库高质量案例。
"""

import logging
from typing import Optional

from .config import get_evolution_config

logger = logging.getLogger(__name__)


class QualityAggregator:
    """质量评分聚合器。"""

    # 权重配置
    WEIGHTS = {
        "human_score": 0.4,      # 人性化评分权重
        "revision_count": 0.3,   # 修改轮数权重（负向指标）
        "user_rating": 0.3,      # 用户评分权重（可选）
    }

    def calculate_quality_score(
        self,
        human_score: float,
        revision_count: int,
        user_rating: Optional[float] = None,
    ) -> float:
        """计算综合质量评分 (0.0-1.0)。

        Args:
            human_score: 人性化评分 (0.0-1.0)
            revision_count: 修改轮数（负向指标，越少越好）
            user_rating: 用户评分 (0.0-1.0，可选)

        Returns:
            综合质量评分 (0.0-1.0)
        """
        # 将 revision_count 转换为正向分数
        # 0次修改 = 1.0, 1-2次 = 0.8, 3次 = 0.6, 4+次 = 0.4
        revision_score = self._revision_to_score(revision_count)

        # 计算
        if user_rating is not None:
            # 有用户评分时使用完整权重
            quality_score = (
                human_score * self.WEIGHTS["human_score"]
                + revision_score * self.WEIGHTS["revision_count"]
                + user_rating * self.WEIGHTS["user_rating"]
            )
        else:
            # 无用户评分时，将用户评分权重分配给其他两项
            adjusted_weights = {
                "human_score": 0.57,  # (0.4 + 0.15)
                "revision_count": 0.43,  # (0.3 + 0.15)
            }
            quality_score = (
                human_score * adjusted_weights["human_score"]
                + revision_score * adjusted_weights["revision_count"]
            )

        logger.debug(
            f"[QualityAggregator] Score calculated: {quality_score:.3f} "
            f"(human={human_score:.3f}, revision={revision_count}, user_rating={user_rating})"
        )

        return min(1.0, max(0.0, quality_score))

    def _revision_to_score(self, revision_count: int) -> float:
        """将修改轮数转换为正向分数。

        Args:
            revision_count: 修改轮数

        Returns:
            正向分数 (0.0-1.0)
        """
        if revision_count <= 0:
            return 1.0
        elif revision_count == 1:
            return 0.9
        elif revision_count == 2:
            return 0.8
        elif revision_count == 3:
            return 0.6
        else:
            return 0.4

    def should_archive_as_quality_case(
        self,
        human_score: float,
        revision_count: int,
        quality_score: Optional[float] = None,
        user_rating: Optional[float] = None,
    ) -> bool:
        """判断是否满足入库条件。

        Args:
            human_score: 人性化评分
            revision_count: 修改轮数
            quality_score: 综合质量评分（可选，如果未提供会计算）
            user_rating: 用户评分（可选）

        Returns:
            True 如果满足入库条件
        """
        config = get_evolution_config()

        # 如果未提供 quality_score，先计算
        if quality_score is None:
            quality_score = self.calculate_quality_score(
                human_score=human_score,
                revision_count=revision_count,
                user_rating=user_rating,
            )

        # 检查各项阈值
        checks = [
            ("quality_score", quality_score >= config.QUALITY_THRESHOLD),
            ("human_score", human_score >= config.HUMAN_SCORE_THRESHOLD),
            ("revision_count", revision_count <= config.MAX_REVISION_COUNT),
        ]

        all_passed = all(check[1] for check in checks)

        if all_passed:
            logger.info(
                f"[QualityAggregator] Quality case criteria met: "
                f"quality={quality_score:.3f}, human={human_score:.3f}, revision={revision_count}"
            )
        else:
            failed_checks = [check[0] for check in checks if not check[1]]
            logger.debug(
                f"[QualityAggregator] Quality case criteria NOT met: "
                f"failed={failed_checks}"
            )

        return all_passed


# 全局实例
_quality_aggregator: Optional[QualityAggregator] = None


def get_quality_aggregator() -> QualityAggregator:
    """获取聚合器实例。"""
    global _quality_aggregator
    if _quality_aggregator is None:
        _quality_aggregator = QualityAggregator()
    return _quality_aggregator