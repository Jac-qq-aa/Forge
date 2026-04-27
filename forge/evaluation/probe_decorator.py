"""节点探针装饰器 - 自动记录节点执行前后的状态。"""

import functools
import logging
import time
from typing import Callable, Dict, Any, Optional

from forge.evaluation.probe import probe_node

logger = logging.getLogger(__name__)

# 循环类型到state key的映射
LOOP_ITERATION_KEY_MAP = {
    "humanize_loop": "humanize_revisions",
    "review_loop": "revision_count",
    "reflection_loop": "reflection_revision_count",
}


def with_probe(
    node_name: str,
    loop_type: Optional[str] = None,
) -> Callable:
    """节点探针装饰器。

    自动记录节点执行前后的state，计算执行耗时，并调用probe_node将数据push到队列。

    Args:
        node_name: 节点名称（如 editor, ai_detector, humanizer_editor 等）
        loop_type: 循环类型（如 "humanize_loop", "review_loop" 等）
                   如果提供，会自动计算当前迭代次数

    Returns:
        装饰器函数

    用法示例:
        @with_probe("editor")
        async def editor_node(state: Dict[str, Any]) -> Dict[str, Any]:
            # 节点逻辑
            return state

        @with_probe("humanizer_editor", loop_type="humanize_loop")
        async def humanizer_node(state: Dict[str, Any]) -> Dict[str, Any]:
            # 带循环信息的节点
            return state
    """

    def decorator(func: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        @functools.wraps(func)
        async def wrapper(state: Dict[str, Any]) -> Dict[str, Any]:
            start_time = time.time()
            state_before = dict(state)

            # 执行原函数
            result = await func(state)

            # 计算耗时
            duration_ms = int((time.time() - start_time) * 1000)

            # 计算循环信息
            loop_info = None
            if loop_type:
                # 使用映射表获取实际的迭代计数字段
                iteration_key = LOOP_ITERATION_KEY_MAP.get(loop_type, f"{loop_type}_iterations")
                current_iterations = state_before.get(iteration_key, 0)
                loop_info = {
                    "loop_type": loop_type,
                    "iteration": current_iterations + 1,
                }

            # 探针记录（失败不影响主流程）
            try:
                probe_node(
                    node_name=node_name,
                    state_before=state_before,
                    state_after=result,
                    duration_ms=duration_ms,
                    loop_info=loop_info,
                )
            except Exception as e:
                logger.warning(f"[ProbeDecorator] probe_node failed for {node_name}: {e}")
                # 继续执行，不影响主流程

            return result

        return wrapper

    return decorator