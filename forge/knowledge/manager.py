"""Knowledge base manager using Milvus for vector storage.

Provides semantic search capabilities for Ruibo Group information.
Requires Milvus Docker running.
"""

import os
import logging
import math
import hashlib
import httpx
from typing import List, Optional
from pymilvus import connections, Collection, FieldSchema, CollectionSchema, DataType

# 不再使用本地模型，改用 Qwen API 生成嵌入

from forge.knowledge.config import (
    MILVUS_HOST,
    MILVUS_PORT,
    COLLECTION_NAME,
    EMBEDDING_MODEL,
    VECTOR_DIMENSION,
)
from forge.config import QWEN_API_KEY, QWEN_API_URL

logger = logging.getLogger(__name__)


class EmbeddingGenerationError(RuntimeError):
    """Raised when an embedding cannot be generated or validated."""


class KnowledgeBase:
    """Vector database manager for knowledge retrieval using Milvus."""

    def __init__(self):
        self.collection = None
        self._connected = False
        self._embedding_model = EMBEDDING_MODEL

    def _ensure_connection(self):
        """Ensure Milvus connection is established."""
        if self._connected:
            return

        try:
            # Connect to Milvus server
            connections.connect(
                alias="default",
                host=MILVUS_HOST,
                port=MILVUS_PORT
            )
            logger.info(f"[KnowledgeBase] Connected to Milvus: {MILVUS_HOST}:{MILVUS_PORT}")

            # Create collection if not exists
            if not self._has_collection():
                self._create_collection()
            else:
                self.collection = Collection(COLLECTION_NAME)
                logger.info(f"[KnowledgeBase] Using existing collection: {COLLECTION_NAME}")

            self._connected = True

        except Exception as e:
            logger.error(f"[KnowledgeBase] Failed to connect to Milvus: {e}")
            raise

    def _has_collection(self) -> bool:
        """Check if collection exists."""
        from pymilvus import utility
        return utility.has_collection(COLLECTION_NAME)

    def _create_collection(self):
        """Create collection with schema."""
        # Define schema with varchar id
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=128),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIMENSION),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=4096),
            FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="source_url", dtype=DataType.VARCHAR, max_length=2048),
            FieldSchema(name="article_id", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="chunk_index", dtype=DataType.INT64),
            FieldSchema(name="total_chunks", dtype=DataType.INT64),
            FieldSchema(name="content_hash", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="secondary_category", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="stage_tags", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="topic_tags", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="province", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="city", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="district", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="region_raw", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="published_at", dtype=DataType.VARCHAR, max_length=10),
            FieldSchema(name="registration_start", dtype=DataType.VARCHAR, max_length=10),
            FieldSchema(name="registration_end", dtype=DataType.VARCHAR, max_length=10),
            FieldSchema(name="event_date", dtype=DataType.VARCHAR, max_length=10),
            FieldSchema(name="validity_status", dtype=DataType.VARCHAR, max_length=32),
        ]
        schema = CollectionSchema(fields, description="Ruibo Group knowledge base v2")

        self.collection: Collection = Collection(COLLECTION_NAME, schema)

        # Create index for vector search
        self.collection.create_index(
            field_name="vector",
            index_params={"metric_type": "COSINE", "index_type": "FLAT", "params": {}}
        )
        logger.info(f"[KnowledgeBase] Created collection: {COLLECTION_NAME}")

    async def _encode_async(self, texts: List[str]) -> List[List[float]]:
        """Encode texts to vectors using Qwen API."""
        embeddings = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for index, text in enumerate(texts):
                try:
                    response = await client.post(
                        f"{QWEN_API_URL}/embeddings",
                        headers={
                            "Authorization": f"Bearer {QWEN_API_KEY}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": self._embedding_model,
                            "input": text
                        }
                    )
                    response.raise_for_status()
                    data = response.json()
                    embedding = data["data"][0]["embedding"]
                    embeddings.append(self._validate_embedding(embedding, index))
                    if (index + 1) % 25 == 0 or index + 1 == len(texts):
                        logger.info(
                            "[KnowledgeBase] Embedded %s/%s documents",
                            index + 1,
                            len(texts),
                        )
                except Exception as e:
                    logger.error(
                        "[KnowledgeBase] Embedding failed at input %s: %s",
                        index,
                        e,
                    )
                    raise EmbeddingGenerationError(
                        f"Embedding generation failed at input {index}"
                    ) from e
        return embeddings

    @staticmethod
    def _validate_embedding(embedding: list, index: int = 0) -> List[float]:
        """Validate an embedding before it can be persisted."""
        if len(embedding) != VECTOR_DIMENSION:
            raise ValueError(
                f"Embedding {index} has dimension {len(embedding)}, "
                f"expected {VECTOR_DIMENSION}"
            )

        vector = [float(value) for value in embedding]
        if not all(math.isfinite(value) for value in vector):
            raise ValueError(f"Embedding {index} contains non-finite values")
        if not any(value != 0.0 for value in vector):
            raise ValueError(f"Embedding {index} is an all-zero vector")
        return vector

    def _encode(self, texts: List[str]) -> List[List[float]]:
        """Encode texts to vectors from sync code."""
        import asyncio
        import threading

        try:
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None

            if running_loop and running_loop.is_running():
                result = {}
                error = {}

                def run_in_thread():
                    try:
                        result["value"] = asyncio.run(self._encode_async(texts))
                    except Exception as exc:
                        error["value"] = exc

                thread = threading.Thread(target=run_in_thread)
                thread.start()
                thread.join()

                if error:
                    raise error["value"]
                return result["value"]

            return asyncio.run(self._encode_async(texts))
        except Exception as e:
            logger.error(f"[KnowledgeBase] _encode failed: {e}")
            raise

    def add_document(self, doc_id: str, content: str, metadata: dict = None):
        """Add a document to the knowledge base.

        Args:
            doc_id: Unique document identifier.
            content: Document content text.
            metadata: Optional metadata (category, title, etc.).
        """
        self.add_documents([{
            "id": doc_id,
            "content": content,
            "metadata": metadata or {},
        }])

    def add_documents(self, documents: List[dict]):
        """Add multiple documents to the knowledge base.

        Args:
            documents: List of dicts with 'id', 'content', 'metadata' keys.
        """
        self._ensure_connection()

        ids = []
        vectors = []
        contents = []
        categories = []
        titles = []
        sources = []
        source_urls = []
        article_ids = []
        chunk_indexes = []
        total_chunks = []
        content_hashes = []
        secondary_categories = []
        stage_tags = []
        topic_tags = []
        provinces = []
        cities = []
        districts = []
        region_raw_values = []
        published_dates = []
        registration_starts = []
        registration_ends = []
        event_dates = []
        validity_statuses = []

        # Collect all content for batch encoding
        content_list = [doc['content'] for doc in documents]
        embeddings = self._encode(content_list)

        for i, doc in enumerate(documents):
            metadata = doc.get('metadata', {})
            ids.append(doc['id'])
            vectors.append(embeddings[i])
            contents.append(doc['content'])
            categories.append(metadata.get('category', 'general'))
            titles.append(metadata.get('title', ''))
            sources.append(metadata.get('source', ''))
            source_urls.append(metadata.get('source_url', ''))
            article_ids.append(metadata.get('article_id', ''))
            chunk_indexes.append(int(metadata.get('chunk_index', 0)))
            total_chunks.append(int(metadata.get('total_chunks', 1)))
            content_hashes.append(
                hashlib.sha256(doc['content'].encode('utf-8')).hexdigest()
            )
            secondary_categories.append(metadata.get('secondary_category', ''))
            stage_tags.append(metadata.get('stage_tags', ''))
            topic_tags.append(metadata.get('topic_tags', ''))
            provinces.append(metadata.get('province', ''))
            cities.append(metadata.get('city', ''))
            districts.append(metadata.get('district', ''))
            region_raw_values.append(metadata.get('region_raw', ''))
            published_dates.append(metadata.get('published_at', ''))
            registration_starts.append(metadata.get('registration_start', ''))
            registration_ends.append(metadata.get('registration_end', ''))
            event_dates.append(metadata.get('event_date', ''))
            validity_statuses.append(metadata.get('validity_status', 'unknown'))

        # Batch insert
        data = [
            ids,
            vectors,
            contents,
            categories,
            titles,
            sources,
            source_urls,
            article_ids,
            chunk_indexes,
            total_chunks,
            content_hashes,
            secondary_categories,
            stage_tags,
            topic_tags,
            provinces,
            cities,
            districts,
            region_raw_values,
            published_dates,
            registration_starts,
            registration_ends,
            event_dates,
            validity_statuses,
        ]
        self.collection.insert(data)
        self.collection.flush()
        logger.info(f"[KnowledgeBase] Added {len(documents)} documents")

    def search(
        self,
        query: str,
        n_results: int = 3,
        category: str = None,
        secondary_category: str = None,
        topic: str = None,
        province: str = None,
        validity_status: str = None,
    ) -> List[dict]:
        """Search for relevant documents.

        Args:
            query: Search query text.
            n_results: Maximum number of results to return.
            category: Optional primary category filter.
            secondary_category: Optional recruitment-stage filter.
            topic: Optional topic tag filter.
            province: Optional province filter.
            validity_status: Optional validity filter.

        Returns:
            List of matching documents with content and metadata.
        """
        self._ensure_connection()

        # Load collection for search
        self.collection.load()

        # Generate query embedding
        query_embedding = self._encode([query])[0]

        # Build filter expression
        conditions = []
        if category:
            conditions.append(f'category == "{self._escape_filter_value(category)}"')
        if secondary_category:
            value = self._escape_filter_value(secondary_category)
            conditions.append(f'secondary_category == "{value}"')
        if topic:
            value = self._escape_filter_value(topic)
            conditions.append(f'topic_tags like "%{value}%"')
        if province:
            value = self._escape_filter_value(province)
            conditions.append(f'province == "{value}"')
        if validity_status:
            value = self._escape_filter_value(validity_status)
            conditions.append(f'validity_status == "{value}"')
        filter_expr = " and ".join(conditions) or None

        # Search
        search_params = {"metric_type": "COSINE", "params": {}}
        results = self.collection.search(
            data=[query_embedding],
            anns_field="vector",
            param=search_params,
            limit=n_results,
            expr=filter_expr,
            output_fields=[
                "content",
                "category",
                "title",
                "source",
                "source_url",
                "article_id",
                "chunk_index",
                "total_chunks",
                "content_hash",
                "secondary_category",
                "stage_tags",
                "topic_tags",
                "province",
                "city",
                "district",
                "region_raw",
                "published_at",
                "registration_start",
                "registration_end",
                "event_date",
                "validity_status",
            ]
        )

        # Format results
        documents = []
        if results and len(results) > 0:
            for hit in results[0]:
                documents.append({
                    'content': hit.entity.get('content', ''),
                    'metadata': {
                        'category': hit.entity.get('category', ''),
                        'title': hit.entity.get('title', ''),
                        'source': hit.entity.get('source', ''),
                        'source_url': hit.entity.get('source_url', ''),
                        'article_id': hit.entity.get('article_id', ''),
                        'chunk_index': hit.entity.get('chunk_index', 0),
                        'total_chunks': hit.entity.get('total_chunks', 1),
                        'content_hash': hit.entity.get('content_hash', ''),
                        'secondary_category': hit.entity.get('secondary_category', ''),
                        'stage_tags': hit.entity.get('stage_tags', ''),
                        'topic_tags': hit.entity.get('topic_tags', ''),
                        'province': hit.entity.get('province', ''),
                        'city': hit.entity.get('city', ''),
                        'district': hit.entity.get('district', ''),
                        'region_raw': hit.entity.get('region_raw', ''),
                        'published_at': hit.entity.get('published_at', ''),
                        'registration_start': hit.entity.get('registration_start', ''),
                        'registration_end': hit.entity.get('registration_end', ''),
                        'event_date': hit.entity.get('event_date', ''),
                        'validity_status': hit.entity.get('validity_status', ''),
                    },
                    'distance': hit.distance,
                })

        logger.info(f"[KnowledgeBase] Search '{query[:30]}...' found {len(documents)} results")
        return documents

    @staticmethod
    def _escape_filter_value(value: str) -> str:
        """Escape a scalar value used in a Milvus filter expression."""
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def has_article(self, article_id: str) -> bool:
        """Return whether any chunk for an article already exists."""
        self._ensure_connection()
        self.collection.load()
        escaped_id = article_id.replace("\\", "\\\\").replace('"', '\\"')
        rows = self.collection.query(
            expr=f'article_id == "{escaped_id}"',
            output_fields=["id"],
            limit=1,
        )
        return bool(rows)

    def get_context_for_topic(self, topic: str, max_docs: int = 5) -> str:
        """Get relevant context for a topic.

        Args:
            topic: Topic to search for.
            max_docs: Maximum documents to include.

        Returns:
            Formatted context string for LLM prompt.
        """
        results = self.search(topic, n_results=max_docs)

        if not results:
            return ""

        # Format as context
        context_parts = ["【锐博集团相关知识】\n"]
        for i, doc in enumerate(results, 1):
            metadata = doc.get('metadata', {})
            category = metadata.get('category', '通用')
            title = metadata.get('title', '未知')
            source = metadata.get('source', '知识库')
            source_url = metadata.get('source_url', '')
            context_parts.append(f"{i}. [{category}] {title}")
            context_parts.append(f"   来源: {source}")
            context_parts.append(f"   URL: {source_url or 'milvus://ruibo_knowledge_v2'}")
            context_parts.append(f"   {doc['content'][:200]}...")
            context_parts.append("")

        return "\n".join(context_parts)

    def count(self) -> int:
        """Get total number of documents."""
        self._ensure_connection()
        # num_entities counts historical inserts and can temporarily include
        # tombstoned rows after upsert. Aggregate count reflects live entities.
        result = self.collection.query(expr="", output_fields=["count(*)"])
        return int(result[0]["count(*)"])

    def clear(self):
        """Clear all documents from the collection."""
        self._ensure_connection()

        from pymilvus import utility

        # Drop and recreate collection
        if utility.has_collection(COLLECTION_NAME):
            utility.drop_collection(COLLECTION_NAME)
            logger.info("[KnowledgeBase] Dropped existing collection")

        self._create_collection()
        logger.info("[KnowledgeBase] Collection cleared and recreated")


# Global instance
_kb_instance = None

def get_knowledge_base() -> KnowledgeBase:
    """Get singleton knowledge base instance."""
    global _kb_instance
    if _kb_instance is None:
        _kb_instance = KnowledgeBase()
    return _kb_instance
