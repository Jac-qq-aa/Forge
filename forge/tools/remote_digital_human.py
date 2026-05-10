"""数字人形象模型调用模块 - 47.107.254.82:13321

API 规范：
- POST /generate_video/
- multipart/form-data
- 参数: image (jpg图片), audio (mp3/wav音频)
- 返回: {"video_url": "..."} 或 {"error": "..."}
"""

import logging
import aiohttp
import asyncio
import os
import tempfile

logger = logging.getLogger(__name__)


class RemoteDigitalHumanGenerator:
    """远程数字人视频生成器。

    调用 47.107.254.82:13321 的数字人模型服务。
    """

    API_URL = "http://47.107.254.82:13321/generate_video/"

    async def generate(self, image_path: str, audio_path: str, output_path: str) -> str:
        """生成数字人视频。

        Args:
            image_path: 头像图片路径 (jpg, 推荐 512x512 或 1024x1024)
            audio_path: 音频文件路径 (mp3 或 wav)
            output_path: 输出视频路径

        Returns:
            生成的视频路径

        Raises:
            Exception: 生成失败
        """
        logger.info(f"[RemoteDigitalHuman] 开始生成")
        logger.info(f"  图片: {image_path}")
        logger.info(f"  音频: {audio_path}")

        # 检查文件
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"图片不存在: {image_path}")
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"音频不存在: {audio_path}")

        # 读取文件
        with open(image_path, 'rb') as img_f:
            image_data = img_f.read()
        with open(audio_path, 'rb') as audio_f:
            audio_data = audio_f.read()

        # 构建 multipart form
        form = aiohttp.FormData()
        form.add_field('image', image_data, filename='avatar.jpg', content_type='image/jpeg')
        form.add_field('audio', audio_data, filename='speech.mp3', content_type='audio/mpeg')

        # 发送请求
        logger.info(f"[RemoteDigitalHuman] 发送请求...")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.API_URL,
                    data=form,
                    timeout=aiohttp.ClientTimeout(total=300)
                ) as resp:
                    result = await resp.json()

                    if 'error' in result:
                        raise Exception(f"服务端错误: {result['error']}")

                    if 'video_url' in result or 'video_path' in result:
                        video_url = result.get('video_url') or result.get('video_path')
                        logger.info(f"[RemoteDigitalHuman] 视频地址: {video_url}")

                        # 下载视频
                        await self._download_video(video_url, output_path)
                        logger.info(f"[RemoteDigitalHuman] 完成: {output_path}")
                        return output_path

                    raise Exception(f"未知返回格式: {result}")

        except asyncio.TimeoutError:
            raise Exception("请求超时 (300s)")
        except Exception as e:
            logger.error(f"[RemoteDigitalHuman] 失败: {e}")
            raise

    async def _download_video(self, video_url: str, output_path: str):
        """下载生成的视频。"""
        # 如果是本地路径，直接复制
        if video_url.startswith('/') or not video_url.startswith('http'):
            # 服务器返回的是本地路径，无法直接下载
            logger.warning(f"[RemoteDigitalHuman] 返回本地路径: {video_url}")
            logger.warning(f"[RemoteDigitalHuman] 需要在服务器上配置文件服务")
            raise Exception(f"服务器返回本地路径，无法下载: {video_url}")

        # HTTP 下载
        async with aiohttp.ClientSession() as session:
            async with session.get(video_url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    raise Exception(f"下载失败: {resp.status}")
                content = await resp.read()
                with open(output_path, 'wb') as f:
                    f.write(content)


# ============================================================================
# 快速测试函数
# ============================================================================

async def test_remote_digital_human():
    """测试远程数字人服务。"""
    generator = RemoteDigitalHumanGenerator()

    # 测试文件
    image_path = "/home/hugo/Forge/avatar_standard.jpg"
    audio_path = "/home/hugo/Forge/test_audio.mp3"
    output_path = "/home/hugo/Forge/output_remote.mp4"

    try:
        result = await generator.generate(image_path, audio_path, output_path)
        print(f"✅ 成功: {result}")
    except Exception as e:
        print(f"❌ 失败: {e}")


if __name__ == "__main__":
    asyncio.run(test_remote_digital_human())