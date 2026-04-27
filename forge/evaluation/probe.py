"""节点探针 - 轻量数据采集 + Redis队列推送。

设计目标：耗时 < 10ms，不阻塞主流程。
"""

import json
import time
import logging
import redis
from typing import Dict, Any, Optional

from forge.config import REDIS_HOST, REDIS_PORT, EVAL_REDIS_DB, EVAL_QUEUE_NAME

logger = logging.getLogger(__name__)

# Redis连接（使用db=1，与session db=0隔离）
_redis_client: Optional[redis.Redis] = None


def _get_redis_client() -> redis.Redis:
    """获取Redis客户端（延迟初始化）。"""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=EVAL_REDIS_DB,
            decode_responses=False,  # 保持bytes，json.dumps后直接存
        )
        logger.info(f"[Probe] Redis client initialized: db={EVAL_REDIS_DB}")
    return _redis_client


def probe_node(
    node_name: str,
    state_before: Dict[str, Any],
    state_after: Dict[str, Any],
    duration_ms: int,
    loop_info: Optional[Dict[str, Any]] = None,
) -> None:
    """节点探针 - 打包数据并push到Redis队列。

    Args:
        node_name: 节点名称（editor, ai_detector, humanizer_editor等）
        state_before: 节点执行前的状态
        state_after: 节点执行后的状态（返回的result dict）
        duration_ms: 节点执行耗时（毫秒）
        loop_info: 循环信息 {"loop_type": "humanize_loop", "iteration": 1}

    设计原则：
    - 极轻量：只做数据打包 + push，不做任何计算
    - 不阻塞：Redis lpush是O(1)操作，耗时 < 10ms
    - 数据精简：只存关键指标，不存完整文章内容
    """
    session_id = state_before.get("session_id", "unknown")

    # 提取关键指标
    input_metrics = extract_key_metrics(state_before)
    output_metrics = extract_key_metrics(state_after)

    # 构建payload
    payload = {
        "session_id": session_id,
        "node_name": node_name,
        "timestamp": time.time(),
        "input_metrics": input_metrics,
        "output_metrics": output_metrics,
        "duration_ms": duration_ms,
        "loop_type": loop_info.get("loop_type") if loop_info else None,
        "loop_iteration": loop_info.get("iteration", 0) if loop_info else 0,
        "metadata": {
            "target_platform": state_after.get("target_platform"),
            "mode": state_after.get("mode", "fast"),
        },
    }

    # Push到Redis队列
    try:
        client = _get_redis_client()
        client.lpush(EVAL_QUEUE_NAME, json.dumps(payload, ensure_ascii=False))
        logger.debug(f"[Probe] {node_name} pushed to queue: session={session_id}")
    except Exception as e:
        logger.warning(f"[Probe] Redis push failed: {e}, data discarded")
        # 失败时静默丢弃，不影响主流程


def extract_key_metrics(state: Dict[str, Any]) -> Dict[str, Any]:
    """从状态中提取关键指标。

    Args:
        state: 节点状态字典

    Returns:
        关键指标字典，包含：
        - ai_score: AI检测分数
        - revision_count: Reviewer迭代次数
        - humanize_revisions: Humanizer迭代次数
        - reflection_revision_count: Reflection迭代次数
        - draft_length: 草稿长度
        - draft_text: 草稿前1000字（用于评估）
        - is_approved: 是否通过Critic
        - stage: 当前阶段
        - has_rag_context: 是否有RAG素材
        - has_fact_sheet: 是否有Fact Sheet
    """
    draft = state.get("rewritten_draft", "") or state.get("current_draft", "") or ""

    return {
        "ai_score": state.get("ai_score", 0.0),
        "revision_count": state.get("revision_count", 0),
        "humanize_revisions": state.get("humanize_revisions", 0),
        "reflection_revision_count": state.get("reflection_revision_count", 0),
        "draft_length": len(draft),
        "draft_text": draft[:1000] if draft else "",
        "is_approved": state.get("is_approved", False),
        "stage": state.get("stage"),
        "has_rag_context": bool(state.get("rag_context")),
        "has_fact_sheet": bool(state.get("fact_sheet")),
    }