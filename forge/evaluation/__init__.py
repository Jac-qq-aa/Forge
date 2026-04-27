"""Forge 评估系统 - 异步旁路评估模块。

核心组件：
- Probe: 轻量节点探针，记录执行特征
- Worker: 后台消费队列，执行评估
- Engine: RAGAS + LLM Judge 评估引擎
- Storage: PostgreSQL 存储层
- ProbeCalculator: 节点有效性 + 循环ROI计算
"""

# 延迟导入，避免循环依赖
__all__ = [
    "probe_node",
    "extract_key_metrics",
    "with_probe",
    "EvaluationStorage",
    "get_evaluation_storage",
    "EvaluationEngine",
    "get_evaluation_engine",
    "calculate_node_effectiveness",
    "calculate_loop_roi",
    "get_aggregate_metrics",
    # Worker
    "process_probe_log",
    "should_trigger_evaluation",
    "run_evaluation_worker",
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
    elif name == "EvaluationEngine" or name == "get_evaluation_engine":
        from .engine import EvaluationEngine, get_evaluation_engine

        return EvaluationEngine if name == "EvaluationEngine" else get_evaluation_engine
    elif name in ("calculate_node_effectiveness", "calculate_loop_roi", "get_aggregate_metrics"):
        from .probe_calculator import calculate_node_effectiveness, calculate_loop_roi, get_aggregate_metrics

        if name == "calculate_node_effectiveness":
            return calculate_node_effectiveness
        elif name == "calculate_loop_roi":
            return calculate_loop_roi
        else:
            return get_aggregate_metrics
    elif name in ("process_probe_log", "should_trigger_evaluation", "run_evaluation_worker"):
        from .worker import process_probe_log, should_trigger_evaluation, run_evaluation_worker

        if name == "process_probe_log":
            return process_probe_log
        elif name == "should_trigger_evaluation":
            return should_trigger_evaluation
        else:
            return run_evaluation_worker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")