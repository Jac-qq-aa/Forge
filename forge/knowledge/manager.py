"""Knowledge base manager using Milvus for vector storage.

Provides semantic search capabilities for Ruibo Group information.
Requires Milvus Docker running.
"""

import os
import logging
from typing import List, Optional
from pymilvus import connections, Collection, FieldSchema, CollectionSchema, DataType
from sentence_transformers import SentenceTransformer

# 强制使用本地缓存，避免联网检查模型更新
os.environ["HF_HUB_OFFLINE"] = "1"

from forge.knowledge.config import (
    MILVUS_HOST,
    MILVUS_PORT,
    COLLECTION_NAME,
    VECTOR_DIMENSION,
)

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """Vector database manager for knowledge retrieval using Milvus."""

    def __init__(self):
        self.collection = None
        self.encoder = None
        self._connected = False

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

            # Initialize encoder (use local cache only)
            self.encoder = SentenceTransformer('all-MiniLM-L6-v2', local_files_only=True)
            logger.info("[KnowledgeBase] Encoder loaded: all-MiniLM-L6-v2 (local)")

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
            FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIMENSION),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=2000),
            FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=256),
        ]
        schema = CollectionSchema(fields, description="Ruibo Group knowledge base")

        self.collection: Collection = Collection(COLLECTION_NAME, schema)

        # Create index for vector search
        self.collection.create_index(
            field_name="vector",
            index_params={"metric_type": "COSINE", "index_type": "IVF_FLAT", "params": {"nlist": 128}}
        )
        logger.info(f"[KnowledgeBase] Created collection: {COLLECTION_NAME}")

    def _encode(self, texts: List[str]) -> List[List[float]]:
        """Encode texts to vectors."""
        if self.encoder is None:
            self.encoder = SentenceTransformer('all-MiniLM-L6-v2', local_files_only=True)
        return self.encoder.encode(texts, convert_to_numpy=True).tolist()

    def add_document(self, doc_id: str, content: str, metadata: dict = None):
        """Add a document to the knowledge base.

        Args:
            doc_id: Unique document identifier.
            content: Document content text.
            metadata: Optional metadata (category, title, etc.).
        """
        self._ensure_connection()

        metadata = metadata or {}
        category = metadata.get("category", "general")
        title = metadata.get("title", "")

        # Generate embedding
        embedding = self._encode([content])[0]

        # Insert into collection
        data = [
            [doc_id],
            [embedding],
            [content],
            [category],
            [title],
        ]

        self.collection.insert(data)
        self.collection.flush()
        logger.info(f"[KnowledgeBase] Added document: {doc_id}")

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

        # Batch insert
        data = [ids, vectors, contents, categories, titles]
        self.collection.insert(data)
        self.collection.flush()
        logger.info(f"[KnowledgeBase] Added {len(documents)} documents")

    def search(self, query: str, n_results: int = 3, category: str = None) -> List[dict]:
        """Search for relevant documents.

        Args:
            query: Search query text.
            n_results: Maximum number of results to return.
            category: Optional category filter.

        Returns:
            List of matching documents with content and metadata.
        """
        self._ensure_connection()

        # Load collection for search
        self.collection.load()

        # Generate query embedding
        query_embedding = self._encode([query])[0]

        # Build filter expression
        filter_expr = None
        if category:
            filter_expr = f'category == "{category}"'

        # Search
        search_params = {"metric_type": "COSINE", "params": {"nprobe": 10}}
        results = self.collection.search(
            data=[query_embedding],
            anns_field="vector",
            param=search_params,
            limit=n_results,
            expr=filter_expr,
            output_fields=["content", "category", "title"]
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
                    },
                    'distance': hit.distance,
                })

        logger.info(f"[KnowledgeBase] Search '{query[:30]}...' found {len(documents)} results")
        return documents

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
            context_parts.append(f"{i}. [{category}] {title}")
            context_parts.append(f"   {doc['content'][:200]}...")
            context_parts.append("")

        return "\n".join(context_parts)

    def count(self) -> int:
        """Get total number of documents."""
        self._ensure_connection()
        return self.collection.num_entities

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