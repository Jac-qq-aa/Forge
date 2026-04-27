"""节点有效性计算器 + 循环ROI计算。

核心公式：
- Node Effectiveness = (output_score - input_score) / duration_seconds
- Loop ROI = (initial_score - final_score) / iterations
"""

import logging
from typing import Dict, Any, List
from collections import defaultdict

logger = logging.getLogger(__name__)


def calculate_node_effectiveness(probe_logs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """计算各节点有效性。

    Args:
        probe_logs: 探针日志列表，每个包含：
            - node_name: 节点名称
            - input_metrics: 输入指标（包含ai_score等）
            - output_metrics: 输出指标
            - duration_ms: 执行时长（毫秒）

    Returns:
        节点有效性字典，格式：
        {
            "node_name": {
                "effectiveness": float,  # 有效性分数
                "input_score": float,    # 输入AI分数
                "output_score": float,   # 输出AI分数
                "duration_seconds": float,  # 执行时长（秒）
                "call_count": int,       # 调用次数
            },
            ...
        }
    """
    if not probe_logs:
        return {}

    # 聚合同一节点的数据
    node_data: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "total_effectiveness": 0.0,
        "total_duration": 0.0,
        "input_scores": [],
        "output_scores": [],
        "call_count": 0,
    })

    for log in probe_logs:
        node_name = log.get("node_name", "unknown")
        input_metrics = log.get("input_metrics", {})
        output_metrics = log.get("output_metrics", {})
        duration_ms = log.get("duration_ms", 0)

        # 获取AI分数（用于计算有效性）
        input_score = input_metrics.get("ai_score", 0.0)
        output_score = output_metrics.get("ai_score", 0.0)

        # 转换为秒
        duration_seconds = duration_ms / 1000.0 if duration_ms > 0 else 0.0

        # 计算单次有效性
        if duration_seconds > 0:
            effectiveness = (output_score - input_score) / duration_seconds
        else:
            effectiveness = 0.0

        node_data[node_name]["total_effectiveness"] += effectiveness
        node_data[node_name]["total_duration"] += duration_seconds
        node_data[node_name]["input_scores"].append(input_score)
        node_data[node_name]["output_scores"].append(output_score)
        node_data[node_name]["call_count"] += 1

    # 计算最终结果
    result = {}
    for node_name, data in node_data.items():
        call_count = data["call_count"]
        avg_effectiveness = data["total_effectiveness"] / call_count if call_count > 0 else 0.0

        result[node_name] = {
            "effectiveness": round(avg_effectiveness, 4),
            "input_score": round(sum(data["input_scores"]) / call_count, 4) if call_count > 0 else 0.0,
            "output_score": round(sum(data["output_scores"]) / call_count, 4) if call_count > 0 else 0.0,
            "duration_seconds": round(data["total_duration"], 2),
            "call_count": call_count,
        }

    logger.debug(f"[ProbeCalculator] Node effectiveness: {result}")
    return result


def calculate_loop_roi(probe_logs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """计算循环ROI。

    Args:
        probe_logs: 探针日志列表，每个包含：
            - loop_type: 循环类型（如"humanize_loop"）
            - loop_iteration: 迭代次数
            - input_metrics: 输入指标
            - output_metrics: 输出指标
            - duration_ms: 执行时长

    Returns:
        循环ROI字典，格式：
        {
            "loop_type": {
                "roi": float,          # ROI分数
                "initial_score": float,  # 初始AI分数
                "final_score": float,    # 最终AI分数
                "iterations": int,       # 迭代次数
                "total_duration_seconds": float,  # 总执行时长
            },
            ...
        }
    """
    if not probe_logs:
        return {}

    # 按循环类型分组
    loop_data: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "iterations": set(),
        "initial_score": None,
        "final_score": None,
        "total_duration": 0.0,
        "all_scores": [],  # 按迭代顺序记录分数
    })

    for log in probe_logs:
        loop_type = log.get("loop_type")
        if not loop_type:
            continue

        loop_iteration = log.get("loop_iteration", 0)
        input_metrics = log.get("input_metrics", {})
        output_metrics = log.get("output_metrics", {})
        duration_ms = log.get("duration_ms", 0)

        input_score = input_metrics.get("ai_score", 0.0)
        output_score = output_metrics.get("ai_score", 0.0)

        data = loop_data[loop_type]
        data["iterations"].add(loop_iteration)
        data["total_duration"] += duration_ms / 1000.0

        # 记录每次迭代的分数变化
        data["all_scores"].append({
            "iteration": loop_iteration,
            "input_score": input_score,
            "output_score": output_score,
        })

    # 如果没有循环数据，返回空字典
    if not loop_data:
        return {}

    # 计算每个循环的ROI
    result = {}
    for loop_type, data in loop_data.items():
        if not data["all_scores"]:
            continue

        # 按迭代排序
        sorted_scores = sorted(data["all_scores"], key=lambda x: x["iteration"])

        # 获取初始和最终分数
        # 初始分数 = 第一个迭代的输入分数
        initial_score = sorted_scores[0]["input_score"]
        # 最终分数 = 最后一个迭代的输出分数
        final_score = sorted_scores[-1]["output_score"]

        # 计算迭代次数
        max_iteration = max(data["iterations"]) if data["iterations"] else 0

        # ROI = (initial_score - final_score) / iterations
        # 注意：对于AI检测分数，降低是好事，所以ROI高表示效果好
        if max_iteration > 0:
            roi = (initial_score - final_score) / max_iteration
        else:
            roi = 0.0

        result[loop_type] = {
            "roi": round(roi, 4),
            "initial_score": round(initial_score, 4),
            "final_score": round(final_score, 4),
            "iterations": max_iteration,
            "total_duration_seconds": round(data["total_duration"], 2),
        }

    logger.debug(f"[ProbeCalculator] Loop ROI: {result}")
    return result


def get_aggregate_metrics(probe_logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """获取聚合指标。

    Args:
        probe_logs: 探针日志列表

    Returns:
        聚合指标字典，包含：
            - total_nodes: 总节点数
            - total_duration_seconds: 总执行时长
            - loop_types: 循环类型列表
            - average_ai_score_change: 平均AI分数变化
    """
    if not probe_logs:
        return {
            "total_nodes": 0,
            "total_duration_seconds": 0.0,
            "loop_types": [],
            "average_ai_score_change": 0.0,
        }

    total_duration = 0.0
    total_ai_change = 0.0
    ai_change_count = 0
    loop_types = set()

    for log in probe_logs:
        duration_ms = log.get("duration_ms", 0)
        total_duration += duration_ms / 1000.0

        input_score = log.get("input_metrics", {}).get("ai_score", 0.0)
        output_score = log.get("output_metrics", {}).get("ai_score", 0.0)

        if input_score > 0 or output_score > 0:
            total_ai_change += abs(output_score - input_score)
            ai_change_count += 1

        loop_type = log.get("loop_type")
        if loop_type:
            loop_types.add(loop_type)

    return {
        "total_nodes": len(probe_logs),
        "total_duration_seconds": round(total_duration, 2),
        "loop_types": list(loop_types),
        "average_ai_score_change": round(total_ai_change / ai_change_count, 4) if ai_change_count > 0 else 0.0,
    }