# forge/evolution/worker.py

"""自进化Worker - 异步后台服务。

运行方式：
    python -m forge.evolution.worker

或作为后台进程：
    asyncio.run(run_evolution_worker())

执行完整的自进化分析周期：
1. 获取待分析session列表
2. 加载微调历史和评分
3. LLM分析反馈模式
4. 生成Prompt改进建议
5. 创建新模板版本
6. 根据配置激活或等待确认
"""

import json
import asyncio
import logging
import signal
import sys
from typing import Optional, Dict, Any, List

from forge.storage.pg_client import get_pg_pool, is_valid_uuid
from forge.evaluation.storage import get_evaluation_storage
from forge.deep_mode.session_manager import get_session_manager

from .config import get_evolution_config
from .trigger import get_evolution_trigger
from .engine import get_evolution_engine
from .prompt_manager import get_prompt_manager
from .storage import get_evolution_storage

logger = logging.getLogger(__name__)

# 运行标志
_running = True


async def run_evolution_analysis(trigger_type: str) -> Dict[str, Any]:
    """执行一次完整的自进化分析周期.

    Args:
        trigger_type: 触发类型（threshold/scheduled）

    Returns:
        分析结果字典
    """
    logger.info(f"[EvolutionWorker] Starting analysis cycle, trigger={trigger_type}")

    config = get_evolution_config()
    trigger = get_evolution_trigger()
    engine = get_evolution_engine()
    prompt_manager = get_prompt_manager()
    storage = get_evolution_storage()
    eval_storage = get_evaluation_storage()
    session_manager = get_session_manager()

    # 1. 获取待分析session列表
    pending_sessions = trigger.get_pending_sessions()

    if len(pending_sessions) < config.MIN_SAMPLE_FOR_ANALYSIS:
        logger.warning(
            f"[EvolutionWorker] Insufficient samples: "
            f"{len(pending_sessions)} < {config.MIN_SAMPLE_FOR_ANALYSIS}"
        )
        return {"status": "skipped", "reason": "insufficient_samples"}

    # 2. 创建evolution_session记录
    evolution_session_id = await storage.create_evolution_session(
        trigger_type=trigger_type,
        trigger_threshold=config.THRESHOLD_COUNT,
        analyzed_session_ids=pending_sessions,
    )

    logger.info(f"[EvolutionWorker] Evolution session created: {evolution_session_id}")

    # 3. 加载微调历史和评分
    tuning_histories = []
    quality_scores = []
    human_scores = []

    for session_id in pending_sessions[:config.MIN_SAMPLE_FOR_ANALYSIS]:
        try:
            # 加载session数据
            session = await session_manager.load_session(session_id)

            if not session:
                logger.warning(f"[EvolutionWorker] Session not found: {session_id}")
                continue

            # 获取微调历史
            tuning_history = session.get("tuning_history", [])
            if not tuning_history:
                # 尝试从数据库获取
                tuning_history = await session_manager.get_session_messages(session_id)

            if tuning_history:
                tuning_histories.append(tuning_history)

            # 获取评估结果
            eval_result = await eval_storage.get_evaluation_result(session_id)

            if eval_result:
                quality_scores.append(eval_result.get("overall_score", 0.5))
                human_scores.append(eval_result.get("human_score", 0.5))
            else:
                # 使用默认值
                quality_scores.append(0.5)
                human_scores.append(0.5)

        except Exception as e:
            logger.warning(f"[EvolutionWorker] Failed to load session {session_id}: {e}")
            continue

    logger.info(
        f"[EvolutionWorker] Loaded {len(tuning_histories)} histories, "
        f"{len(quality_scores)} scores"
    )

    # 4. 获取当前模板
    current_template = await prompt_manager.get_active_template("deep_content_generator")

    if not current_template:
        logger.error("[EvolutionWorker] No active template found")
        return {"status": "failed", "reason": "no_active_template"}

    # 5. LLM分析反馈模式
    try:
        analysis_result = await engine.analyze_feedback_patterns(
            tuning_histories=tuning_histories,
            quality_scores=quality_scores,
            prompt_template=current_template,
        )

        if not analysis_result:
            logger.warning("[EvolutionWorker] Analysis returned no result")
            await storage.update_evolution_session(
                evolution_session_id,
                status="failed",
            )
            return {"status": "failed", "reason": "analysis_failed"}

    except Exception as e:
        logger.error(f"[EvolutionWorker] Analysis error: {e}")
        await storage.update_evolution_session(
            evolution_session_id,
            status="failed",
        )
        return {"status": "failed", "reason": str(e)}

    # 6. 更新evolution_session记录
    await storage.update_evolution_session(
        evolution_session_id,
        analysis_result=analysis_result,
        suggested_changes=analysis_result.get("prompt_changes"),
        status="analyzed",
    )

    # 7. 应用Prompt修改（创建新版本）
    try:
        prompt_changes = analysis_result.get("prompt_changes", {})

        if not prompt_changes:
            logger.info("[EvolutionWorker] No prompt changes suggested")
            await storage.update_evolution_session(
                evolution_session_id,
                status="completed",
            )
            trigger.clear_pending(trigger_type)
            return {"status": "completed", "reason": "no_changes_needed"}

        # 应用修改
        new_template_content = engine.apply_prompt_changes(current_template, prompt_changes)

        # 创建新模板版本
        change_reason = analysis_result.get("root_cause_analysis", "LLM分析建议")
        change_summary = json.dumps(analysis_result.get("recommendations", []))

        new_template_id = await prompt_manager.create_new_version(
            template_key="deep_content_generator",
            system_prompt=new_template_content["system_prompt"],
            user_prompt_template=new_template_content["user_prompt_template"],
            change_reason=change_reason,
            previous_id=current_template.get("id"),
            change_summary=change_summary,
            activate=False,  # 先不激活
        )

        logger.info(f"[EvolutionWorker] New template created: {new_template_id}")

    except Exception as e:
        logger.error(f"[EvolutionWorker] Template creation error: {e}")
        await storage.update_evolution_session(
            evolution_session_id,
            status="failed",
        )
        return {"status": "failed", "reason": str(e)}

    # 8. 根据配置决定是否自动激活
    status = "pending_approval"

    if config.AUTO_ACTIVATE:
        try:
            success = await prompt_manager.activate_version(new_template_id)

            if success:
                await storage.update_evolution_session(
                    evolution_session_id,
                    status="applied",
                    applied_template_id=new_template_id,
                )
                status = "applied"
                logger.info(f"[EvolutionWorker] Template activated: {new_template_id}")
            else:
                logger.warning("[EvolutionWorker] Template activation failed")
                status = "activation_failed"

        except Exception as e:
            logger.error(f"[EvolutionWorker] Activation error: {e}")
            status = "activation_failed"

    # 9. 清空待分析队列
    trigger.clear_pending(trigger_type)

    result = {
        "status": status,
        "evolution_session_id": evolution_session_id,
        "new_template_id": new_template_id,
        "analyzed_count": len(tuning_histories),
        "patterns_found": len(analysis_result.get("patterns", [])),
        "auto_activated": config.AUTO_ACTIVATE and status == "applied",
    }

    logger.info(f"[EvolutionWorker] Analysis cycle completed: {result}")
    return result


async def run_evolution_worker():
    """运行自进化Worker主循环.

    定期检查触发条件，执行分析周期。
    """
    logger.info("[EvolutionWorker] Starting evolution worker...")

    config = get_evolution_config()
    trigger = get_evolution_trigger()

    # 从数据库恢复待分析队列
    await trigger.load_pending_from_db()

    # 注册信号处理
    def signal_handler(signum, frame):
        global _running
        logger.info(f"[EvolutionWorker] Received signal {signum}, shutting down...")
        _running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 主循环
    while _running:
        try:
            # 检查触发条件
            should_run, trigger_type = await trigger.should_trigger()

            if should_run:
                logger.info(f"[EvolutionWorker] Trigger detected: {trigger_type}")

                try:
                    result = await run_evolution_analysis(trigger_type)
                    logger.info(f"[EvolutionWorker] Analysis result: {result['status']}")

                except Exception as e:
                    logger.error(f"[EvolutionWorker] Analysis cycle failed: {e}")

            # 等待下次检查（每10分钟）
            await asyncio.sleep(600)

        except Exception as e:
            logger.error(f"[EvolutionWorker] Unexpected error: {e}")
            await asyncio.sleep(60)

    logger.info("[EvolutionWorker] Worker stopped")


def main():
    """Worker入口函数。"""
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)-16s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    # 运行Worker
    asyncio.run(run_evolution_worker())


if __name__ == "__main__":
    main()