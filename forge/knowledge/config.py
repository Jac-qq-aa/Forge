"""Knowledge base configuration for Milvus vector database."""

import os

# Milvus connection. Containers override the host with ``standalone`` while
# local scripts keep using the published localhost port.
MILVUS_HOST = os.environ.get("MILVUS_HOST", "127.0.0.1")
MILVUS_PORT = os.environ.get("MILVUS_PORT", "19530")

# Collection name
COLLECTION_NAME = os.environ.get("MILVUS_COLLECTION_NAME", "ruibo_knowledge_v2")

# Embedding configuration. The dimension must match the Milvus collection.
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-v3")
VECTOR_DIMENSION = int(os.environ.get("EMBEDDING_VECTOR_DIMENSION", "1024"))

# Knowledge categories
CATEGORIES = [
    "company_intro",      # 公司介绍
    "recruitment",        # 招聘信息
    "culture",            # 企业文化
    "success_cases",      # 成功案例
    "industry_insights",  # 行业洞察/观点文章
    "employee_stories",   # 员工故事
    "policy_updates",     # 政策解读
    "training",           # 培训发展
    "news",               # 公司新闻动态
    "faq",                # 常见问题解答
]
