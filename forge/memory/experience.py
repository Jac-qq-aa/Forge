"""按任务条件检索已被用户定稿接受的编辑经验。"""

from __future__ import annotations

import logging
import math
import os
import uuid
from typing import Any, Dict, List, Optional
from uuid import UUID

from forge.config import QWEN_API_KEY, QWEN_API_URL
from forge.storage.pg_client import get_pg_pool, is_valid_uuid

logger = logging.getLogger(__name__)

COLLECTION_NAME = "memory_experience_cases_v1"
VECTOR_DIMENSION = int(os.environ.get("EMBEDDING_VECTOR_DIMENSION", "1024"))
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-v3")
MAX_CONTEXT_CASES = 3
MILVUS_HOST = os.environ.get("MILVUS_HOST", "127.0.0.1")
MILVUS_PORT = os.environ.get("MILVUS_PORT", "19530")


def _scope_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_experience_query(state: Dict[str, Any], stage: str) -> str:
    """将当前任务转换为用于相似经验检索的查询文本。"""
    source = state.get("source_article", {})
    parts = [
        f"阶段：{stage}",
        f"平台：{state.get('target_platform') or 'zhihu_article'}",
        f"内容类型：{source.get('content_type') or 'general'}",
        f"标题：{source.get('title') or ''}",
        f"本次要求：{state.get('user_input') or ''}",
    ]
    if stage == "outline":
        research_result = state.get("research_result", {})
        facts = research_result.get("facts", []) if isinstance(research_result, dict) else []
        approved_fact_titles = [
            str(fact.get("claim") or fact.get("content") or "")[:120]
            for fact in facts
            if isinstance(fact, dict)
            and fact.get("status") != "unsupported"
            and (fact.get("review") or {}).get("decision") == "accepted"
        ]
        parts.append("已确认事实：" + "；".join(approved_fact_titles[:8]))
    else:
        parts.append(f"大纲：{str(state.get('outline') or '')[:1200]}")
    return "\n".join(parts)


class ExperienceMemoryManager:
    """PG 保存完整案例，Milvus 保存向量及精确过滤字段。"""

    def __init__(self) -> None:
        self.collection = None
        self._connected = False

    def _ensure_connection(self) -> None:
        if self._connected:
            return
        from pymilvus import (
            Collection,
            CollectionSchema,
            DataType,
            FieldSchema,
            connections,
            utility,
        )
        connections.connect(
            alias=COLLECTION_NAME,
            host=MILVUS_HOST,
            port=MILVUS_PORT,
        )
        if utility.has_collection(COLLECTION_NAME):
            self.collection = Collection(COLLECTION_NAME)
        else:
            fields = [
                FieldSchema(
                    name="id",
                    dtype=DataType.VARCHAR,
                    is_primary=True,
                    max_length=64,
                ),
                FieldSchema(
                    name="vector",
                    dtype=DataType.FLOAT_VECTOR,
                    dim=VECTOR_DIMENSION,
                ),
                FieldSchema(name="case_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="memory_owner_id", dtype=DataType.VARCHAR, max_length=128),
                FieldSchema(name="stage", dtype=DataType.VARCHAR, max_length=32),
                FieldSchema(name="platform", dtype=DataType.VARCHAR, max_length=32),
                FieldSchema(name="content_type", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="summary", dtype=DataType.VARCHAR, max_length=2048),
            ]
            self.collection = Collection(
                COLLECTION_NAME,
                CollectionSchema(fields, description="Accepted user editing experiences"),
            )
            self.collection.create_index(
                field_name="vector",
                index_params={
                    "metric_type": "COSINE",
                    "index_type": "IVF_FLAT",
                    "params": {"nlist": 128},
                },
            )
        self._connected = True

    async def _encode(self, text: str) -> List[float]:
        if not QWEN_API_KEY:
            raise RuntimeError("QWEN_API_KEY is required for experience embeddings")
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{QWEN_API_URL}/embeddings",
                headers={
                    "Authorization": f"Bearer {QWEN_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"model": EMBEDDING_MODEL, "input": text},
            )
            response.raise_for_status()
            vector = [
                float(value)
                for value in response.json()["data"][0]["embedding"]
            ]
        if len(vector) != VECTOR_DIMENSION:
            raise ValueError(
                f"experience embedding dimension {len(vector)} != {VECTOR_DIMENSION}"
            )
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("experience embedding contains non-finite values")
        return vector

    async def index_case(self, case: Dict[str, Any]) -> Optional[str]:
        """将一条 PG 经验案例写入 Milvus。"""
        try:
            self._ensure_connection()
            if self.collection is None:
                return None
            vector_id = str(uuid.uuid4())
            retrieval_text = str(case["retrieval_text"])
            data = [
                [vector_id],
                [await self._encode(retrieval_text)],
                [str(case["id"])],
                [str(case["memory_owner_id"])],
                [str(case["stage"])],
                [str(case["target_platform"])],
                [str(case["content_type"])],
                [retrieval_text[:2048]],
            ]
            self.collection.insert(data)
            self.collection.flush()
            await self._save_vector_id(str(case["id"]), vector_id)
            return vector_id
        except Exception as exc:
            logger.warning("[ExperienceMemory] Indexing failed: %s", exc)
            return None

    async def _save_vector_id(self, case_id: str, vector_id: str) -> None:
        if not is_valid_uuid(case_id):
            return
        pool = await get_pg_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE memory_experience_cases
                SET vector_id = $2
                WHERE id = $1
                """,
                UUID(case_id),
                vector_id,
            )

    async def search(
        self,
        *,
        query: str,
        memory_owner_id: str,
        stage: str,
        target_platform: str,
        content_type: str,
        limit: int = MAX_CONTEXT_CASES,
    ) -> List[Dict[str, Any]]:
        """先按用户和任务条件过滤，再做向量相似度检索。"""
        try:
            has_indexed = await self._has_indexed_cases(
                memory_owner_id=memory_owner_id,
                stage=stage,
                target_platform=target_platform,
                content_type=content_type,
            )
            if not has_indexed:
                pending_case = await self._load_pending_case(
                    memory_owner_id=memory_owner_id,
                    stage=stage,
                    target_platform=target_platform,
                    content_type=content_type,
                )
                if pending_case:
                    await self.index_case(pending_case)
                has_indexed = await self._has_indexed_cases(
                    memory_owner_id=memory_owner_id,
                    stage=stage,
                    target_platform=target_platform,
                    content_type=content_type,
                )
            if not has_indexed:
                return []
            self._ensure_connection()
            if self.collection is None:
                return []
            self.collection.load()
            expr = (
                f'memory_owner_id == "{_scope_literal(memory_owner_id)}"'
                f' and stage == "{_scope_literal(stage)}"'
                f' and platform == "{_scope_literal(target_platform)}"'
                f' and content_type == "{_scope_literal(content_type)}"'
            )
            results = self.collection.search(
                data=[await self._encode(query)],
                anns_field="vector",
                param={"metric_type": "COSINE", "params": {"nprobe": 10}},
                limit=limit,
                expr=expr,
                output_fields=["case_id"],
            )
            case_ids = [
                hit.entity.get("case_id")
                for hit in (results[0] if results else [])
                if hit.entity.get("case_id")
            ]
            return await self._load_cases(case_ids)
        except Exception as exc:
            logger.warning("[ExperienceMemory] Search failed: %s", exc)
            return []

    async def _load_pending_case(
        self,
        *,
        memory_owner_id: str,
        stage: str,
        target_platform: str,
        content_type: str,
    ) -> Optional[Dict[str, Any]]:
        pool = await get_pg_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, memory_owner_id, stage, target_platform,
                       content_type, retrieval_text
                FROM memory_experience_cases
                WHERE memory_owner_id = $1
                  AND stage = $2
                  AND target_platform = $3
                  AND content_type = $4
                  AND vector_id IS NULL
                ORDER BY success_score DESC, created_at DESC
                LIMIT 1
                """,
                memory_owner_id,
                stage,
                target_platform,
                content_type,
            )
        return dict(row) if row else None

    async def _has_indexed_cases(
        self,
        *,
        memory_owner_id: str,
        stage: str,
        target_platform: str,
        content_type: str,
    ) -> bool:
        pool = await get_pg_pool()
        if pool is None:
            return False
        async with pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM memory_experience_cases
                        WHERE memory_owner_id = $1
                          AND stage = $2
                          AND target_platform = $3
                          AND content_type = $4
                          AND vector_id IS NOT NULL
                    )
                    """,
                    memory_owner_id,
                    stage,
                    target_platform,
                    content_type,
                )
            )

    async def _load_cases(self, case_ids: List[str]) -> List[Dict[str, Any]]:
        uuid_ids = [UUID(case_id) for case_id in case_ids if is_valid_uuid(case_id)]
        if not uuid_ids:
            return []
        pool = await get_pg_pool()
        if pool is None:
            return []
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, instruction, before_excerpt, after_excerpt,
                       diff_summary, success_score, created_at
                FROM memory_experience_cases
                WHERE id = ANY($1::uuid[])
                """,
                uuid_ids,
            )
            row_map = {str(row["id"]): dict(row) for row in rows}
            ordered = [row_map[case_id] for case_id in case_ids if case_id in row_map]
            if ordered:
                await conn.execute(
                    """
                    UPDATE memory_experience_cases
                    SET use_count = use_count + 1, last_used_at = NOW()
                    WHERE id = ANY($1::uuid[])
                    """,
                    uuid_ids,
                )
            return ordered

    async def get_context(
        self,
        *,
        query: str,
        memory_owner_id: str,
        stage: str,
        target_platform: str,
        content_type: str,
    ) -> str:
        cases = await self.search(
            query=query,
            memory_owner_id=memory_owner_id,
            stage=stage,
            target_platform=target_platform,
            content_type=content_type,
        )
        if not cases:
            return ""
        parts = ["以下是同一用户在相似任务中已经接受的修改经验："]
        for index, case in enumerate(cases, 1):
            parts.append(f"{index}. 当时要求：{case['instruction']}")
            parts.append(f"   接受后的写法：{str(case['after_excerpt'])[:500]}")
        parts.append("只借鉴修改方式，不复制案例中的具体事实、数据或专有名词。")
        return "\n".join(parts)


_manager: Optional[ExperienceMemoryManager] = None


def get_experience_memory_manager() -> ExperienceMemoryManager:
    global _manager
    if _manager is None:
        _manager = ExperienceMemoryManager()
    return _manager
