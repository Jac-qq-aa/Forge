"""Knowledge base configuration for Milvus vector database."""

import os
from pathlib import Path

# Milvus server connection (Docker)
MILVUS_HOST = "127.0.0.1"
MILVUS_PORT = "19530"

# Collection name
COLLECTION_NAME = "ruibo_knowledge"

# Vector dimensions (all-MiniLM-L6-v2 produces 384-dimensional vectors)
VECTOR_DIMENSION = 384

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