"""Delete old test data from knowledge base."""

import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forge.knowledge import get_knowledge_base

# 要删除的旧数据ID（init_knowledge_base.py中的测试数据）
OLD_DATA_IDS = [
    "company_intro_001",
    "company_intro_002",
    "culture_001",
    "culture_002",
    "culture_003",
    "recruitment_001",
    "recruitment_002",
    "recruitment_003",
    "success_cases_001",
    "success_cases_002",
    "success_cases_003",
]

print("=" * 60)
print("删除旧版测试数据")
print("=" * 60)

kb = get_knowledge_base()
print(f"删除前文档数: {kb.count()}")

# Milvus 按ID删除 - 使用正确的表达式格式
collection = kb.collection

# 构建表达式: id in ["xxx", "yyy"]
expr = 'id in ["' + '", "'.join(OLD_DATA_IDS) + '"]'
print(f"删除表达式: {expr}")

result = collection.delete(expr=expr)
print(f"删除结果: {result}")

collection.flush()
time.sleep(2)

print(f"删除后文档数: {kb.count()}")
print("✅ 删除完成")

# 显示剩余文档分类统计
print("\n剩余文档分类统计:")
results = collection.query(
    expr="",
    output_fields=["id", "category"],
    limit=100
)

categories = {}
for r in results:
    cat = r.get('category', 'unknown')
    categories[cat] = categories.get(cat, 0) + 1

for cat, count in sorted(categories.items()):
    print(f"  {cat}: {count} 条")

# 显示剩余文档ID
print("\n剩余文档ID列表:")
for r in results:
    print(f"  {r.get('id')}")