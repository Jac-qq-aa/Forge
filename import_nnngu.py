"""导入锐博官网内容到知识库.

抓取官网文章，智能切分，导入到 Milvus 向量数据库。
"""

import sys
import os
import logging
import json
import argparse
from collections import Counter
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
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
from forge.knowledge.nnngu_classifier import classify_nnngu_article
from forge.knowledge.text_splitter import ChineseTextSplitter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


def _strip_title_prefix(content: str, title: str) -> str:
    prefix = f"[{title}] "
    return content[len(prefix):] if content.startswith(prefix) else content


def _reconstruct_chunks(chunks: list[dict], title: str, overlap: int) -> str:
    bodies = [_strip_title_prefix(chunk["content"], title) for chunk in chunks]
    if not bodies:
        return ""

    reconstructed = bodies[0]
    for body in bodies[1:]:
        max_overlap = min(overlap, len(reconstructed), len(body))
        shared = next(
            (
                size
                for size in range(max_overlap, -1, -1)
                if reconstructed.endswith(body[:size])
            ),
            0,
        )
        reconstructed += body[shared:]
    return reconstructed


def build_documents(articles: list) -> tuple[list[dict], dict]:
    """Split articles and return import documents plus an audit report."""
    splitter = ChineseTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
        min_chunk_size=100,
        preserve_context=True,
    )
    documents = []
    article_audits = []
    classification_reference_date = date.today()

    for article in articles:
        article_id = str(article["id"])
        title = article["title"]
        content = article["content"]
        category = article.get("category", "company_intro")
        classification = classify_nnngu_article(
            article,
            reference_date=classification_reference_date,
        )
        normalized = splitter._clean_text(content)
        chunks = splitter.split_text(content, {"title": title})
        reconstructed = _reconstruct_chunks(chunks, title, splitter.chunk_overlap)

        article_audits.append({
            "article_id": article_id,
            "title": title,
            "source_url": article.get("url", ""),
            "source_characters": len(normalized),
            "chunk_count": len(chunks),
            "chunk_lengths": [
                len(_strip_title_prefix(chunk["content"], title))
                for chunk in chunks
            ],
            "lossless": reconstructed == normalized,
            "coverage_ratio": round(
                SequenceMatcher(None, normalized, reconstructed).ratio(),
                6,
            ),
            "classification": classification,
        })

        for index, chunk in enumerate(chunks):
            documents.append({
                "id": f"nnngu_{article_id}_chunk_{index:03d}",
                "content": chunk["content"],
                "metadata": {
                    "category": category,
                    "title": title,
                    "source": "nnngu.com",
                    "source_url": article.get("url", ""),
                    "article_id": article_id,
                    "chunk_index": index,
                    "total_chunks": len(chunks),
                    **classification,
                },
            })

    chunk_lengths = [len(document["content"]) for document in documents]
    document_ids = [document["id"] for document in documents]
    secondary_categories = Counter(
        item["classification"]["secondary_category"] or "not_applicable"
        for item in article_audits
    )
    topic_tags = Counter(
        tag
        for item in article_audits
        for tag in item["classification"]["topic_tags"].split(",")
        if tag
    )
    validity_statuses = Counter(
        item["classification"]["validity_status"] for item in article_audits
    )
    provinces = Counter(
        item["classification"]["province"] or "unknown"
        for item in article_audits
    )
    audit = {
        "article_count": len(articles),
        "chunk_count": len(documents),
        "lossless_article_count": sum(item["lossless"] for item in article_audits),
        "minimum_coverage_ratio": min(
            (item["coverage_ratio"] for item in article_audits),
            default=0,
        ),
        "duplicate_document_ids": len(document_ids) - len(set(document_ids)),
        "classification_reference_date": classification_reference_date.isoformat(),
        "classification": {
            "secondary_categories": dict(secondary_categories),
            "topic_tags": dict(topic_tags),
            "validity_statuses": dict(validity_statuses),
            "provinces": dict(provinces),
        },
        "chunk_length": {
            "minimum": min(chunk_lengths, default=0),
            "maximum": max(chunk_lengths, default=0),
            "average": round(sum(chunk_lengths) / len(chunk_lengths), 2)
            if chunk_lengths else 0,
        },
        "articles": article_audits,
    }
    return documents, audit


def save_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def import_to_knowledge_base(documents: list[dict]):
    """导入文章到知识库.

    Args:
        documents: 已审计的知识片段
    """
    print("=" * 60)
    print("导入锐博官网内容到知识库")
    print("=" * 60)
    print(f"片段数: {len(documents)}")
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

    print("\n[步骤2] 批量导入...")
    kb.add_documents(documents)

    # 验证
    print("\n[步骤3] 验证导入结果...")
    new_count = kb.count()
    print(f"  导入后文档数: {new_count} (新增 {new_count - current_count})")

    print("\n[步骤4] 测试搜索...")
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


def parse_args():
    parser = argparse.ArgumentParser(description="抓取并导入锐博官网知识库")
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="使用已保存的 raw-*.json 快照，不重新抓取官网",
    )
    return parser.parse_args()


def main():
    """主函数."""
    args = parse_args()

    if args.snapshot:
        payload = json.loads(args.snapshot.read_text(encoding="utf-8"))
        articles = payload["articles"]
        print(f"从快照读取 {len(articles)} 篇文章: {args.snapshot}")
    else:
        print("开始抓取锐博官网内容...")
        articles = scrape_nnngu()
        print(f"抓取到 {len(articles)} 篇文章")

    if not articles:
        print("没有抓取到内容，退出")
        return

    output_dir = Path("data") / "knowledge" / "nnngu"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    audit_path = output_dir / f"audit-{timestamp}.json"

    if not args.snapshot:
        snapshot_path = output_dir / f"raw-{timestamp}.json"
        save_json(snapshot_path, {
            "scraped_at": timestamp,
            "source": "https://www.nnngu.com",
            "articles": articles,
        })
        print(f"原始快照: {snapshot_path}")

    documents, audit = build_documents(articles)
    save_json(audit_path, audit)
    print(f"切片审计: {audit_path}")
    print(
        f"审计结果: {audit['lossless_article_count']}/{audit['article_count']} "
        f"篇无损，最低覆盖率 {audit['minimum_coverage_ratio']:.2%}，"
        f"重复ID {audit['duplicate_document_ids']}"
    )

    if (
        audit["lossless_article_count"] != audit["article_count"]
        or audit["duplicate_document_ids"] != 0
    ):
        raise RuntimeError("切片审计未通过，已停止导入")

    import_to_knowledge_base(documents)


if __name__ == "__main__":
    main()
