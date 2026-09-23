# forge/evaluation/worker.py

"""评估Worker - 异步消费队列执行评估。

运行方式：
    python -m forge.evaluation.worker

或作为后台进程：
    asyncio.run(run_evaluation_worker())
"""

import json
import asyncio
import logging
import signal
import sys
from typing import Optional

import redis

from forge.config import (
    EVAL_QUEUE_NAME,
    EVAL_REDIS_DB,
    REDIS_HOST,
    REDIS_PASSWORD,
    REDIS_PORT,
)
from .article_expression import ArticleExpressionEvaluator
from .article_queue import ARTICLE_FINALIZED_EVENT, article_hash
from .article_storage import (
    get_completed_article_expression,
    get_final_article,
    save_article_expression,
)
from .engine import EvaluationEngine
from .storage import save_probe_log, get_session_probe_logs, save_evaluation_result

logger = logging.getLogger(__name__)

# Redis客户端
_redis_client: Optional[redis.Redis] = None

# 运行标志
_running = True


def _get_redis_client() -> redis.Redis:
    """获取Redis客户端。"""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            db=EVAL_REDIS_DB,
        )
    return _redis_client


def should_trigger_evaluation(node_name: str) -> bool:
    """节点探针不触发最终评估；深度流程由 article_finalized 事件触发。"""
    return False


async def process_article_finalized(payload: dict) -> None:
    """处理最终文章表达质量评估事件。"""
    session_id = str(payload.get("session_id") or "")
    queued_hash = str(payload.get("article_hash") or "")
    target_platform = str(payload.get("target_platform") or "zhihu_article")
    if not session_id or not queued_hash:
        raise ValueError("article_finalized 事件缺少 session_id 或 article_hash")

    final_article = await get_final_article(session_id)
    if not final_article:
        raise ValueError(f"会话 {session_id} 不存在最终文章")

    # 始终评价数据库中的最新定稿，避免旧队列消息覆盖新定稿结果。
    current_hash = article_hash(final_article)
    if current_hash != queued_hash:
        logger.info(
            "[EvalWorker] Queued article is stale; evaluating latest draft: %s",
            session_id,
        )
    completed = await get_completed_article_expression(session_id, current_hash)
    if completed is not None:
        logger.info(
            "[EvalWorker] Article expression evaluation already completed: %s / %s",
            session_id,
            current_hash,
        )
        return

    evaluator = None
    try:
        evaluator = ArticleExpressionEvaluator()
        result = await evaluator.evaluate(final_article, target_platform)
        await save_article_expression(session_id, current_hash, result.to_dict())
        logger.info(
            "[EvalWorker] Article expression evaluation completed: %s, score=%.4f",
            session_id,
            result.overall_score,
        )
    except Exception as e:
        await save_article_expression(
            session_id,
            current_hash,
            {
                "status": "failed",
                "model": getattr(evaluator, "model_name", None),
                "error_message": str(e),
            },
        )
        raise


async def process_queue_message(payload: dict) -> None:
    """按事件类型路由评估队列消息。"""
    if payload.get("event_type") == ARTICLE_FINALIZED_EVENT:
        await process_article_finalized(payload)
        return
    await process_probe_log(payload)


async def process_probe_log(payload: dict) -> None:
    """处理单条probe log。

    Args:
        payload: 探针数据包
    """
    session_id = payload.get("session_id", "unknown")
    node_name = payload.get("node_name", "unknown")

    logger.info(f"[EvalWorker] Processing: {session_id} / {node_name}")

    # 1. 保存probe log
    await save_probe_log(payload)

    # 2. 判断是否触发完整评估
    if should_trigger_evaluation(node_name):
        logger.info(f"[EvalWorker] Triggering evaluation for: {session_id}")

        try:
            # 获取该session所有probe logs
            session_logs = await get_session_probe_logs(session_id)

            if not session_logs:
                logger.warning(f"[EvalWorker] No logs found for session: {session_id}")
                return

            # 从probe_logs提取original_text和draft_text
            original_text = None
            draft_text = None

            # 从editor节点的output_metrics获取改写后的草稿
            for log in session_logs:
                if log.get("node_name") == "editor":
                    output_metrics = log.get("output_metrics", {})
                    draft_text = output_metrics.get("draft_text", "")
                    break

            # 从session表获取原始文本（source_article）
            from forge.storage.pg_client import get_pg_pool
            pool = await get_pg_pool()
            async with pool.acquire() as conn:
                from uuid import UUID
                session_row = await conn.fetchrow(
                    'SELECT source_article FROM sessions WHERE id = $1',
                    UUID(session_id)
                )
                if session_row and session_row.get("source_article"):
                    source_article = session_row["source_article"]
                    if isinstance(source_article, dict):
                        original_text = source_article.get("text", "")
                    elif isinstance(source_article, str):
                        import json
                        try:
                            source_article = json.loads(source_article)
                            original_text = source_article.get("text", "")
                        except:
                            original_text = source_article

            # 执行评估
            engine = EvaluationEngine()
            eval_result = await engine.evaluate_session(
                session_id=session_id,
                probe_logs=session_logs,
                original_text=original_text,
                draft_text=draft_text,
            )

            # 保存评估结果
            await save_evaluation_result(session_id, eval_result)

            logger.info(f"[EvalWorker] Evaluation completed: {session_id}, score={eval_result.get('overall_score')}")

        except Exception as e:
            logger.error(f"[EvalWorker] Evaluation failed: {session_id}, error={e}")
            # 保存失败状态
            await save_evaluation_result(session_id, {
                "status": "failed",
                "error_message": str(e),
            })


async def run_evaluation_worker() -> None:
    """运行评估Worker主循环。

    阻塞式消费Redis队列，处理每条消息。
    """
    logger.info("[EvalWorker] Starting evaluation worker...")
    logger.info(f"[EvalWorker] Queue: {EVAL_QUEUE_NAME}, Redis: {REDIS_HOST}:{REDIS_PORT}/db{EVAL_REDIS_DB}")

    client = _get_redis_client()

    # 注册信号处理
    def signal_handler(signum, frame):
        global _running
        logger.info(f"[EvalWorker] Received signal {signum}, shutting down...")
        _running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    while _running:
        try:
            # 阻塞获取队列消息（BRPOP超时30秒）
            result = client.brpop(EVAL_QUEUE_NAME, timeout=30)

            if result is None:
                # 超时，继续等待
                continue

            # 解析消息
            _, payload_bytes = result
            payload = json.loads(payload_bytes)

            # 处理消息
            await process_queue_message(payload)

        except redis.ConnectionError as e:
            logger.error(f"[EvalWorker] Redis connection error: {e}, reconnecting...")
            await asyncio.sleep(5)
            _redis_client = None  # 强制重新连接

        except json.JSONDecodeError as e:
            logger.error(f"[EvalWorker] JSON decode error: {e}")
            continue

        except Exception as e:
            logger.error(f"[EvalWorker] Unexpected error: {e}")
            await asyncio.sleep(1)

    logger.info("[EvalWorker] Worker stopped")


def main():
    """Worker入口函数。"""
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)-12s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    # 运行Worker
    asyncio.run(run_evaluation_worker())


if __name__ == "__main__":
    main()
