"""Query all documents in the knowledge base."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forge.knowledge import get_knowledge_base
from pymilvus import Collection

kb = get_knowledge_base()

print("=" * 60)
print("知识库文档列表")
print("=" * 60)
print(f"总数: {kb.count()} 条")
print("=" * 60)

# 直接查询 Milvus 获取所有文档
collection = kb.collection
collection.load()

# 查询所有数据
results = collection.query(
    expr="",
    output_fields=["id", "content", "category", "title"],
    limit=100
)

# 按分类分组显示
categories = {}
for r in results:
    cat = r.get('category', 'unknown')
    if cat not in categories:
        categories[cat] = []
    categories[cat].append(r)

for cat, docs in categories.items():
    print(f"\n【{cat}】 ({len(docs)} 条)")
    print("-" * 40)
    for doc in docs:
        id = doc.get('id', '')
        title = doc.get('title', '')
        content = doc.get('content', '')
        preview = content[:80] if content else ''
        print(f"  ID: {id}")
        print(f"  标题: {title}")
        print(f"  内容: {preview}...")
        print()