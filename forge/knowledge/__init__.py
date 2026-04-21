"""Knowledge base package for Forge - Milvus backend."""

from forge.knowledge.manager import KnowledgeBase, get_knowledge_base
from forge.knowledge.config import CATEGORIES, MILVUS_HOST, MILVUS_PORT, COLLECTION_NAME

__all__ = ['KnowledgeBase', 'get_knowledge_base', 'CATEGORIES', 'MILVUS_HOST', 'MILVUS_PORT', 'COLLECTION_NAME']