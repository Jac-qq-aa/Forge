"""导入锐博官网内容到知识库.

抓取官网文章，智能切分，导入到 Milvus 向量数据库。
"""

import sys
import os
import re
import logging
from pathlib import Path

# 强制使用本地模型缓存，不联网
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 直接导入爬虫，避免导入整个 forge.tools 模块
import importlib.util
spec = importlib.util.spec_from_file_location(
    "nnngu_scraper",
    os.path.join(os.path.dirname(__file__), "forge", "tools", "nnngu_scraper.py")
)
nnngu_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nnngu_module)
scrape_nnngu = nnngu_module.scrape_nnngu
# 直接导入知识库模块
from forge.knowledge.manager import get_knowledge_base
from forge.knowledge.config import MILVUS_HOST, MILVUS_PORT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


def split_by_sentences(text: str, max_len: int = 500) -> list:
    """按句子切分长文本."""
    parts = re.split(r'([。！？\n]+)', text)
    sentences = []
    current = ""

    for part in parts:
        if re.match(r'[。！？\n]+', part):
            current += part
            if len(current.strip()) > 80:
                sentences.append(current.strip())
            current = ""
        else:
            current += part
            if len(current) > max_len:
                sentences.append(current.strip())
                current = ""

    if current.strip() and len(current.strip()) > 80:
        sentences.append(current.strip())

    return sentences


def smart_split_content(content: str, min_len: int = 100, max_len: int = 500) -> list:
    """智能切分内容.

    策略：
    1. 短文本（<max_len）：保持原样
    2. 长文本（>max_len）：按句子切分
    """
    if len(content) <= max_len:
        return [content]

    return split_by_sentences(content, max_len)


def import_to_knowledge_base(articles: list):
    """导入文章到知识库.

    Args:
        articles: 文章列表
    """
    print("=" * 60)
    print("导入锐博官网内容到知识库")
    print("=" * 60)
    print(f"文章数: {len(articles)}")
    print(f"Milvus: {MILVUS_HOST}:{MILVUS_PORT}")

    # 连接知识库
    print("\n[步骤1] 连接 Milvus...")
    try:
        kb = get_knowledge_base()
        current_count = kb.count()
        print(f"  当前知识库文档数: {current_count}")
    except Exception as e:
        print(f"  ❌ 连接失败: {e}")
        print("\n请确保 Milvus 正在运行:")
        print("  docker-compose up -d")
        return

    # 切分并准备文档
    print("\n[步骤2] 智能切分...")
    documents = []
    chunk_count = 0

    for article in articles:
        article_id = article["id"]
        title = article["title"]
        content = article["content"]
        category = article.get("category", "company_intro")

        # 切分
        chunks = smart_split_content(content)
        for i, chunk in enumerate(chunks):
            doc_id = f"nnngu_{article_id}_{i:02d}"
            documents.append({
                "id": doc_id,
                "content": chunk,
                "metadata": {
                    "category": category,
                    "title": title,
                    "source": "nnngu.com",
                    "article_id": article_id,
                }
            })
            chunk_count += 1

    print(f"  切分后 {chunk_count} 个片段")

    # 批量导入
    print("\n[步骤3] 批量导入...")
    kb.add_documents(documents)

    # 验证
    print("\n[步骤4] 验证导入结果...")
    new_count = kb.count()
    print(f"  导入后文档数: {new_count} (新增 {new_count - current_count})")

    # 测试搜索
    print("\n[步骤5] 测试搜索...")
    test_queries = ["锐博集团", "招聘", "劳务派遣", "人力资源"]
    for query in test_queries:
        results = kb.search(query, n_results=2)
        print(f"\n  搜索 '{query}' 结果:")
        for r in results:
            title = r['metadata'].get('title', '')
            preview = r['content'][:60]
            print(f"    - [{title}] {preview}...")

    print("\n" + "=" * 60)
    print("✅ 导入完成！")
    print("=" * 60)


def main():
    """主函数."""
    print("开始抓取锐博官网内容...")

    # 抓取
    articles = scrape_nnngu()
    print(f"抓取到 {len(articles)} 篇文章")

    if not articles:
        print("没有抓取到内容，退出")
        return

    # 导入
    import_to_knowledge_base(articles)


if __name__ == "__main__":
    main()