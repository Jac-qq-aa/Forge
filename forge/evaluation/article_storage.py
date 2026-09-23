"""最终文章表达质量评估的 PostgreSQL 存储接口。"""

import json
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from forge.storage.pg_client import get_pg_pool, is_valid_uuid


def _require_uuid(session_id: str) -> UUID:
    if not is_valid_uuid(session_id):
        raise ValueError(f"无效的 session_id: {session_id}")
    return UUID(session_id)


async def get_final_article(session_id: str) -> Optional[str]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT final_draft FROM sessions WHERE id = $1",
            _require_uuid(session_id),
        )
    return str(value) if value else None


async def get_completed_article_expression(
    session_id: str,
    digest: str,
) -> Optional[Dict[str, Any]]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM article_expression_evaluations
            WHERE session_id = $1 AND article_hash = $2 AND status = 'completed'
            """,
            _require_uuid(session_id),
            digest,
        )
    return _row_to_dict(row) if row else None


async def save_article_expression(
    session_id: str,
    digest: str,
    result: Dict[str, Any],
) -> None:
    status = str(result.get("status") or "completed")
    evaluated_at = datetime.now() if status in {"completed", "failed"} else None
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO article_expression_evaluations (
                session_id, article_hash, overall_score, structure_score,
                writing_score, naturalness_score, ai_style_risk,
                metric_reasons, issues, model, status, error_message, evaluated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            ON CONFLICT (session_id, article_hash) DO UPDATE SET
                overall_score = EXCLUDED.overall_score,
                structure_score = EXCLUDED.structure_score,
                writing_score = EXCLUDED.writing_score,
                naturalness_score = EXCLUDED.naturalness_score,
                ai_style_risk = EXCLUDED.ai_style_risk,
                metric_reasons = EXCLUDED.metric_reasons,
                issues = EXCLUDED.issues,
                model = EXCLUDED.model,
                status = EXCLUDED.status,
                error_message = EXCLUDED.error_message,
                evaluated_at = EXCLUDED.evaluated_at
            """,
            _require_uuid(session_id),
            digest,
            result.get("overall_score"),
            result.get("structure_score"),
            result.get("writing_score"),
            result.get("naturalness_score"),
            result.get("ai_style_risk"),
            json.dumps(result.get("metric_reasons") or {}, ensure_ascii=False),
            json.dumps(result.get("issues") or [], ensure_ascii=False),
            result.get("model"),
            status,
            result.get("error_message"),
            evaluated_at,
        )


async def get_latest_article_expression(session_id: str) -> Optional[Dict[str, Any]]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM article_expression_evaluations
            WHERE session_id = $1
            ORDER BY evaluated_at DESC NULLS LAST, created_at DESC
            LIMIT 1
            """,
            _require_uuid(session_id),
        )
    return _row_to_dict(row) if row else None


def _row_to_dict(row: Any) -> Dict[str, Any]:
    result = dict(row)
    for key in ("id", "session_id"):
        if isinstance(result.get(key), UUID):
            result[key] = str(result[key])
    for key in ("created_at", "evaluated_at"):
        if isinstance(result.get(key), datetime):
            result[key] = result[key].isoformat()
    for key in ("metric_reasons", "issues"):
        if isinstance(result.get(key), str):
            result[key] = json.loads(result[key])
    return result
