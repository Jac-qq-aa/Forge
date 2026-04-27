"""Forge 评估系统 - 异步旁路评估模块。

核心组件：
- Probe: 轻量节点探针，记录执行特征
- Worker: 后台消费队列，执行评估
- Engine: RAGAS + LLM Judge 评估引擎
- Storage: PostgreSQL 存储层
"""

# 延迟导入，避免循环依赖
__all__ = [
    "probe_node",
    "extract_key_metrics",
    "with_probe",
    "EvaluationStorage",
    "get_evaluation_storage",
    "EvaluationEngine",
]


def __getattr__(name: str):
    """延迟导入模块成员。"""
    if name == "probe_node" or name == "extract_key_metrics":
        from .probe import probe_node, extract_key_metrics

        return probe_node if name == "probe_node" else extract_key_metrics
    elif name == "with_probe":
        from .probe_decorator import with_probe

        return with_probe
    elif name == "EvaluationStorage" or name == "get_evaluation_storage":
        from .storage import EvaluationStorage, get_evaluation_storage

        return EvaluationStorage if name == "EvaluationStorage" else get_evaluation_storage
    elif name == "EvaluationEngine":
        from .engine import EvaluationEngine

        return EvaluationEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")