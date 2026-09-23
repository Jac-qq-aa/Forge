"""最终文章表达质量评估队列生产者。"""

import hashlib
import json
import logging
import time
from typing import Optional

import redis.asyncio as redis

from forge.config import (
    EVAL_QUEUE_NAME,
    EVAL_REDIS_DB,
    REDIS_HOST,
    REDIS_PASSWORD,
    REDIS_PORT,
)


ARTICLE_FINALIZED_EVENT = "article_finalized"
_redis_client: Optional[redis.Redis] = None
logger = logging.getLogger(__name__)


def article_hash(article: str) -> str:
    """生成用于幂等判断的文章 SHA-256。"""
    return hashlib.sha256(article.encode("utf-8")).hexdigest()


def _get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            db=EVAL_REDIS_DB,
            decode_responses=True,
        )
    return _redis_client


async def enqueue_article_expression_evaluation(
    session_id: str,
    final_article: str,
    target_platform: str,
) -> str:
    """投递定稿事件；队列中不包含文章正文或任何事实材料。"""
    digest = article_hash(final_article)
    payload = {
        "event_type": ARTICLE_FINALIZED_EVENT,
        "session_id": session_id,
        "article_hash": digest,
        "target_platform": target_platform,
        "timestamp": time.time(),
    }
    client = _get_redis_client()
    await client.lpush(EVAL_QUEUE_NAME, json.dumps(payload, ensure_ascii=False))
    return digest


async def try_enqueue_article_expression_evaluation(
    session_id: str,
    final_article: str,
    target_platform: str,
) -> bool:
    """尽力投递评估事件；失败时返回 False，不影响定稿主流程。"""
    try:
        await enqueue_article_expression_evaluation(
            session_id=session_id,
            final_article=final_article,
            target_platform=target_platform,
        )
        return True
    except Exception as exc:
        logger.warning(
            "[ArticleEvalQueue] Failed to enqueue evaluation for %s: %s",
            session_id,
            exc,
        )
        return False
