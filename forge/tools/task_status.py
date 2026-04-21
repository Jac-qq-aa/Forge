"""数字人视频生成任务状态管理。

支持异步任务模式：
1. 创建任务 -> 返回 task_id
2. 后台生成视频
3. 查询任务状态
"""

import os
import json
import asyncio
import logging
from typing import Optional
from datetime import datetime
from forge.config import VIDEO_OUTPUT_DIR

logger = logging.getLogger(__name__)

# 任务状态文件目录
TASK_STATUS_DIR = f"{VIDEO_OUTPUT_DIR}/task_status"
os.makedirs(TASK_STATUS_DIR, exist_ok=True)


class TaskStatus:
    """任务状态。"""
    PENDING = "pending"       # 等待处理
    RUNNING = "running"       # 正在生成
    COMPLETED = "completed"   # 已完成
    FAILED = "failed"         # 失败


def get_status_file(task_id: str) -> str:
    """获取任务状态文件路径。"""
    return f"{TASK_STATUS_DIR}/{task_id}.json"


def save_task_status(task_id: str, status: str, **extra):
    """保存任务状态。"""
    status_file = get_status_file(task_id)
    data = {
        "task_id": task_id,
        "status": status,
        "updated_at": datetime.now().isoformat(),
        **extra
    }
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"[TaskStatus] Saved: {task_id} -> {status}")


def load_task_status(task_id: str) -> Optional[dict]:
    """加载任务状态。"""
    status_file = get_status_file(task_id)
    if os.path.exists(status_file):
        with open(status_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


async def run_generation_task(task_id: str, text: str, video_path: str, avatar_url: str = "", voice: str = "longxiaochun"):
    """后台运行视频生成任务。

    Args:
        task_id: 任务 ID
        text: 文案内容
        video_path: 视频输出路径
        avatar_url: 自定义头像图片 URL（可选）
        voice: 语音风格 ID（可选，默认 longxiaochun）
    """
    from forge.tools.digital_human_generator import DigitalHumanGenerator

    try:
        # 更新状态为运行中
        save_task_status(task_id, TaskStatus.RUNNING)

        # 创建生成器并生成视频
        generator = DigitalHumanGenerator(avatar_url=avatar_url, voice=voice)
        await generator.generate(text, video_path)

        # 更新状态为完成
        save_task_status(
            task_id,
            TaskStatus.COMPLETED,
            video_path=video_path,
            task_dir=generator.task_dir
        )
        logger.info(f"[TaskStatus] Task completed: {task_id}")

    except Exception as e:
        # 更新状态为失败
        save_task_status(task_id, TaskStatus.FAILED, error=str(e))
        logger.error(f"[TaskStatus] Task failed: {task_id} - {e}")