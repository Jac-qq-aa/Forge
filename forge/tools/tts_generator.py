"""阿里云 DashScope TTS 语音合成（CosyVoice）。

使用 DashScope Python SDK 调用 CosyVoice 模型。
与数字人视频生成使用同一 API Key（QWEN_API_KEY）。

参考文档：https://help.aliyun.com/zh/model-studio/text-to-speech
"""

import logging
import os
import dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer

logger = logging.getLogger(__name__)


class TtsError(Exception):
    """TTS 语音合成失败。"""
    pass


class TtsGenerator:
    """阿里云 CosyVoice 语音合成器。"""

    # 默认模型和语音
    DEFAULT_MODEL = "cosyvoice-v1"
    DEFAULT_VOICE = "longxiaochun"  # 龙小春（中文女声，温柔）

    # 可选语音列表
    VOICES = {
        # CosyVoice 预设语音
        "longxiaochun": "龙小春（女声，温柔）",
        "longwan": "龙婉（女声，活泼）",
        "longyue": "龙悦（女声，甜美）",
        "longfei": "龙飞（男声，沉稳）",
        "longjielidou": "龙杰力豆（童声）",
        "longshuo": "龙硕（男声）",
        "longteng": "龙腾（男声，大气）",
        "longshuang": "龙双（女声）",
        "longyao": "龙瑶（女声）",
        "longhui": "龙辉（男声）",
        # 更多语音可参考阿里云文档
    }

    def __init__(self, model: str = None, voice: str = None, api_key: str = None):
        """初始化 TTS 生成器。

        Args:
            model: 模型名称，默认 cosyvoice-v1
            voice: 语音名称，默认 longxiaochun
            api_key: DashScope API Key，默认从配置读取
        """
        self.model = model or self.DEFAULT_MODEL
        self.voice = voice or self.DEFAULT_VOICE
        self.api_key = api_key or os.getenv("QWEN_API_KEY")

        if not self.api_key:
            raise ValueError("QWEN_API_KEY 未配置")

        # 设置 DashScope API Key
        dashscope.api_key = self.api_key

    async def generate(self, text: str, output_path: str) -> str:
        """生成语音音频。

        Args:
            text: 要转换的文本
            output_path: 音频保存路径

        Returns:
            生成的音频文件路径
        """
        logger.info(f"[TTS] Generating audio for {len(text)} chars")
        logger.info(f"[TTS] Model: {self.model}, Voice: {self.voice}")

        # 确保输出目录存在
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        # 调用 CosyVoice TTS（SDK 是同步的，在异步中直接调用）
        try:
            synthesizer = SpeechSynthesizer(
                model=self.model,
                voice=self.voice
            )

            # call() 返回音频 bytes
            audio_data = synthesizer.call(text)

            if not audio_data:
                raise TtsError("TTS 未返回音频数据")

            # 保存到文件
            with open(output_path, "wb") as f:
                f.write(audio_data)

            logger.info(f"[TTS] Audio saved: {output_path}, size: {len(audio_data)} bytes")
            return output_path

        except Exception as e:
            logger.error(f"[TTS] Failed: {e}")
            raise TtsError(f"语音合成失败: {e}")


# ===== 备用方案：Edge TTS =====

class EdgeTtsGenerator:
    """Edge TTS 备用实现（微软免费服务）。"""

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural"):
        self.voice = voice

    async def generate(self, text: str, output_path: str) -> str:
        """使用 Edge TTS 生成音频。"""
        import edge_tts

        logger.info(f"[TTS] Using Edge TTS fallback, voice: {self.voice}")
        communicate = edge_tts.Communicate(text, self.voice)
        await communicate.save(output_path)

        logger.info(f"[TTS] Audio saved: {output_path}")
        return output_path