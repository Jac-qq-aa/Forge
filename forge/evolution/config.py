# forge/evolution/config.py

"""自进化系统配置参数。"""

from dataclasses import dataclass


@dataclass
class EvolutionConfig:
    """自进化系统配置。"""

    # === 触发配置 ===
    THRESHOLD_COUNT: int = 10          # 阈值触发样本数
    SCHEDULE_HOUR: int = 2             # 定时触发时间（凌晨2点）
    MIN_INTERVAL_HOURS: int = 12       # 最小间隔（防止频繁触发）

    # === 入库条件 ===
    QUALITY_THRESHOLD: float = 0.70    # 综合质量评分阈值
    HUMAN_SCORE_THRESHOLD: float = 0.80  # 人性化评分阈值
    MAX_REVISION_COUNT: int = 3        # 最大修改轮数（少修改 = 高质量）

    # === Prompt变更配置 ===
    AUTO_ACTIVATE: bool = False        # 是否自动激活（False=需人工确认）
    MIN_SAMPLE_FOR_ANALYSIS: int = 5   # 最少样本数才能分析
    KEEP_OLD_VERSION_DAYS: int = 30    # 保留旧版本天数

    # === RAG检索配置 ===
    QUALITY_CASE_TOP_K: int = 2        # 检索案例数量
    VECTOR_DIMENSION: int = 384        # 向量维度（all-MiniLM-L6-v2）

    # === Milvus配置 ===
    QUALITY_CASES_COLLECTION: str = "quality_cases_vectors"


# 全局配置实例
_evolution_config: EvolutionConfig = None


def get_evolution_config() -> EvolutionConfig:
    """获取配置实例。"""
    global _evolution_config
    if _evolution_config is None:
        _evolution_config = EvolutionConfig()
    return _evolution_config