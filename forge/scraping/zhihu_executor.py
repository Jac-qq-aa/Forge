"""知乎采集任务执行、结果规范化与缓存。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from forge.storage.redis_client import get_redis_client

from .config import ZHIHU_RESULT_CACHE_SECONDS
from .zhihu_pool import ZhihuScraperPool

logger = logging.getLogger(__name__)
_pool: ZhihuScraperPool | None = None


def get_zhihu_pool() -> ZhihuScraperPool:
    global _pool
    if _pool is None:
        _pool = ZhihuScraperPool()
    return _pool


def _cache_key(operation: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{operation}:{canonical}".encode("utf-8")).hexdigest()
    return f"zhihu-result:{digest}"


async def _get_cached(key: str) -> dict[str, Any] | None:
    try:
        client = await get_redis_client()
        value = await client.get(key)
        return json.loads(value) if value else None
    except Exception:
        logger.warning("读取知乎采集缓存失败", exc_info=True)
        return None


async def _set_cached(key: str, value: dict[str, Any]) -> None:
    try:
        client = await get_redis_client()
        await client.setex(
            key,
            ZHIHU_RESULT_CACHE_SECONDS,
            json.dumps(value, ensure_ascii=False),
        )
    except Exception:
        logger.warning("写入知乎采集缓存失败", exc_info=True)


def _parse_count(value: Any) -> int:
    from forge.tools.zhihu_scraper_persistent import parse_count_text

    return parse_count_text(value)


def _require_zhihu_url(url: str) -> str:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not (
        hostname == "zhihu.com" or hostname.endswith(".zhihu.com")
    ):
        raise ValueError("只允许采集 zhihu.com 域名")
    return url


def _require_blogger_id(value: str) -> str:
    if value.startswith(("http://", "https://")):
        return _require_zhihu_url(value)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("知乎博主 ID 格式非法")
    return value


def _raise_if_blocked(scraper: Any) -> None:
    current_url = str(getattr(getattr(scraper, "page", None), "url", "")).lower()
    if any(marker in current_url for marker in ("signin", "login", "unhuman")):
        raise RuntimeError(f"知乎触发登录或人机验证: {current_url}")


def _format_articles(articles: list[dict[str, Any]]) -> dict[str, Any]:
    result = []
    for index, article in enumerate(articles, 1):
        type_label = {
            "question": "知乎问答",
            "article": "知乎文章",
        }.get(article.get("type", ""), article.get("type", "未知"))
        result.append({
            "id": index,
            "title": article.get("title", "无标题"),
            "summary": article.get("summary", "")[:100],
            "source_url": article.get("source_url", ""),
            "type": type_label,
            "author": article.get("author", ""),
            "likes": _parse_count(
                article.get("likes")
                or article.get("like_count")
                or article.get("vote_count")
                or article.get("upvote_count")
                or article.get("likes_text")
            ),
        })
    return {"success": True, "articles": result, "count": len(result)}


async def execute_zhihu_job(job: dict[str, Any]) -> dict[str, Any]:
    operation = job["operation"]
    payload = job["payload"]
    key = _cache_key(operation, payload)
    cached = await _get_cached(key)
    if cached is not None:
        cached["cached"] = True
        return cached

    handlers = {
        "zhihu_search": _search,
        "zhihu_article_detail": _article_detail,
        "zhihu_question_answers": _question_answers,
        "zhihu_recommend_articles": _recommend_articles,
    }
    if operation not in handlers:
        raise ValueError(f"不支持的知乎采集操作: {operation}")
    result = await handlers[operation](payload)
    await _set_cached(key, result)
    return result


async def _search(payload: dict[str, Any]) -> dict[str, Any]:
    async with get_zhihu_pool().lease() as scraper:
        if payload.get("search_mode") == "blogger":
            articles = await scraper.get_user_articles(
                _require_blogger_id(payload["source"]),
                payload.get("max_results", 5),
            )
        else:
            articles = await scraper.search_articles(
                payload["source"], payload.get("max_results", 5)
            )
        _raise_if_blocked(scraper)
    return _format_articles(articles)


async def _article_detail(payload: dict[str, Any]) -> dict[str, Any]:
    async with get_zhihu_pool().lease() as scraper:
        source_url = _require_zhihu_url(payload["source_url"])
        article = await scraper.scrape_by_url(source_url)
        _raise_if_blocked(scraper)
        if article.get("title") == "需要登录":
            raise RuntimeError("知乎触发登录或人机验证")
    return {
        "success": True,
        "article": {
            "title": article.get("title", "无标题"),
            "text": article.get("text") or article.get("content") or article.get("summary") or "",
            "summary": article.get("summary", ""),
            "source_url": article.get("source_url", source_url),
            "likes": _parse_count(
                article.get("likes")
                or article.get("like_count")
                or article.get("vote_count")
                or article.get("upvote_count")
                or article.get("likes_text")
            ),
        },
    }


async def _question_answers(payload: dict[str, Any]) -> dict[str, Any]:
    async with get_zhihu_pool().lease() as scraper:
        result = await scraper.get_question_answers(
            _require_zhihu_url(payload["question_url"]),
            payload.get("max_answers", 10),
        )
        _raise_if_blocked(scraper)
    return {"success": True, **result}


async def _recommend_articles(payload: dict[str, Any]) -> dict[str, Any]:
    bloggers = payload.get("bloggers", [])

    async def scrape_blogger(blogger: dict[str, Any]) -> list[dict[str, Any]]:
        async with get_zhihu_pool().lease() as scraper:
            articles = await scraper.get_user_articles(
                _require_blogger_id(blogger["id"]),
                payload.get("max_per_blogger", 3),
            )
            _raise_if_blocked(scraper)
        for article in articles:
            article["blogger_name"] = blogger.get("name", blogger["id"])
            article["blogger_id"] = blogger["id"]
            article["likes"] = _parse_count(article.get("likes"))
        return articles

    batches = await asyncio.gather(
        *(scrape_blogger(blogger) for blogger in bloggers),
        return_exceptions=True,
    )
    articles = []
    failures = []
    for blogger, batch in zip(bloggers, batches):
        if isinstance(batch, Exception):
            failures.append(batch)
            logger.warning("获取博主文章失败 blogger=%s error=%s", blogger.get("id"), batch)
            continue
        articles.extend(batch)
    if bloggers and len(failures) == len(bloggers):
        raise RuntimeError(f"全部博主采集失败: {failures[0]}")
    return {"success": True, "articles": articles, "count": len(articles)}


async def close_zhihu_resources() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
