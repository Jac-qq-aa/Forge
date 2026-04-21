"""AI 文生视频生成器，调用阿里云百炼视频生成 API。

阿里云百炼提供文生视频服务，使用 DashScope API。

参考文档：https://help.aliyun.com/document_detail/608652.html
"""

import logging
import asyncio
import aiohttp
import os
from forge.config import QWEN_API_KEY, VIDEO_OUTPUT_DIR

logger = logging.getLogger(__name__)


class VideoGeneratorError(Exception):
    """视频生成失败。"""
    pass


class VideoGenerator:
    """AI 文生视频生成器，使用阿里云百炼 API。"""

    def __init__(self):
        if not QWEN_API_KEY:
            raise ValueError("QWEN_API_KEY not set")
        self.api_key = QWEN_API_KEY
        # 阿里云视频生成 API
        self.api_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/text2video"

    async def generate(
        self,
        prompt: str,
        output_path: str,
        duration: int = 5,
        resolution: str = "720p"
    ) -> str:
        """从文本生成视频。

        Args:
            prompt: 视频描述文本（脚本内容）
            output_path: 视频保存路径
            duration: 视频时长（秒），默认5秒
            resolution: 视频分辨率，默认720p

        Returns:
            生成的视频文件路径
        """
        logger.info(f"[VideoGenerator] Generating video from prompt")
        logger.info(f"[VideoGenerator] Prompt length: {len(prompt)} chars, duration: {duration}s")

        # 截取 prompt（API 有长度限制）
        if len(prompt) > 500:
            prompt = prompt[:500]
            logger.info(f"[VideoGenerator] Prompt truncated to 500 chars")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",  # 异步模式
        }

        payload = {
            "model": "video-generation-v1",
            "input": {
                "text": prompt,
            },
            "parameters": {
                "duration": duration,
                "resolution": resolution,
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                # 发起生成请求（异步）
                async with session.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        logger.error(f"[VideoGenerator] API error: {resp.status} - {error}")
                        raise VideoGeneratorError(f"API error: {resp.status}")

                    result = await resp.json()
                    task_id = result.get("output", {}).get("task_id")

                    if not task_id:
                        logger.error(f"[VideoGenerator] No task_id in response: {result}")
                        raise VideoGeneratorError("No task_id in response")

                    logger.info(f"[VideoGenerator] Task created: {task_id}")

                # 等待任务完成并获取结果
                video_url = await self._wait_for_task(task_id, session)

                # 下载视频到本地
                await self._download_video(video_url, output_path)

                logger.info(f"[VideoGenerator] Video saved to: {output_path}")
                return output_path

        except Exception as e:
            logger.error(f"[VideoGenerator] Failed: {e}")
            raise VideoGeneratorError(f"Video generation failed: {e}") from e

    async def _wait_for_task(self, task_id: str, session: aiohttp.ClientSession, max_wait: int = 300) -> str:
        """等待异步任务完成并获取视频 URL。

        Args:
            task_id: 任务 ID
            session: aiohttp session
            max_wait: 最大等待时间（秒）

        Returns:
            视频下载 URL
        """
        query_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        waited = 0
        poll_interval = 5

        while waited < max_wait:
            await asyncio.sleep(poll_interval)
            waited += poll_interval

            async with session.get(query_url, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning(f"[VideoGenerator] Query failed: {resp.status}")
                    continue

                result = await resp.json()
                status = result.get("output", {}).get("task_status")

                logger.info(f"[VideoGenerator] Task status: {status} (waited {waited}s)")

                if status == "SUCCEEDED":
                    video_url = result.get("output", {}).get("video_url")
                    if video_url:
                        return video_url
                    else:
                        raise VideoGeneratorError("Task succeeded but no video_url")

                elif status == "FAILED":
                    error_msg = result.get("output", {}).get("message", "Unknown error")
                    raise VideoGeneratorError(f"Task failed: {error_msg}")

                elif status in ["PENDING", "RUNNING"]:
                    continue  # 继续等待

                else:
                    logger.warning(f"[VideoGenerator] Unknown status: {status}")

        raise VideoGeneratorError(f"Task timeout after {max_wait}s")

    async def _download_video(self, url: str, output_path: str):
        """下载视频到本地。"""
        logger.info(f"[VideoGenerator] Downloading video from: {url[:50]}...")

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    with open(output_path, "wb") as f:
                        f.write(content)
                    logger.info(f"[VideoGenerator] Downloaded: {len(content)} bytes")
                else:
                    raise VideoGeneratorError(f"Download failed: {resp.status}")