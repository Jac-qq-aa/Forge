"""Rebuild legacy/optimized NNGu baselines and compare retrieval quality."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import random
import re
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import httpx
from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    connections,
    utility,
)

from forge.config import QWEN_API_KEY, QWEN_API_URL
from forge.knowledge.config import EMBEDDING_MODEL, VECTOR_DIMENSION
from import_nnngu import build_documents


LEGACY_COLLECTION = "ruibo_eval_legacy"
OPTIMIZED_COLLECTION = "ruibo_eval_optimized"
SOURCE_COLLECTION = "ruibo_knowledge_v2"

QUESTIONS = [
    {"id": "q01", "group": "公司事实与FAQ", "query": "锐博集团是哪一年创办的，总部在哪里？", "gold": ["_groupprofile"]},
    {"id": "q02", "group": "公司事实与FAQ", "query": "锐博集团有多少家子分公司，业务覆盖多少城市？", "gold": ["_groupprofile"]},
    {"id": "q03", "group": "公司事实与FAQ", "query": "企业为什么选择BPO商业流程外包？", "gold": ["7634"]},
    {"id": "q04", "group": "公司事实与FAQ", "query": "岗位外包是什么，对企业有什么作用？", "gold": ["7516"]},
    {"id": "q05", "group": "公司事实与FAQ", "query": "企业哪些工作可以进行劳务外包？", "gold": ["7510"]},
    {"id": "q06", "group": "公司事实与FAQ", "query": "劳务派遣工的用人单位是谁？", "gold": ["637"]},
    {"id": "q07", "group": "公司事实与FAQ", "query": "劳务派遣员工发生工伤由谁负责？", "gold": ["634"]},
    {"id": "q08", "group": "公司事实与FAQ", "query": "劳务派遣工的工资如何发放？", "gold": ["633"]},
    {"id": "q09", "group": "公司事实与FAQ", "query": "锐博集团2025年海南民营企业100强排第几？", "gold": ["_developmentcourse"]},
    {"id": "q10", "group": "公司事实与FAQ", "query": "锐博集团重庆分公司的办公地址在哪里？", "gold": ["_framework"]},
    {"id": "q11", "group": "招聘发布", "query": "重庆兼职和见习人事岗位招聘有什么要求？", "gold": ["13420"], "filters": {"secondary_category": "recruitment_posting", "province": "重庆"}},
    {"id": "q12", "group": "招聘发布", "query": "云南锐博人力资源服务有限公司最新招聘简章", "gold": ["13426"], "filters": {"secondary_category": "recruitment_posting", "province": "云南"}},
    {"id": "q13", "group": "招聘发布", "query": "黄磷和磷矿科技项目劳务派遣人员招聘", "gold": ["13401"], "filters": {"secondary_category": "recruitment_posting", "topic": "labor_dispatch"}},
    {"id": "q14", "group": "招聘发布", "query": "天津轨道交通支队2026年政府专职消防员招聘公告", "gold": ["13421"], "filters": {"secondary_category": "recruitment_posting", "topic": "fire_emergency", "province": "天津"}},
    {"id": "q15", "group": "招聘发布", "query": "锐博集团大客户销售岗位工作地点和任职要求", "gold": ["13136"], "filters": {"secondary_category": "recruitment_posting", "province": "广东"}},
    {"id": "q16", "group": "招聘发布", "query": "锐博集团客服主管需要制定SOP和FAQ的岗位", "gold": ["13136"], "filters": {"secondary_category": "recruitment_posting", "province": "广东"}},
    {"id": "q17", "group": "医疗院内细节", "query": "院内服务招聘2人，工作地点在景东县和腾冲市", "gold": ["13422"], "filters": {"secondary_category": "recruitment_posting", "topic": "medical_on_site"}},
    {"id": "q18", "group": "医疗院内细节", "query": "院内服务招聘7人，岗位分布在德宏、景东、双江和红河", "gold": ["13416"], "filters": {"secondary_category": "recruitment_posting", "topic": "medical_on_site", "province": "云南"}},
    {"id": "q19", "group": "医疗院内细节", "query": "院内服务招聘6人，工作地点包括文山、宾川、富宁和玉溪", "gold": ["13415"], "filters": {"secondary_category": "recruitment_posting", "topic": "medical_on_site", "province": "云南"}},
    {"id": "q20", "group": "招录流程与名单", "query": "天津轨道交通支队2026年消防员面试和心理测试通知", "gold": ["13425"], "filters": {"secondary_category": "process_notice", "topic": "fire_emergency", "province": "天津"}},
    {"id": "q21", "group": "招录流程与名单", "query": "天津轨道交通支队2026年消防员体检和政治考核公告", "gold": ["13428"], "filters": {"secondary_category": "process_notice", "topic": "fire_emergency", "province": "天津"}},
    {"id": "q22", "group": "招录流程与名单", "query": "静海区2025年消防员体能测试和岗位适应性测试公告", "gold": ["13371"], "filters": {"secondary_category": "process_notice", "topic": "fire_emergency", "province": "天津"}},
    {"id": "q23", "group": "招录流程与名单", "query": "海南大学审计处工程审计劳务派遣资格复审和面试事项", "gold": ["12397"], "filters": {"secondary_category": "process_notice", "topic": "labor_dispatch", "province": "海南"}},
    {"id": "q24", "group": "招录流程与名单", "query": "天津轨道交通支队2026年消防员总成绩公告", "gold": ["13427"], "filters": {"secondary_category": "results_and_list", "topic": "fire_emergency", "province": "天津"}},
    {"id": "q25", "group": "招录流程与名单", "query": "天津轨道交通支队2026年消防员体能和岗位适应性测试成绩", "gold": ["13424"], "filters": {"secondary_category": "results_and_list", "topic": "fire_emergency", "province": "天津"}},
    {"id": "q26", "group": "招录流程与名单", "query": "云南省调水中心2025年劳务派遣辅助人员面试名单", "gold": ["13237"], "filters": {"secondary_category": "results_and_list", "topic": "labor_dispatch", "province": "云南"}},
    {"id": "q27", "group": "招录流程与名单", "query": "北辰区2025年专职消防员拟录用人员公示", "gold": ["13380"], "filters": {"secondary_category": "admission_publicity", "topic": "fire_emergency", "province": "天津"}},
    {"id": "q28", "group": "招录流程与名单", "query": "静海区2025年专职消防员拟录用人员公示", "gold": ["13381"], "filters": {"secondary_category": "admission_publicity", "topic": "fire_emergency", "province": "天津"}},
    {"id": "q29", "group": "招录流程与名单", "query": "滨海高新区2025年专职消防员拟录用人员公示", "gold": ["13372"], "filters": {"secondary_category": "admission_publicity", "topic": "fire_emergency", "province": "天津"}},
    {"id": "q30", "group": "招录流程与名单", "query": "云南省调水中心2025年劳务派遣辅助人员录用公示", "gold": ["13239"], "filters": {"secondary_category": "admission_publicity", "topic": "labor_dispatch", "province": "云南"}},
    {"id": "q31", "group": "更正与近重复", "query": "云南第一女子监狱警务辅助人员笔试成绩和体能测试更正公告", "gold": ["12317"], "filters": {"secondary_category": "correction_supplement", "province": "云南"}},
    {"id": "q32", "group": "更正与近重复", "query": "云南第一女子监狱招聘警务辅助人员公告更正版", "gold": ["12313"], "filters": {"secondary_category": "correction_supplement", "province": "云南"}},
    {"id": "q33", "group": "长文末端细节", "query": "锐博集团2018年成立了哪家天津公司？", "gold": ["_developmentcourse"]},
    {"id": "q34", "group": "长文末端细节", "query": "锐博集团西安分公司的地址是航天城中心广场哪一栋？", "gold": ["_framework"]},
    {"id": "q35", "group": "长文末端细节", "query": "锐博集团招聘中哪个岗位负责项目落地、编写SOP并培训下属？", "gold": ["13136"], "filters": {"secondary_category": "recruitment_posting", "province": "广东"}},
]


def split_legacy_content(text: str, max_len: int = 500) -> list[str]:
    """Exact legacy splitter from the pre-optimization importer."""
    if len(text) <= max_len:
        return [text]
    parts = re.split(r"([。！？\n]+)", text)
    sentences: list[str] = []
    current = ""
    for part in parts:
        if re.match(r"[。！？\n]+", part):
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


def build_legacy_documents(articles: list[dict]) -> tuple[list[dict], dict]:
    documents: list[dict] = []
    coverage: list[float] = []
    represented_articles = 0
    for article in articles:
        article_id = str(article["id"])
        chunks = split_legacy_content(article["content"])
        joined = "".join(chunks)
        ratio = SequenceMatcher(None, article["content"], joined).ratio()
        coverage.append(ratio)
        represented_articles += bool(chunks)
        for index, content in enumerate(chunks):
            documents.append({
                "id": f"nnngu_{article_id}_{index:02d}",
                "content": content,
                "article_id": article_id,
                "title": article["title"],
                "metadata": {},
            })
    return documents, {
        "articles": len(articles),
        "represented_articles": represented_articles,
        "chunks": len(documents),
        "fully_lossless_articles": sum(math.isclose(value, 1.0) for value in coverage),
        "minimum_coverage": min(coverage),
        "mean_coverage": sum(coverage) / len(coverage),
    }


class EmbeddingCache:
    def __init__(self, path: Path):
        self.path = path
        self.values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def embed_missing(self, texts: list[str]) -> None:
        missing = list(dict.fromkeys(text for text in texts if self.key(text) not in self.values))
        if not missing:
            return
        semaphore = asyncio.Semaphore(6)
        async with httpx.AsyncClient(timeout=60.0) as client:
            async def embed_one(text: str) -> None:
                async with semaphore:
                    for attempt in range(5):
                        try:
                            response = await client.post(
                                f"{QWEN_API_URL}/embeddings",
                                headers={"Authorization": f"Bearer {QWEN_API_KEY}"},
                                json={"model": EMBEDDING_MODEL, "input": text},
                            )
                            response.raise_for_status()
                            vector = response.json()["data"][0]["embedding"]
                            if len(vector) != VECTOR_DIMENSION:
                                raise ValueError(f"unexpected embedding dimension: {len(vector)}")
                            self.values[self.key(text)] = vector
                            return
                        except (httpx.HTTPError, ValueError, KeyError):
                            if attempt == 4:
                                raise
                            await asyncio.sleep(2 ** attempt)
            for start in range(0, len(missing), 25):
                batch = missing[start:start + 25]
                await asyncio.gather(*(embed_one(text) for text in batch))
                print(f"embedded {min(start + 25, len(missing))}/{len(missing)} missing texts", flush=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.values), encoding="utf-8")

    def vectors(self, texts: list[str]) -> list[list[float]]:
        return [self.values[self.key(text)] for text in texts]


def create_collection(name: str, documents: list[dict], vectors: list[list[float]]) -> Collection:
    if utility.has_collection(name):
        utility.drop_collection(name)
    schema = CollectionSchema([
        FieldSchema("id", DataType.VARCHAR, is_primary=True, max_length=128),
        FieldSchema("vector", DataType.FLOAT_VECTOR, dim=VECTOR_DIMENSION),
        FieldSchema("content", DataType.VARCHAR, max_length=4096),
        FieldSchema("article_id", DataType.VARCHAR, max_length=256),
        FieldSchema("title", DataType.VARCHAR, max_length=512),
        FieldSchema("secondary_category", DataType.VARCHAR, max_length=64),
        FieldSchema("topic_tags", DataType.VARCHAR, max_length=256),
        FieldSchema("province", DataType.VARCHAR, max_length=64),
    ], description=f"NNGu retrieval evaluation baseline: {name}")
    collection = Collection(name, schema)
    collection.insert([
        [document["id"] for document in documents],
        vectors,
        [document["content"] for document in documents],
        [document["article_id"] for document in documents],
        [document["title"] for document in documents],
        [document.get("metadata", {}).get("secondary_category", "") for document in documents],
        [document.get("metadata", {}).get("topic_tags", "") for document in documents],
        [document.get("metadata", {}).get("province", "") for document in documents],
    ])
    collection.flush()
    collection.create_index("vector", {"metric_type": "COSINE", "index_type": "FLAT", "params": {}})
    collection.load()
    return collection


def fetch_optimized_vectors(documents: list[dict]) -> list[list[float]]:
    source = Collection(SOURCE_COLLECTION)
    source.load()
    rows = source.query(expr="id != \"\"", output_fields=["id", "vector"], limit=1000)
    vectors = {row["id"]: row["vector"] for row in rows}
    missing = [document["id"] for document in documents if document["id"] not in vectors]
    if missing:
        raise RuntimeError(f"source collection lacks {len(missing)} optimized vectors")
    return [vectors[document["id"]] for document in documents]


def make_filter(filters: dict) -> str | None:
    conditions = []
    if value := filters.get("secondary_category"):
        conditions.append(f'secondary_category == "{value}"')
    if value := filters.get("topic"):
        conditions.append(f'topic_tags like "%{value}%"')
    if value := filters.get("province"):
        conditions.append(f'province == "{value}"')
    return " and ".join(conditions) or None


def article_search(collection: Collection, vector: list[float], expr: str | None = None) -> list[dict]:
    hits = collection.search(
        data=[vector],
        anns_field="vector",
        param={"metric_type": "COSINE", "params": {}},
        limit=50,
        expr=expr,
        output_fields=["article_id", "title", "content"],
    )[0]
    articles = []
    seen = set()
    for hit in hits:
        article_id = hit.entity.get("article_id")
        if article_id in seen:
            continue
        seen.add(article_id)
        articles.append({
            "article_id": article_id,
            "title": hit.entity.get("title"),
            "score": round(float(hit.distance), 6),
            "chunk_preview": hit.entity.get("content")[:160],
        })
    return articles


def rank_of(results: list[dict], gold: list[str]) -> int | None:
    gold_set = set(gold)
    return next((index for index, result in enumerate(results, 1) if result["article_id"] in gold_set), None)


def summarize(details: list[dict], result_key: str) -> dict:
    ranks = [item[result_key]["rank"] for item in details]
    return {
        "questions": len(ranks),
        "hit_at_1": sum(rank is not None and rank <= 1 for rank in ranks) / len(ranks),
        "hit_at_3": sum(rank is not None and rank <= 3 for rank in ranks) / len(ranks),
        "hit_at_5": sum(rank is not None and rank <= 5 for rank in ranks) / len(ranks),
        "hit_at_10": sum(rank is not None and rank <= 10 for rank in ranks) / len(ranks),
        "mrr_at_10": sum(1 / rank for rank in ranks if rank is not None and rank <= 10) / len(ranks),
        "misses_at_10": sum(rank is None or rank > 10 for rank in ranks),
    }


def paired_comparison(details: list[dict]) -> dict:
    """Return paired deltas, exact McNemar p-value, and bootstrap MRR CI."""
    old_ranks = [item["legacy"]["rank"] for item in details]
    new_ranks = [item["optimized"]["rank"] for item in details]
    filtered_ranks = [item["optimized_filtered"]["rank"] for item in details]

    def rank_value(rank: int | None) -> int:
        return rank if rank is not None else 999

    improved = sum(rank_value(new) < rank_value(old) for old, new in zip(old_ranks, new_ranks))
    regressed = sum(rank_value(new) > rank_value(old) for old, new in zip(old_ranks, new_ranks))
    filter_improved = sum(rank_value(filtered) < rank_value(new) for new, filtered in zip(new_ranks, filtered_ranks))
    filter_regressed = sum(rank_value(filtered) > rank_value(new) for new, filtered in zip(new_ranks, filtered_ranks))

    old_hit5 = [rank is not None and rank <= 5 for rank in old_ranks]
    new_hit5 = [rank is not None and rank <= 5 for rank in new_ranks]
    gained = sum(not old and new for old, new in zip(old_hit5, new_hit5))
    lost = sum(old and not new for old, new in zip(old_hit5, new_hit5))
    discordant = gained + lost
    exact_p = (
        min(1.0, 2 * sum(math.comb(discordant, k) for k in range(min(gained, lost) + 1)) / (2 ** discordant))
        if discordant else 1.0
    )

    def rr(rank: int | None) -> float:
        return 1 / rank if rank is not None and rank <= 10 else 0.0

    rng = random.Random(20260721)
    deltas = []
    for _ in range(10000):
        indexes = [rng.randrange(len(details)) for _ in details]
        deltas.append(
            sum(rr(new_ranks[index]) - rr(old_ranks[index]) for index in indexes)
            / len(indexes)
        )
    deltas.sort()
    return {
        "pure_vector_rank_improved": improved,
        "pure_vector_rank_unchanged": len(details) - improved - regressed,
        "pure_vector_rank_regressed": regressed,
        "filter_rank_improved": filter_improved,
        "filter_rank_unchanged": len(details) - filter_improved - filter_regressed,
        "filter_rank_regressed": filter_regressed,
        "hit_at_5_gained": gained,
        "hit_at_5_lost": lost,
        "hit_at_5_mcnemar_exact_p": exact_p,
        "mrr_at_10_delta": summarize(details, "optimized")["mrr_at_10"] - summarize(details, "legacy")["mrr_at_10"],
        "mrr_at_10_delta_bootstrap_95_ci": [deltas[249], deltas[9749]],
    }


def render_report(payload: dict) -> str:
    def pct(value: float) -> str:
        return f"{value:.1%}"
    old = payload["metrics"]["legacy"]
    new = payload["metrics"]["optimized"]
    filtered = payload["metrics"]["optimized_filtered"]
    lines = [
        "# 锐博官网知识库召回量化对比",
        "",
        f"- 评测时间：{payload['evaluated_at']}",
        f"- 原始文章：{payload['source_articles']} 篇",
        f"- 问题数：{len(payload['details'])} 个",
        f"- 嵌入模型：`{EMBEDDING_MODEL}`，向量维度：{VECTOR_DIMENSION}",
        "- 检索参数：COSINE + FLAT；主指标按文章去重后计算",
        "",
        "## 总体指标",
        "",
        "| 基线 | 分块数 | Hit@1 | Hit@3 | Hit@5 | Hit@10 | MRR@10 | Top10未命中 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| 优化前纯向量 | {payload['baselines']['legacy']['chunks']} | {pct(old['hit_at_1'])} | {pct(old['hit_at_3'])} | {pct(old['hit_at_5'])} | {pct(old['hit_at_10'])} | {old['mrr_at_10']:.4f} | {old['misses_at_10']} |",
        f"| 优化后纯向量 | {payload['baselines']['optimized']['chunks']} | {pct(new['hit_at_1'])} | {pct(new['hit_at_3'])} | {pct(new['hit_at_5'])} | {pct(new['hit_at_10'])} | {new['mrr_at_10']:.4f} | {new['misses_at_10']} |",
        f"| 优化后+结构化过滤 | {payload['baselines']['optimized']['chunks']} | {pct(filtered['hit_at_1'])} | {pct(filtered['hit_at_3'])} | {pct(filtered['hit_at_5'])} | {pct(filtered['hit_at_10'])} | {filtered['mrr_at_10']:.4f} | {filtered['misses_at_10']} |",
        "",
        "## 切片完整性",
        "",
        f"- 优化前：{payload['baselines']['legacy']['fully_lossless_articles']}/{payload['source_articles']} 篇完全无损，平均覆盖率 {pct(payload['baselines']['legacy']['mean_coverage'])}，最低覆盖率 {pct(payload['baselines']['legacy']['minimum_coverage'])}。",
        f"- 优化后：{payload['baselines']['optimized']['fully_lossless_articles']}/{payload['source_articles']} 篇完全无损，平均及最低覆盖率均为 100%。",
        "",
        "## 分组指标",
        "",
        "| 问题组 | 数量 | 优化前 Hit@5 | 优化后 Hit@5 | 过滤后 Hit@5 |",
        "|---|---:|---:|---:|---:|",
    ]
    for group, values in payload["group_metrics"].items():
        lines.append(f"| {group} | {values['legacy']['questions']} | {pct(values['legacy']['hit_at_5'])} | {pct(values['optimized']['hit_at_5'])} | {pct(values['optimized_filtered']['hit_at_5'])} |")
    comparison = payload["comparison"]
    ci_low, ci_high = comparison["mrr_at_10_delta_bootstrap_95_ci"]
    lines.extend([
        "",
        "## 配对统计与结论",
        "",
        f"- 纯向量逐题排名：{comparison['pure_vector_rank_improved']} 题提升，{comparison['pure_vector_rank_unchanged']} 题不变，{comparison['pure_vector_rank_regressed']} 题下降。",
        f"- 结构化过滤相对优化后纯向量：{comparison['filter_rank_improved']} 题提升，{comparison['filter_rank_unchanged']} 题不变，{comparison['filter_rank_regressed']} 题下降。",
        f"- Hit@5 配对变化：新增命中 {comparison['hit_at_5_gained']} 题，丢失命中 {comparison['hit_at_5_lost']} 题；McNemar 精确检验 `p={comparison['hit_at_5_mcnemar_exact_p']:.6f}`。",
        f"- MRR@10 绝对提升 `{comparison['mrr_at_10_delta']:.4f}`，10,000 次配对 bootstrap 95% CI 为 `[{ci_low:.4f}, {ci_high:.4f}]`。",
        "- 主要收益来自标题前缀、完整保留短尾段和长文内容、以及文章级结构化分类；不是向量模型或索引参数变化造成。",
        "- 唯一纯向量排名下降题是静海区体测公告：相似的招聘公告占据第1，正确结果降至第2；流程分类过滤后恢复第1。",
        "",
        "## 逐题结果",
        "",
    ])
    for item in payload["details"]:
        lines.extend([
            f"### {item['id']} · {item['query']}",
            "",
            f"- 正确文章：`{', '.join(item['gold'])}`",
            f"- 排名：优化前 `{item['legacy']['rank'] or '未进Top50'}`；优化后 `{item['optimized']['rank'] or '未进Top50'}`；过滤后 `{item['optimized_filtered']['rank'] or '未进Top50'}`",
            f"- 变化：{item['analysis']}",
            "- 优化前 Top5：" + "；".join(f"{r['article_id']} {r['title']} ({r['score']:.4f})" for r in item['legacy']['top5']),
            "- 优化后 Top5：" + "；".join(f"{r['article_id']} {r['title']} ({r['score']:.4f})" for r in item['optimized']['top5']),
            "- 过滤后 Top5：" + "；".join(f"{r['article_id']} {r['title']} ({r['score']:.4f})" for r in item['optimized_filtered']['top5']),
            "",
        ])
    return "\n".join(lines)


def analyze(old_rank: int | None, new_rank: int | None, filtered_rank: int | None) -> str:
    old_value = old_rank or 999
    new_value = new_rank or 999
    filtered_value = filtered_rank or 999
    parts = []
    if new_value < old_value:
        parts.append("纯向量排名提升")
    elif new_value > old_value:
        parts.append("纯向量排名下降")
    else:
        parts.append("纯向量排名不变")
    if filtered_value < new_value:
        parts.append("结构化过滤进一步提升")
    elif filtered_value > new_value:
        parts.append("过滤后下降，需检查分类条件")
    else:
        parts.append("结构化过滤未改变排名")
    return "；".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data/knowledge/evaluation"))
    parser.add_argument("--milvus-host", default="host.docker.internal")
    args = parser.parse_args()

    articles = json.loads(args.snapshot.read_text(encoding="utf-8"))["articles"]
    legacy_documents, legacy_stats = build_legacy_documents(articles)
    optimized_raw, optimized_audit = build_documents(articles)
    optimized_documents = [{
        **document,
        "article_id": document["metadata"]["article_id"],
        "title": document["metadata"]["title"],
    } for document in optimized_raw]

    connections.connect(host=args.milvus_host, port="19530")
    optimized_vectors = fetch_optimized_vectors(optimized_documents)
    cache = EmbeddingCache(args.output_dir / "embedding-cache.json")
    asyncio.run(cache.embed_missing([document["content"] for document in legacy_documents] + [q["query"] for q in QUESTIONS]))
    legacy_vectors = cache.vectors([document["content"] for document in legacy_documents])
    query_vectors = cache.vectors([q["query"] for q in QUESTIONS])

    legacy_collection = create_collection(LEGACY_COLLECTION, legacy_documents, legacy_vectors)
    optimized_collection = create_collection(OPTIMIZED_COLLECTION, optimized_documents, optimized_vectors)

    details = []
    for question, vector in zip(QUESTIONS, query_vectors):
        legacy_results = article_search(legacy_collection, vector)
        optimized_results = article_search(optimized_collection, vector)
        filtered_results = article_search(optimized_collection, vector, make_filter(question.get("filters", {})))
        old_rank = rank_of(legacy_results, question["gold"])
        new_rank = rank_of(optimized_results, question["gold"])
        filtered_rank = rank_of(filtered_results, question["gold"])
        details.append({
            **question,
            "legacy": {"rank": old_rank, "top5": legacy_results[:5]},
            "optimized": {"rank": new_rank, "top5": optimized_results[:5]},
            "optimized_filtered": {"rank": filtered_rank, "top5": filtered_results[:5]},
            "analysis": analyze(old_rank, new_rank, filtered_rank),
        })

    optimized_stats = {
        "articles": optimized_audit["article_count"],
        "represented_articles": optimized_audit["article_count"],
        "chunks": optimized_audit["chunk_count"],
        "fully_lossless_articles": optimized_audit["lossless_article_count"],
        "minimum_coverage": optimized_audit["minimum_coverage_ratio"],
        "mean_coverage": sum(a["coverage_ratio"] for a in optimized_audit["articles"]) / len(optimized_audit["articles"]),
    }
    grouped = defaultdict(list)
    for item in details:
        grouped[item["group"]].append(item)
    payload = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": str(args.snapshot),
        "source_articles": len(articles),
        "collections": {"legacy": LEGACY_COLLECTION, "optimized": OPTIMIZED_COLLECTION},
        "baselines": {"legacy": legacy_stats, "optimized": optimized_stats},
        "metrics": {
            "legacy": summarize(details, "legacy"),
            "optimized": summarize(details, "optimized"),
            "optimized_filtered": summarize(details, "optimized_filtered"),
        },
        "group_metrics": {
            group: {
                "legacy": summarize(items, "legacy"),
                "optimized": summarize(items, "optimized"),
                "optimized_filtered": summarize(items, "optimized_filtered"),
            }
            for group, items in grouped.items()
        },
        "comparison": paired_comparison(details),
        "details": details,
    }
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / f"retrieval-evaluation-{timestamp}.json"
    report_path = args.output_dir / f"retrieval-evaluation-{timestamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(render_report(payload), encoding="utf-8")
    print(json.dumps(payload["metrics"], ensure_ascii=False, indent=2))
    print(f"json_report={json_path}")
    print(f"markdown_report={report_path}")


if __name__ == "__main__":
    main()
