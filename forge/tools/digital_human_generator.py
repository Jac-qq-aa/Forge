"""数字人念稿视频生成器，调用阿里云万相数字人 API。

阿里云万相数字人 (wan2.2-s2v) 基于 DashScope API，
支持图片+音频生成口型同步的数字人视频。

限制：
- 音频时长 < 20秒
- 音频文件 < 15MB

长文本方案：
- 分段生成多个视频（每段 < 60字）
- 用 FFmpeg 合并成完整视频

参考文档：https://help.aliyun.com/zh/model-studio/wan-s2v-api
"""

import logging
import aiohttp
import asyncio
import os
import hashlib
import time
import subprocess
import re
from forge.config import VIDEO_OUTPUT_DIR, QWEN_API_KEY
from forge.tools.tts_generator import TtsGenerator

logger = logging.getLogger(__name__)


class DigitalHumanError(Exception):
    """数字人视频生成失败。"""
    pass


class DigitalHumanGenerator:
    """阿里云万相数字人视频生成器。"""

    # 预设的数字人图片 URL
    DEFAULT_IMAGE_URL = "https://img.alicdn.com/imgextra/i3/O1CN011FObkp1T7Ttowoq4F_!!6000000002335-0-tps-1440-1797.jpg"

    # DashScope API 地址
    BASE_URL = "https://dashscope.aliyuncs.com/api/v1"

    # 每段最大字数（确保音频 < 20秒，TTS 约 3-4 字/秒，保守取 60 字）
    MAX_SEGMENT_LENGTH = 60

    def __init__(self, api_key: str = None, avatar_url: str = None, voice: str = None):
        self.api_key = api_key or QWEN_API_KEY
        if not self.api_key:
            raise ValueError("QWEN_API_KEY 未配置")

        # 自定义头像图片 URL（可选）
        self.avatar_url = avatar_url or self.DEFAULT_IMAGE_URL

        # 语音风格（可选）
        self.voice = voice or "longxiaochun"

        # 任务目录（保存所有中间产物）
        self.task_dir = None
        self.task_id = None

    async def generate(self, text: str, output_path: str) -> str:
        """生成数字人念稿视频（支持长文本分段）。

        保存所有中间产物：
        - 音频文件：task_dir/audio/*.mp3
        - 分段视频：task_dir/segments/*.mp4
        - 最终视频：output_path
        """
        logger.info(f"[DigitalHuman] 开始生成，文本长度: {len(text)} 字")

        # 创建任务目录（按时间戳命名）
        self.task_id = hashlib.md5(f"{text}{time.time()}".encode()).hexdigest()[:12]
        self.task_dir = f"{VIDEO_OUTPUT_DIR}/task_{self.task_id}"
        os.makedirs(self.task_dir, exist_ok=True)
        os.makedirs(f"{self.task_dir}/audio", exist_ok=True)
        os.makedirs(f"{self.task_dir}/segments", exist_ok=True)
        logger.info(f"[DigitalHuman] 任务目录: {self.task_dir}")

        # 分段处理
        segments = self._split_text(text)
        logger.info(f"[DigitalHuman] 分为 {len(segments)} 段: {[len(s) for s in segments]} 字")

        if len(segments) == 1:
            # 单段直接生成
            return await self._generate_single(segments[0], output_path)
        else:
            # 多段生成并合并
            return await self._generate_segments(segments, output_path)

    def _split_text(self, text: str) -> list[str]:
        """将文本分段，每段不超过 MAX_SEGMENT_LENGTH 字。

        优先按句子分割，保持语义完整性。
        """
        # 先按句子分割
        sentences = re.split(r'([。！？；\n])', text)

        # 合并句子和分隔符
        merged_sentences = []
        for i in range(0, len(sentences) - 1, 2):
            if sentences[i]:
                merged_sentences.append(sentences[i] + (sentences[i+1] if i+1 < len(sentences) else ''))

        # 如果最后还有剩余
        if len(sentences) % 2 == 1 and sentences[-1]:
            merged_sentences.append(sentences[-1])

        # 如果没有句子分割符，按逗号分割
        if len(merged_sentences) == 0:
            merged_sentences = text.split('，')

        # 合并短句子到 MAX_SEGMENT_LENGTH
        segments = []
        current_segment = ""

        for sentence in merged_sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            # 如果单个句子超过限制，强制分割
            if len(sentence) > self.MAX_SEGMENT_LENGTH:
                if current_segment:
                    segments.append(current_segment)
                    current_segment = ""
                # 强制按字数分割
                while len(sentence) > self.MAX_SEGMENT_LENGTH:
                    segments.append(sentence[:self.MAX_SEGMENT_LENGTH])
                    sentence = sentence[self.MAX_SEGMENT_LENGTH:]
                if sentence:
                    current_segment = sentence
            # 如果加入后不超过限制
            elif len(current_segment) + len(sentence) <= self.MAX_SEGMENT_LENGTH:
                current_segment += sentence
            # 否则开始新段
            else:
                if current_segment:
                    segments.append(current_segment)
                current_segment = sentence

        # 添加最后一段
        if current_segment:
            segments.append(current_segment)

        # 确保每段都不超过限制
        final_segments = []
        for seg in segments:
            if len(seg) > self.MAX_SEGMENT_LENGTH:
                # 再次分割
                for i in range(0, len(seg), self.MAX_SEGMENT_LENGTH):
                    final_segments.append(seg[i:i+self.MAX_SEGMENT_LENGTH])
            else:
                final_segments.append(seg)

        return final_segments if final_segments else [text[:self.MAX_SEGMENT_LENGTH]]

    async def _generate_single(self, text: str, output_path: str) -> str:
        """生成单个视频。

        保存中间产物：
        - 音频：task_dir/audio/segment_0.mp3
        - 视频：task_dir/segments/segment_0.mp4 和 output_path
        """
        # Step 1: TTS 生成音频（保存到任务目录）
        logger.info(f"[DigitalHuman] TTS 生成音频: {text[:30]}...")
        tts = TtsGenerator(voice=self.voice)
        audio_path = f"{self.task_dir}/audio/segment_0.mp3"
        await tts.generate(text, audio_path)

        # 检查音频时长
        duration = self._get_audio_duration(audio_path)
        logger.info(f"[DigitalHuman] 音频时长: {duration:.2f}s, 已保存: {audio_path}")

        if duration > 20:
            logger.warning(f"[DigitalHuman] 音频超过20s，截断文本")
            # 尝试更短的文本
            shorter_text = text[:30]
            await tts.generate(shorter_text, audio_path)
            duration = self._get_audio_duration(audio_path)
            logger.info(f"[DigitalHuman] 新音频时长: {duration:.2f}s")

        # Step 2: 上传到 OSS
        audio_url = await self._upload_audio(audio_path)
        logger.info(f"[DigitalHuman] 音频 URL: {audio_url}")

        # Step 3: 创建视频任务
        task_id = await self._create_video_task(audio_url)
        logger.info(f"[DigitalHuman] 任务 ID: {task_id}")

        # Step 4: 等待完成
        video_url = await self._wait_for_task(task_id)
        logger.info(f"[DigitalHuman] 视频生成完成")

        # Step 5: 下载视频（保存到任务目录）
        segment_path = f"{self.task_dir}/segments/segment_0.mp4"
        await self._download_video(video_url, segment_path)
        logger.info(f"[DigitalHuman] 分段视频已保存: {segment_path}")

        # 复制到最终输出路径
        import shutil
        shutil.copy(segment_path, output_path)
        logger.info(f"[DigitalHuman] 最终视频已保存: {output_path}")

        # 不删除中间文件，保留供查看
        logger.info(f"[DigitalHuman] 中间文件保留在: {self.task_dir}")

        return output_path

    async def _generate_segments(self, segments: list[str], output_path: str) -> str:
        """分段生成多个视频并合并。

        保存中间产物：
        - 音频：task_dir/audio/segment_i.mp3（每段）
        - 分段视频：task_dir/segments/segment_i.mp4（每段）
        - 最终视频：output_path
        """
        segment_videos = []
        segment_audios = []
        first_error = None  # 保存第一个错误信息

        for i, segment in enumerate(segments):
            logger.info(f"[DigitalHuman] 处理第 {i+1}/{len(segments)} 段")

            # Step 1: TTS 生成音频（保存到任务目录）
            logger.info(f"[DigitalHuman] TTS 生成音频: {segment[:30]}...")
            tts = TtsGenerator(voice=self.voice)
            audio_path = f"{self.task_dir}/audio/segment_{i}.mp3"

            try:
                await tts.generate(segment, audio_path)
                segment_audios.append(audio_path)
            except Exception as e:
                error_msg = str(e)
                logger.error(f"[DigitalHuman] 第 {i+1} 段 TTS 失败: {error_msg}")
                if first_error is None:
                    first_error = error_msg  # 保存第一个错误
                continue  # 跳过该段，继续下一段

            # 检查音频时长
            duration = self._get_audio_duration(audio_path)
            logger.info(f"[DigitalHuman] 音频时长: {duration:.2f}s, 已保存: {audio_path}")

            if duration > 20:
                logger.warning(f"[DigitalHuman] 第 {i+1} 段音频超过20s")
                continue  # 跳过该段

            # Step 2: 上传到 OSS + Step 3: 创建视频任务 + Step 4: 等待完成并下载
            try:
                audio_url = await self._upload_audio(audio_path)

                api_task_id = await self._create_video_task(audio_url)
                logger.info(f"[DigitalHuman] API 任务 ID: {api_task_id}")

                video_url = await self._wait_for_task(api_task_id)
                segment_path = f"{self.task_dir}/segments/segment_{i}.mp4"
                await self._download_video(video_url, segment_path)
                segment_videos.append(segment_path)
                logger.info(f"[DigitalHuman] 分段视频已保存: {segment_path}")
            except Exception as e:
                error_msg = str(e)
                logger.error(f"[DigitalHuman] 第 {i+1} 段生成失败: {error_msg}")
                if first_error is None:
                    first_error = error_msg
                continue

        if not segment_videos:
            # 传递详细的错误信息
            if first_error:
                raise DigitalHumanError(f"所有分段生成失败: {first_error}")
            else:
                raise DigitalHumanError("所有分段生成失败")

        # 合并视频
        if len(segment_videos) == 1:
            # 单个视频直接复制
            import shutil
            shutil.copy(segment_videos[0], output_path)
        else:
            # FFmpeg 合并
            await self._merge_videos(segment_videos, output_path)

        logger.info(f"[DigitalHuman] 最终视频: {output_path}")
        logger.info(f"[DigitalHuman] 中间文件保留在: {self.task_dir}")
        logger.info(f"[DigitalHuman] 音频文件: {len(segment_audios)} 个")
        logger.info(f"[DigitalHuman] 分段视频: {len(segment_videos)} 个")

        return output_path

    async def _merge_videos(self, video_paths: list[str], output_path: str):
        """用 FFmpeg 合并多个视频。"""
        logger.info(f"[DigitalHuman] 合并 {len(video_paths)} 个视频")

        # 创建 concat 文件（保存到任务目录）
        concat_file = f"{self.task_dir}/concat.txt"
        with open(concat_file, "w") as f:
            for path in video_paths:
                f.write(f"file '{path}'\n")
        logger.info(f"[DigitalHuman] Concat 文件已保存: {concat_file}")

        # FFmpeg 合并
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_file,
            "-c", "copy",
            output_path
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error = stderr.decode() if stderr else "Unknown error"
            raise DigitalHumanError(f"视频合并失败: {error}")

        # 不删除 concat 文件，保留供查看

    def _get_audio_duration(self, audio_path: str) -> float:
        """获取音频时长。"""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
                capture_output=True, text=True
            )
            return float(result.stdout.strip())
        except:
            return 0.0

    async def _upload_audio(self, audio_path: str) -> str:
        """上传音频到 OSS。"""
        oss_bucket = os.getenv("OSS_BUCKET", "")
        oss_endpoint = os.getenv("OSS_ENDPOINT", "")
        oss_key_id = os.getenv("OSS_ACCESS_KEY_ID", "")
        oss_key_secret = os.getenv("OSS_ACCESS_KEY_SECRET", "")

        logger.info(f"[DigitalHuman] OSS: bucket={oss_bucket}")

        if oss_bucket and oss_endpoint and oss_key_id and oss_key_secret:
            try:
                import oss2
                auth = oss2.Auth(oss_key_id, oss_key_secret)
                bucket = oss2.Bucket(auth, oss_endpoint, oss_bucket)

                object_key = f"digital_human/audio_{int(time.time())}.mp3"
                result = bucket.put_object_from_file(object_key, audio_path)

                if result.status == 200:
                    audio_url = f"https://{oss_bucket}.{oss_endpoint}/{object_key}"
                    logger.info(f"[DigitalHuman] OSS 上传成功")
                    return audio_url
            except Exception as e:
                logger.warning(f"[DigitalHuman] OSS 上传失败: {e}")

        # 备用：使用示例音频
        logger.warning("[DigitalHuman] 使用示例音频 URL")
        return "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250825/iaqpio/input_audio.MP3"

    async def _create_video_task(self, audio_url: str) -> str:
        """创建视频生成任务。"""
        url = f"{self.BASE_URL}/services/aigc/image2video/video-synthesis"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        payload = {
            "model": "wan2.2-s2v",
            "input": {
                "image_url": self.avatar_url,  # 使用自定义或默认头像
                "audio_url": audio_url,
            },
            "parameters": {"resolution": "480P"}
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    raise DigitalHumanError(f"创建任务失败: {resp.status} - {error}")

                result = await resp.json()
                task_id = result.get("output", {}).get("task_id")
                if not task_id:
                    raise DigitalHumanError(f"无 task_id: {result}")
                return task_id

    async def _wait_for_task(self, task_id: str, max_wait: int = 600) -> str:
        """等待任务完成。"""
        url = f"{self.BASE_URL}/tasks/{task_id}"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        waited = 0
        poll_interval = 15

        async with aiohttp.ClientSession() as session:
            while waited < max_wait:
                await asyncio.sleep(poll_interval)
                waited += poll_interval

                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        continue

                    result = await resp.json()
                    output = result.get("output", {})
                    status = output.get("task_status", "UNKNOWN")

                    logger.info(f"[DigitalHuman] 状态: {status}, 等待 {waited}s")

                    if status == "SUCCEEDED":
                        video_url = output.get("results", {}).get("video_url")
                        if video_url:
                            return video_url
                        raise DigitalHumanError("成功但无 video_url")

                    elif status == "FAILED":
                        code = output.get("code", "Unknown")
                        message = output.get("message", "")
                        raise DigitalHumanError(f"任务失败: {code} - {message}")

                    elif status in ["PENDING", "RUNNING"]:
                        continue

        raise DigitalHumanError(f"超时: {max_wait}s")

    async def _download_video(self, url: str, output_path: str):
        """下载视频。"""
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    with open(output_path, "wb") as f:
                        f.write(content)
                else:
                    raise DigitalHumanError(f"下载失败: {resp.status}")