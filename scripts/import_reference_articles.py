"""导入知乎文章快照，分析、去重并建立主题/风格双向量索引。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forge.reference_articles.analyzer import (
    analyze_reference_article,
    discover_boilerplate,
    extract_company_fact_candidates,
    split_sentences,
    strip_boilerplate,
)
from forge.reference_articles.storage import ReferenceArticleRepository
from forge.reference_articles.vector_index import ReferenceVectorIndex


async def import_directory(directory: Path, index_vectors: bool) -> dict[str, int]:
    repository = ReferenceArticleRepository()
    vector_index = ReferenceVectorIndex()
    counts = {
        "read": 0, "stored": 0, "duplicate": 0, "invalid": 0,
        "indexed": 0, "boilerplate_sentences": 0, "fact_candidates": 0,
    }
    raw_articles: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.json")):
        counts["read"] += 1
        try:
            raw_articles.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            counts["invalid"] += 1
    boilerplate = discover_boilerplate([
        split_sentences(str(raw.get("content") or raw.get("text") or "")) for raw in raw_articles
    ])
    counts["boilerplate_sentences"] = len(boilerplate)
    fact_candidates: list[dict[str, object]] = []
    for raw in raw_articles:
        try:
            original_content = str(raw.get("content") or raw.get("text") or "")
            cleaned_content, _ = strip_boilerplate(original_content, boilerplate)
            prepared = {**raw, "content": cleaned_content}
            fact_candidates.extend(extract_company_fact_candidates(str(raw.get("id") or ""), original_content))
            article = analyze_reference_article(prepared)
            _, stored = await repository.upsert(article)
            counts["stored" if stored else "duplicate"] += 1
        except ValueError:
            counts["invalid"] += 1
    counts["fact_candidates"] = await repository.upsert_fact_candidates(fact_candidates)
    if index_vectors:
        for row in await repository.pending(limit=max(100, counts["stored"] + 10)):
            vector_id = await vector_index.index(row)
            await repository.mark_indexed(str(row["id"]), vector_id)
            counts["indexed"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=Path("data/articles/zhihu"))
    parser.add_argument("--skip-vectors", action="store_true", help="只导入 PG，不调用嵌入模型")
    args = parser.parse_args()
    if not args.directory.is_dir():
        raise SystemExit(f"目录不存在：{args.directory}")
    result = asyncio.run(import_directory(args.directory, not args.skip_vectors))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
