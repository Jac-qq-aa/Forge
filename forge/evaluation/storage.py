"""评估存储层 - PostgreSQL持久化。

表结构：
- probe_logs: 节点探针日志
- evaluation_results: 评估结果汇总
"""

import json
import logging
import asyncpg
from typing import Dict, Any, List, Optional
from datetime import datetime
from uuid import UUID

from forge.storage.pg_client import get_pg_pool, is_valid_uuid

logger = logging.getLogger(__name__)


class EvaluationStorage:
    """评估数据存储类。"""

    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None

    async def _get_pool(self) -> asyncpg.Pool:
        """获取PG连接池。"""
        if self._pool is None:
            self._pool = await get_pg_pool()
        return self._pool

    async def save_probe_log(self, payload: Dict[str, Any]) -> None:
        """保存单条probe log。

        Args:
            payload: 探针数据包，包含：
                - session_id: 会话ID（UUID字符串）
                - node_name: 节点名称
                - timestamp: Unix时间戳
                - input_metrics: 输入指标
                - output_metrics: 输出指标
                - duration_ms: 执行时长
                - loop_type: 循环类型（可选）
                - loop_iteration: 循环迭代次数（可选）
                - metadata: 元数据（可选）
        """
        session_id = payload["session_id"]

        # 验证UUID格式
        if not is_valid_uuid(session_id):
            logger.warning(f"[EvalStorage] Invalid session_id format: {session_id}")
            return

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                # 使用数据库原子操作计算node_sequence，避免竞态条件
                await conn.execute(
                    """
                    INSERT INTO probe_logs (
                        session_id, node_name, node_sequence, timestamp,
                        input_metrics, output_metrics, duration_ms,
                        loop_type, loop_iteration, metadata
                    ) VALUES (
                        $1, $2,
                        COALESCE((
                            SELECT MAX(node_sequence) FROM probe_logs WHERE session_id = $1
                        ), 0) + 1,
                        $3, $4, $5, $6, $7, $8, $9
                    )
                    """,
                    UUID(session_id),
                    payload["node_name"],
                    datetime.fromtimestamp(payload["timestamp"]),
                    json.dumps(payload.get("input_metrics") or {}),  # asyncpg需要JSON字符串
                    json.dumps(payload.get("output_metrics") or {}),  # asyncpg需要JSON字符串
                    payload.get("duration_ms"),
                    payload.get("loop_type"),
                    payload.get("loop_iteration", 0),
                    json.dumps(payload.get("metadata") or {}),  # asyncpg需要JSON字符串
                )
            except asyncpg.PostgresError as e:
                logger.error(f"[EvalStorage] save_probe_log failed: {e}")
                return

        logger.debug(f"[EvalStorage] Probe log saved: {payload['node_name']}")

    async def get_session_probe_logs(self, session_id: str) -> List[Dict[str, Any]]:
        """获取session的所有probe logs。

        Args:
            session_id: 会话ID（UUID字符串）

        Returns:
            probe log列表，按node_sequence排序
        """
        if not is_valid_uuid(session_id):
            logger.warning(f"[EvalStorage] Invalid session_id format: {session_id}")
            return []

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                rows = await conn.fetch(
                    """
                    SELECT id, session_id, node_name, node_sequence, timestamp,
                           input_metrics, output_metrics, duration_ms,
                           loop_type, loop_iteration, metadata
                    FROM probe_logs
                    WHERE session_id = $1
                    ORDER BY node_sequence ASC
                    """,
                    UUID(session_id),
                )
            except asyncpg.PostgresError as e:
                logger.error(f"[EvalStorage] get_session_probe_logs failed: {e}")
                return []

        return [self._row_to_dict(row) for row in rows]

    async def save_evaluation_result(
        self,
        session_id: str,
        result: Dict[str, Any]
    ) -> None:
        """保存评估结果。

        Args:
            session_id: 会话ID（UUID字符串）
            result: 评估结果字典，包含：
                - overall_score: 总体评分
                - summarization_score: 忠实度评分（0-1）- 存入metrics_detail
                - rubrics_score: 改写质量评分（1-5）- 存入metrics_detail
                - human_score: 人性化评分
                - metrics_detail: 详细指标
                - node_effectiveness: 节点效率分析
                - loop_roi: 循环ROI分析
                - status: 状态

        注意：数据库表保持原有结构（faithfulness_score, relevance_score），
        新指标（summarization_score, rubrics_score）存入metrics_detail JSONB字段，
        以实现向后兼容。
        """
        if not is_valid_uuid(session_id):
            logger.warning(f"[EvalStorage] Invalid session_id format: {session_id}")
            return

        # 将新指标合并到metrics_detail中
        metrics_detail = result.get("metrics_detail") or {}
        if result.get("summarization_score") is not None:
            metrics_detail["summarization_score"] = result["summarization_score"]
        if result.get("rubrics_score") is not None:
            metrics_detail["rubrics_score"] = result["rubrics_score"]

        # 向后兼容：将新指标映射到旧字段（用于兼容现有前端）
        # summarization_score -> faithfulness_score (范围转换)
        # rubrics_score -> relevance_score (范围转换)
        faithfulness_compat = None
        relevance_compat = None
        if result.get("summarization_score") is not None:
            faithfulness_compat = result["summarization_score"]  # 0-1范围
        if result.get("rubrics_score") is not None:
            # rubrics_score是1-5范围，转换为0-1范围
            relevance_compat = (result["rubrics_score"] - 1) / 4.0 if result["rubrics_score"] else None

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO evaluation_results (
                        session_id, overall_score, faithfulness_score,
                        relevance_score, human_score, metrics_detail,
                        node_effectiveness, loop_roi, evaluated_at, status
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    ON CONFLICT (session_id) DO UPDATE SET
                        overall_score = $2,
                        faithfulness_score = $3,
                        relevance_score = $4,
                        human_score = $5,
                        metrics_detail = $6,
                        node_effectiveness = $7,
                        loop_roi = $8,
                        evaluated_at = $9,
                        status = $10
                    """,
                    UUID(session_id),
                    result.get("overall_score"),
                    faithfulness_compat,  # 兼容映射
                    relevance_compat,     # 兼容映射
                    result.get("human_score"),
                    json.dumps(metrics_detail),  # 包含新指标
                    json.dumps(result.get("node_effectiveness") or {}),
                    json.dumps(result.get("loop_roi") or {}),
                    datetime.now(),
                    result.get("status", "completed"),
                )
            except asyncpg.PostgresError as e:
                logger.error(f"[EvalStorage] save_evaluation_result failed: {e}")
                return

        logger.info(f"[EvalStorage] Evaluation result saved: {session_id}")

    async def get_evaluation_result(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取评估结果。

        Args:
            session_id: 会话ID（UUID字符串）

        Returns:
            评估结果字典，不存在返回None
        """
        if not is_valid_uuid(session_id):
            logger.warning(f"[EvalStorage] Invalid session_id format: {session_id}")
            return None

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    """
                    SELECT id, session_id, overall_score, faithfulness_score,
                           relevance_score, human_score, metrics_detail,
                           node_effectiveness, loop_roi, created_at,
                           evaluated_at, status, error_message
                    FROM evaluation_results
                    WHERE session_id = $1
                    """,
                    UUID(session_id),
                )
            except asyncpg.PostgresError as e:
                logger.error(f"[EvalStorage] get_evaluation_result failed: {e}")
                return None

        if not row:
            return None
        return self._row_to_dict(row)

    async def get_evaluation_stats(self, limit: int = 100) -> List[Dict[str, Any]]:
        """获取评估统计数据（后台报告）。

        Args:
            limit: 最大返回数量

        Returns:
            评估结果列表
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                query = """
                    SELECT session_id, overall_score, faithfulness_score,
                           relevance_score, human_score, status, created_at
                    FROM evaluation_results
                    WHERE status = 'completed'
                    ORDER BY created_at DESC
                    LIMIT $1
                """
                rows = await conn.fetch(query, limit)
            except asyncpg.PostgresError as e:
                logger.error(f"[EvalStorage] get_evaluation_stats failed: {e}")
                return []

        return [self._row_to_dict(row) for row in rows]

    def _row_to_dict(self, row: asyncpg.Record) -> Dict[str, Any]:
        """转换数据库行到字典。"""
        result = dict(row)

        # 处理UUID
        if "id" in result and isinstance(result["id"], UUID):
            result["id"] = str(result["id"])
        if "session_id" in result and isinstance(result["session_id"], UUID):
            result["session_id"] = str(result["session_id"])

        # 处理datetime
        for key in ["timestamp", "created_at", "evaluated_at"]:
            if key in result and result[key] is not None:
                if isinstance(result[key], datetime):
                    result[key] = result[key].isoformat()

        # 处理JSONB字段 - asyncpg返回的是dict/list，不需要解析
        # 但如果返回的是字符串，需要解析
        json_fields = ["input_metrics", "output_metrics", "metadata",
                       "metrics_detail", "node_effectiveness", "loop_roi"]
        for key in json_fields:
            if key in result and isinstance(result[key], str):
                try:
                    result[key] = json.loads(result[key])
                except json.JSONDecodeError:
                    pass

        return result


# 全局实例
_eval_storage: Optional[EvaluationStorage] = None


def get_evaluation_storage() -> EvaluationStorage:
    """获取存储实例。"""
    global _eval_storage
    if _eval_storage is None:
        _eval_storage = EvaluationStorage()
    return _eval_storage


# 便捷函数
async def save_probe_log(payload: Dict[str, Any]) -> None:
    """保存probe log的便捷函数。"""
    storage = get_evaluation_storage()
    await storage.save_probe_log(payload)


async def get_session_probe_logs(session_id: str) -> List[Dict[str, Any]]:
    """获取session probe logs的便捷函数。"""
    storage = get_evaluation_storage()
    return await storage.get_session_probe_logs(session_id)


async def save_evaluation_result(session_id: str, result: Dict[str, Any]) -> None:
    """保存评估结果的便捷函数。"""
    storage = get_evaluation_storage()
    await storage.save_evaluation_result(session_id, result)


async def get_evaluation_result(session_id: str) -> Optional[Dict[str, Any]]:
    """获取评估结果的便捷函数。"""
    storage = get_evaluation_storage()
    return await storage.get_evaluation_result(session_id)


async def get_evaluation_stats(limit: int = 100) -> List[Dict[str, Any]]:
    """获取评估统计的便捷函数。"""
    storage = get_evaluation_storage()
    return await storage.get_evaluation_stats(limit)