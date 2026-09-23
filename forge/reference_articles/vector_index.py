"""参考文章的双向量 Milvus 索引：主题向量与风格向量分离。"""

from __future__ import annotations

import math
import os
import uuid
from typing import Any

import httpx

from forge.config import QWEN_API_KEY, QWEN_API_URL

COLLECTION_NAME = "reference_articles_v1"
VECTOR_DIMENSION = int(os.environ.get("EMBEDDING_VECTOR_DIMENSION", "1024"))
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-v3")
MILVUS_HOST = os.environ.get("MILVUS_HOST", "127.0.0.1")
MILVUS_PORT = os.environ.get("MILVUS_PORT", "19530")


class ReferenceVectorIndex:
    def __init__(self) -> None:
        self.collection = None
        self._connected = False

    def _ensure_connection(self) -> None:
        if self._connected:
            return
        from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

        connections.connect(alias=COLLECTION_NAME, host=MILVUS_HOST, port=MILVUS_PORT)
        if utility.has_collection(COLLECTION_NAME, using=COLLECTION_NAME):
            self.collection = Collection(COLLECTION_NAME, using=COLLECTION_NAME)
        else:
            fields = [
                FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
                FieldSchema(name="topic_vector", dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIMENSION),
                FieldSchema(name="style_vector", dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIMENSION),
                FieldSchema(name="row_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="platform", dtype=DataType.VARCHAR, max_length=32),
                FieldSchema(name="composition_type", dtype=DataType.VARCHAR, max_length=16),
                FieldSchema(name="archetype", dtype=DataType.VARCHAR, max_length=32),
                FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
            ]
            self.collection = Collection(
                COLLECTION_NAME,
                CollectionSchema(fields, description="Topic and style vectors for reference articles"),
                using=COLLECTION_NAME,
            )
            for field in ("topic_vector", "style_vector"):
                self.collection.create_index(
                    field_name=field,
                    index_params={"metric_type": "COSINE", "index_type": "FLAT", "params": {}},
                )
        self._connected = True

    async def _embed(self, text: str) -> list[float]:
        if not QWEN_API_KEY:
            raise RuntimeError("QWEN_API_KEY is required for reference article embeddings")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{QWEN_API_URL}/embeddings",
                headers={"Authorization": f"Bearer {QWEN_API_KEY}"},
                json={"model": EMBEDDING_MODEL, "input": text},
            )
            response.raise_for_status()
            vector = [float(value) for value in response.json()["data"][0]["embedding"]]
        if len(vector) != VECTOR_DIMENSION or not all(math.isfinite(value) for value in vector):
            raise ValueError("invalid reference article embedding")
        return vector

    async def index(self, row: dict[str, Any]) -> str:
        self._ensure_connection()
        if self.collection is None:
            raise RuntimeError("Milvus reference collection was not initialized")
        vector_id = str(uuid.uuid4())
        topic_vector, style_vector = await self._embed(str(row["topic_text"])), await self._embed(str(row["style_text"]))
        self.collection.insert([
            [vector_id], [topic_vector], [style_vector], [str(row["id"])],
            [str(row["platform"])], [str(row["composition_type"])],
            [str(row["archetype"])], [str(row["title"])[:512]],
        ])
        self.collection.flush()
        return vector_id

    async def search(
        self,
        *,
        topic_query: str,
        style_query: str,
        platform: str,
        archetype: str = "",
        limit: int = 3,
    ) -> list[tuple[str, float]]:
        self._ensure_connection()
        if self.collection is None:
            raise RuntimeError("Milvus reference collection was not initialized")
        self.collection.load()
        expression = f'platform == "{platform.replace(chr(34), "")}"'
        if archetype:
            expression += f' and archetype == "{archetype.replace(chr(34), "")}"'
        topic_vector, style_vector = await self._embed(topic_query), await self._embed(style_query)
        scores: dict[str, float] = {}
        for field, vector, weight in (("topic_vector", topic_vector, 0.7), ("style_vector", style_vector, 0.3)):
            result = self.collection.search(
                data=[vector], anns_field=field,
                param={"metric_type": "COSINE", "params": {}},
                limit=max(limit * 3, 10), expr=expression, output_fields=["row_id"],
            )
            for hit in (result[0] if result else []):
                row_id = str(hit.entity.get("row_id"))
                scores[row_id] = scores.get(row_id, 0.0) + weight * float(hit.score)
        return sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]
