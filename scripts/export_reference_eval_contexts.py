"""把文章快照导出为 DeepEval contexts；不生成或手写 goldens。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forge.reference_articles.analyzer import discover_boilerplate, split_sentences, strip_boilerplate


def export_contexts(source: Path, output: Path, limit: int = 0) -> int:
    articles = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(source.glob("*.json"))]
    boilerplate = discover_boilerplate([
        split_sentences(str(article.get("content") or article.get("text") or ""))
        for article in articles
    ])
    contexts: list[list[str]] = []
    selected = articles[:limit] if limit > 0 else articles
    for article in selected:
        content, _ = strip_boilerplate(
            str(article.get("content") or article.get("text") or ""), boilerplate
        )
        contexts.append([
            f"标题：{article.get('title') or ''}\n平台：zhihu_article\n正文：{content[:6000]}"
        ])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(contexts, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(contexts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/articles/zhihu"))
    parser.add_argument("--output", type=Path, default=Path("tests/evals/reference_contexts.json"))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    print(f"exported_contexts={export_contexts(args.source, args.output, args.limit)}")


if __name__ == "__main__":
    main()
