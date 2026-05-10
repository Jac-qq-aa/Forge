# forge/evolution/storage.py

"""自进化系统存储层 - PostgreSQL持久化。

表结构：
- prompt_templates: Prompt模板版本表
- evolution_sessions: 自进化分析会话表
- quality_cases: 高质量案例表
"""

import json
import logging
import asyncpg
from typing import Dict, Any, List, Optional
from datetime import datetime
from uuid import UUID

from forge.storage.pg_client import get_pg_pool, is_valid_uuid

logger = logging.getLogger(__name__)


class EvolutionStorage:
    """自进化系统数据存储类。"""

    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None

    async def _get_pool(self) -> asyncpg.Pool:
        """获取PG连接池。"""
        if self._pool is None:
            self._pool = await get_pg_pool()
        return self._pool

    # ============================================================================
    # Prompt Templates
    # ============================================================================

    async def get_active_template(self, template_key: str) -> Optional[Dict[str, Any]]:
        """获取当前激活的模板。

        Args:
            template_key: 模板标识（如 "deep_content_generator")

        Returns:
            模板数据字典，不存在返回 None
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    """
                    SELECT id, template_key, version, is_active,
                           system_prompt, user_prompt_template,
                           change_reason, change_summary, previous_version_id,
                           avg_quality_score, avg_human_score, avg_revision_count,
                           sample_count, created_at, activated_at
                    FROM prompt_templates
                    WHERE template_key = $1 AND is_active = TRUE
                    """,
                    template_key,
                )
            except asyncpg.PostgresError as e:
                logger.error(f"[EvolutionStorage] get_active_template failed: {e}")
                return None

        if not row:
            return None
        return self._row_to_dict(row)

    async def get_template_by_id(self, template_id: str) -> Optional[Dict[str, Any]]:
        """根据ID获取模板。

        Args:
            template_id: 模板ID（UUID字符串）

        Returns:
            模板数据字典，不存在返回 None
        """
        if not is_valid_uuid(template_id):
            logger.warning(f"[EvolutionStorage] Invalid template_id: {template_id}")
            return None

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    """
                    SELECT id, template_key, version, is_active,
                           system_prompt, user_prompt_template,
                           change_reason, change_summary, previous_version_id,
                           avg_quality_score, avg_human_score, avg_revision_count,
                           sample_count, created_at, activated_at
                    FROM prompt_templates
                    WHERE id = $1
                    """,
                    UUID(template_id),
                )
            except asyncpg.PostgresError as e:
                logger.error(f"[EvolutionStorage] get_template_by_id failed: {e}")
                return None

        if not row:
            return None
        return self._row_to_dict(row)

    async def create_template(
        self,
        template_key: str,
        system_prompt: str,
        user_prompt_template: str,
        change_reason: str = None,
        change_summary: str = None,
        previous_version_id: str = None,
        is_active: bool = False,
    ) -> str:
        """创建新模板版本。

        Args:
            template_key: 模板标识
            system_prompt: 系统提示词
            user_prompt_template: 用户提示词模板
            change_reason: 修改原因
            change_summary: 修改摘要
            previous_version_id: 前一版本ID
            is_active: 是否激活

        Returns:
            新创建的模板ID
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                # 获取当前最大版本号
                max_version = await conn.fetchval(
                    """
                    SELECT MAX(version) FROM prompt_templates WHERE template_key = $1
                    """,
                    template_key,
                )
                new_version = (max_version or 0) + 1

                # 插入新模板
                row = await conn.fetchrow(
                    """
                    INSERT INTO prompt_templates (
                        template_key, version, is_active,
                        system_prompt, user_prompt_template,
                        change_reason, change_summary, previous_version_id
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    RETURNING id
                    """,
                    template_key,
                    new_version,
                    is_active,
                    system_prompt,
                    user_prompt_template,
                    change_reason,
                    change_summary,
                    UUID(previous_version_id) if previous_version_id and is_valid_uuid(previous_version_id) else None,
                )

                template_id = str(row["id"])
                logger.info(f"[EvolutionStorage] Template created: {template_key} v{new_version}")
                return template_id

            except asyncpg.PostgresError as e:
                logger.error(f"[EvolutionStorage] create_template failed: {e}")
                raise

    async def activate_template(self, template_id: str) -> bool:
        """激活指定模板（同时 deactivate 其他同 key 的模板）。

        Args:
            template_id: 模板ID（UUID字符串）

        Returns:
            True 如果成功激活
        """
        if not is_valid_uuid(template_id):
            logger.warning(f"[EvolutionStorage] Invalid template_id: {template_id}")
            return False

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                # 先获取模板的 key
                template_key = await conn.fetchval(
                    "SELECT template_key FROM prompt_templates WHERE id = $1",
                    UUID(template_id),
                )

                if not template_key:
                    logger.warning(f"[EvolutionStorage] Template not found: {template_id}")
                    return False

                # Deactivate 同 key 的所有模板
                await conn.execute(
                    """
                    UPDATE prompt_templates SET is_active = FALSE
                    WHERE template_key = $1
                    """,
                    template_key,
                )

                # Activate 目标模板
                await conn.execute(
                    """
                    UPDATE prompt_templates SET is_active = TRUE, activated_at = $2
                    WHERE id = $1
                    """,
                    UUID(template_id),
                    datetime.now(),
                )

                logger.info(f"[EvolutionStorage] Template activated: {template_id}")
                return True

            except asyncpg.PostgresError as e:
                logger.error(f"[EvolutionStorage] activate_template failed: {e}")
                return False

    async def update_template_stats(
        self,
        template_id: str,
        quality_score: float,
        human_score: float,
        revision_count: int,
    ) -> bool:
        """更新模板效果统计（增量更新平均值）。

        Args:
            template_id: 模板ID
            quality_score: 本次质量评分
            human_score: 本次人性化评分
            revision_count: 本次修改轮数

        Returns:
            True 如果成功更新
        """
        if not is_valid_uuid(template_id):
            logger.warning(f"[EvolutionStorage] Invalid template_id: {template_id}")
            return False

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                # 获取当前统计值
                row = await conn.fetchrow(
                    """
                    SELECT avg_quality_score, avg_human_score, avg_revision_count, sample_count
                    FROM prompt_templates WHERE id = $1
                    """,
                    UUID(template_id),
                )

                if not row:
                    return False

                # 计算新的平均值（增量更新）
                n = row["sample_count"] or 0
                old_q = row["avg_quality_score"] or 0.0
                old_h = row["avg_human_score"] or 0.0
                old_r = row["avg_revision_count"] or 0.0

                new_n = n + 1
                new_q = (old_q * n + quality_score) / new_n
                new_h = (old_h * n + human_score) / new_n
                new_r = (old_r * n + revision_count) / new_n

                # 更新
                await conn.execute(
                    """
                    UPDATE prompt_templates SET
                        avg_quality_score = $2,
                        avg_human_score = $3,
                        avg_revision_count = $4,
                        sample_count = $5
                    WHERE id = $1
                    """,
                    UUID(template_id),
                    new_q,
                    new_h,
                    new_r,
                    new_n,
                )

                logger.debug(f"[EvolutionStorage] Template stats updated: {template_id}, n={new_n}")
                return True

            except asyncpg.PostgresError as e:
                logger.error(f"[EvolutionStorage] update_template_stats failed: {e}")
                return False

    async def get_template_history(
        self,
        template_key: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """获取模板版本历史。

        Args:
            template_key: 模板标识
            limit: 最大返回数量

        Returns:
            版本列表（按版本号降序）
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                rows = await conn.fetch(
                    """
                    SELECT id, template_key, version, is_active,
                           change_reason, change_summary,
                           avg_quality_score, avg_human_score, sample_count,
                           created_at, activated_at
                    FROM prompt_templates
                    WHERE template_key = $1
                    ORDER BY version DESC
                    LIMIT $2
                    """,
                    template_key,
                    limit,
                )
            except asyncpg.PostgresError as e:
                logger.error(f"[EvolutionStorage] get_template_history failed: {e}")
                return []

        return [self._row_to_dict(row) for row in rows]

    # ============================================================================
    # Quality Cases
    # ============================================================================

    async def insert_quality_case(
        self,
        source_session_id: str,
        quality_score: float,
        human_score: float,
        revision_count: int,
        original_draft: str,
        final_draft: str,
        tuning_history: List[Dict],
        key_changes: str = None,
        target_platform: str = None,
    ) -> str:
        """入库高质量案例。

        Args:
            source_session_id: 来源 session ID
            quality_score: 综合质量评分
            human_score: 人性化评分
            revision_count: 修改轮数
            original_draft: 初版草稿
            final_draft: 定稿版本
            tuning_history: 微调对话历史
            key_changes: 关键修改点摘要
            target_platform: 目标平台

        Returns:
            新创建的案例ID
        """
        if not is_valid_uuid(source_session_id):
            logger.warning(f"[EvolutionStorage] Invalid session_id: {source_session_id}")
            return None

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    """
                    INSERT INTO quality_cases (
                        source_session_id, quality_score, human_score, revision_count,
                        original_draft, final_draft, tuning_history,
                        key_changes, target_platform
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    RETURNING id
                    """,
                    UUID(source_session_id),
                    quality_score,
                    human_score,
                    revision_count,
                    original_draft,
                    final_draft,
                    json.dumps(tuning_history),  # JSONB需要json.dumps
                    key_changes,
                    target_platform,
                )

                case_id = str(row["id"])
                logger.info(f"[EvolutionStorage] Quality case inserted: {case_id}")
                return case_id

            except asyncpg.PostgresError as e:
                # 处理唯一约束冲突
                if "unique_source_session" in str(e):
                    logger.warning(f"[EvolutionStorage] Case already exists for session: {source_session_id}")
                    return None
                logger.error(f"[EvolutionStorage] insert_quality_case failed: {e}")
                raise

    async def get_quality_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        """获取高质量案例。

        Args:
            case_id: 案例ID

        Returns:
            案例数据字典
        """
        if not is_valid_uuid(case_id):
            logger.warning(f"[EvolutionStorage] Invalid case_id: {case_id}")
            return None

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    """
                    SELECT id, source_session_id, quality_score, human_score, revision_count,
                           original_draft, final_draft, key_changes, tuning_history,
                           target_platform, vector_id, extracted_at
                    FROM quality_cases
                    WHERE id = $1
                    """,
                    UUID(case_id),
                )
            except asyncpg.PostgresError as e:
                logger.error(f"[EvolutionStorage] get_quality_case failed: {e}")
                return None

        if not row:
            return None
        return self._row_to_dict(row)

    async def update_case_vector_id(self, case_id: str, vector_id: str) -> bool:
        """更新案例的向量ID（Milvus同步后）。

        Args:
            case_id: 案例ID
            vector_id: Milvus向量ID

        Returns:
            True 如果成功更新
        """
        if not is_valid_uuid(case_id):
            return False

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                await conn.execute(
                    """
                    UPDATE quality_cases SET vector_id = $2
                    WHERE id = $1
                    """,
                    UUID(case_id),
                    vector_id,
                )
                return True
            except asyncpg.PostgresError as e:
                logger.error(f"[EvolutionStorage] update_case_vector_id failed: {e}")
                return False

    async def get_quality_cases_for_rag(
        self,
        platform: str = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """获取高质量案例用于 RAG 检索（按评分排序）。

        Args:
            platform: 目标平台过滤（可选）
            limit: 最大返回数量

        Returns:
            案例列表
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                if platform:
                    rows = await conn.fetch(
                        """
                        SELECT id, quality_score, human_score,
                               original_draft, final_draft, key_changes,
                               target_platform, vector_id
                        FROM quality_cases
                        WHERE target_platform = $1 AND vector_id IS NOT NULL
                        ORDER BY quality_score DESC
                        LIMIT $2
                        """,
                        platform,
                        limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT id, quality_score, human_score,
                               original_draft, final_draft, key_changes,
                               target_platform, vector_id
                        FROM quality_cases
                        WHERE vector_id IS NOT NULL
                        ORDER BY quality_score DESC
                        LIMIT $1
                        """,
                        limit,
                    )
            except asyncpg.PostgresError as e:
                logger.error(f"[EvolutionStorage] get_quality_cases_for_rag failed: {e}")
                return []

        return [self._row_to_dict(row) for row in rows]

    # ============================================================================
    # Evolution Sessions
    # ============================================================================

    async def create_evolution_session(
        self,
        trigger_type: str,
        trigger_threshold: int = None,
        analyzed_session_ids: List[str] = None,
    ) -> str:
        """创建自进化分析会话。

        Args:
            trigger_type: 触发类型（threshold/scheduled）
            trigger_threshold: 阈值触发时的样本数量
            analyzed_session_ids: 分析的 session ID列表

        Returns:
            新创建的 evolution_session ID
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                # 转换 UUID 列表
                uuid_list = [UUID(sid) for sid in analyzed_session_ids if is_valid_uuid(sid)]

                row = await conn.fetchrow(
                    """
                    INSERT INTO evolution_sessions (
                        trigger_type, trigger_threshold, analyzed_session_ids
                    ) VALUES ($1, $2, $3)
                    RETURNING id
                    """,
                    trigger_type,
                    trigger_threshold,
                    uuid_list,
                )

                session_id = str(row["id"])
                logger.info(f"[EvolutionStorage] Evolution session created: {session_id}")
                return session_id

            except asyncpg.PostgresError as e:
                logger.error(f"[EvolutionStorage] create_evolution_session failed: {e}")
                raise

    async def update_evolution_session(
        self,
        session_id: str,
        analysis_result: Dict = None,
        suggested_changes: Dict = None,
        status: str = None,
        applied_template_id: str = None,
    ) -> bool:
        """更新自进化分析会话。

        Args:
            session_id: evolution_session ID
            analysis_result: 分析结果
            suggested_changes: 建议修改
            status: 状态
            applied_template_id: 应用的模板ID

        Returns:
            True 如果成功更新
        """
        if not is_valid_uuid(session_id):
            return False

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                updates = []
                values = [UUID(session_id)]
                idx = 2

                if analysis_result:
                    updates.append(f"analysis_result = ${idx}, analyzed_at = NOW()")
                    values.append(json.dumps(analysis_result))
                    idx += 1

                if suggested_changes:
                    updates.append(f"suggested_changes = ${idx}")
                    values.append(json.dumps(suggested_changes))
                    idx += 1

                if status:
                    updates.append(f"status = ${idx}")
                    values.append(status)
                    idx += 1
                    if status == "applied":
                        updates.append("applied_at = NOW()")

                if applied_template_id and is_valid_uuid(applied_template_id):
                    updates.append(f"applied_template_id = ${idx}")
                    values.append(UUID(applied_template_id))
                    idx += 1

                if not updates:
                    return False

                sql = f"UPDATE evolution_sessions SET {', '.join(updates)} WHERE id = $1"
                await conn.execute(sql, *values)
                return True

            except asyncpg.PostgresError as e:
                logger.error(f"[EvolutionStorage] update_evolution_session failed: {e}")
                return False

    async def get_sessions_for_analysis(
        self,
        template_key: str = None,
        min_samples: int = 5,
        limit: int = 20,
    ) -> List[str]:
        """获取待分析的 session 列表。

        选择标准：
        - 已定稿（stage='completed')
        - 有 evaluation 结果
        - 未被之前的 evolution_session 分析过

        Args:
            template_key: 模板标识（可选，用于筛选使用特定模板的session）
            min_samples: 最少样本数
            limit: 最大返回数量

        Returns:
            session ID列表
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                # 查询已定稿且有评估结果的session
                # 且未被之前的evolution_session分析过
                rows = await conn.fetch(
                    """
                    SELECT s.id
                    FROM sessions s
                    INNER JOIN evaluation_results e ON s.id = e.session_id
                    WHERE s.stage = 'completed'
                      AND e.status = 'completed'
                      AND NOT EXISTS (
                          SELECT 1 FROM evolution_sessions es
                          WHERE es.analyzed_session_ids::uuid[] @> ARRAY[s.id]
                      )
                    ORDER BY s.finalized_at DESC
                    LIMIT $1
                    """,
                    limit,
                )

                session_ids = [str(row["id"]) for row in rows]
                logger.info(f"[EvolutionStorage] Found {len(session_ids)} sessions for analysis")
                return session_ids

            except asyncpg.PostgresError as e:
                logger.error(f"[EvolutionStorage] get_sessions_for_analysis failed: {e}")
                return []

    # ============================================================================
    # Helper Methods
    # ============================================================================

    def _row_to_dict(self, row: asyncpg.Record) -> Dict[str, Any]:
        """转换数据库行到字典。"""
        result = dict(row)

        # 处理UUID
        for key in ["id", "source_session_id", "previous_version_id", "applied_template_id"]:
            if key in result and isinstance(result[key], UUID):
                result[key] = str(result[key])

        # 处理datetime
        for key in ["created_at", "activated_at", "extracted_at", "analyzed_at", "applied_at"]:
            if key in result and result[key] is not None:
                if isinstance(result[key], datetime):
                    result[key] = result[key].isoformat()

        # 处理JSONB字段
        json_fields = ["tuning_history", "analysis_result", "suggested_changes"]
        for key in json_fields:
            if key in result and isinstance(result[key], str):
                try:
                    result[key] = json.loads(result[key])
                except json.JSONDecodeError:
                    pass

        return result


# 全局实例
_evolution_storage: Optional[EvolutionStorage] = None


def get_evolution_storage() -> EvolutionStorage:
    """获取存储实例。"""
    global _evolution_storage
    if _evolution_storage is None:
        _evolution_storage = EvolutionStorage()
    return _evolution_storage