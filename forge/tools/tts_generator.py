"""阿里云 DashScope TTS 语音合成（CosyVoice）。

使用 DashScope Python SDK 调用 CosyVoice 模型。
与数字人视频生成使用同一 API Key（QWEN_API_KEY）。

参考文档：https://help.aliyun.com/zh/model-studio/text-to-speech
"""

import logging
import os
import re
import dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer

logger = logging.getLogger(__name__)


class TtsError(Exception):
    """TTS 语音合成失败。"""

    def __init__(self, message: str, error_code: str = None):
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class TtsGenerator:
    """阿里云 CosyVoice 语音合成器。"""

    # 默认模型和语音
    DEFAULT_MODEL = "cosyvoice-v1"
    DEFAULT_VOICE = "longxiaochun"  # 龙小春（中文女声，温柔）- 免费可用

    # 可选语音列表（免费可用）
    VOICES = {
        # CosyVoice 免费可用语音
        "longxiaochun": "龙小春（女声，温柔）",
        "longwan": "龙婉（女声，活泼）",
        "longyue": "龙悦（女声，甜美）",
        "longfei": "龙飞（男声，沉稳）",
        "longshuo": "龙硕（男声）",
        # 以下语音需要付费服务
        "longjielidou": "龙杰力豆（童声）- 需付费",
        "longteng": "龙腾（男声，大气）- 需付费",
        "longshuang": "龙双（女声）- 需付费",
        "longyao": "龙瑶（女声）- 需付费",
        "longhui": "龙辉（男声）- 需付费",
    }

    # 错误码到友好提示的映射
    ERROR_MESSAGES = {
        "AllocationQuota.FreeTierOnly": "阿里云 TTS 免费额度已耗尽，请在 DashScope 控制台开通付费服务",
        "InvalidParameter": "语音参数错误，请检查文本内容和语音设置",
        "RateLimitExceeded": "请求频率超限，请稍后再试",
        "InsufficientBalance": "账户余额不足，请充值后继续使用",
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
            raise ValueError("QWEN_API_KEY 未配置。请检查: 1) .env文件是否存在 2) 是否运行了 load_dotenv() 3) 环境变量是否设置")

        # 设置 DashScope API Key（必须设置，否则SDK返回空数据）
        dashscope.api_key = self.api_key
        logger.info(f"[TTS] Initialized: model={self.model}, voice={self.voice}")

    async def generate(self, text: str, output_path: str) -> str:
        """生成语音音频。

        Args:
            text: 要转换的文本
            output_path: 音频保存路径

        Returns:
            生成的音频文件路径
        """
        logger.info(f"[TTS] Generating audio for {len(text)} chars: {text[:50]}...")
        logger.info(f"[TTS] Model: {self.model}, Voice: {self.voice}")

        # 确保输出目录存在
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        # 调用 CosyVoice TTS（SDK 是同步的，在异步中直接调用）
        try:
            # 再次确认API Key已设置（防止并发问题）
            if not dashscope.api_key:
                dashscope.api_key = self.api_key
                logger.warning("[TTS] Re-setting DashScope credentials")

            synthesizer = SpeechSynthesizer(
                model=self.model,
                voice=self.voice
            )

            # call() 返回音频 bytes
            audio_data = synthesizer.call(text)

            if not audio_data:
                logger.error(f"[TTS] Audio data is None or empty")
                logger.error(f"[TTS] Input text: {text}")
                logger.error(f"[TTS] API key is set but TTS returned empty data - check key validity and service status")
                raise TtsError("TTS 未返回音频数据。请检查: 1) DashScope API Key 是否有效 2) 是否开通了CosyVoice服务 3) 账户是否有余额")

            # 保存到文件
            with open(output_path, "wb") as f:
                f.write(audio_data)

            logger.info(f"[TTS] Audio saved: {output_path}, size: {len(audio_data)} bytes")
            return output_path

        except Exception as e:
            error_str = str(e)

            # 尝试从错误信息中提取 DashScope 的错误码和错误消息
            error_code = None
            error_message = error_str

            # 解析 TaskFailed 错误格式
            # 格式: TaskFailed: {"header":{"error_code":"XXX","error_message":"YYY"}...}
            if "TaskFailed" in error_str or "error_code" in error_str:
                try:
                    # 提取 JSON 部分
                    json_match = re.search(r'\{.*\}', error_str)
                    if json_match:
                        import json
                        error_json = json.loads(json_match.group())
                        header = error_json.get("header", {})
                        error_code = header.get("error_code", "")
                        raw_message = header.get("error_message", "")

                        # 使用友好提示映射
                        if error_code in self.ERROR_MESSAGES:
                            error_message = self.ERROR_MESSAGES[error_code]
                        elif raw_message:
                            error_message = raw_message
                except Exception as parse_error:
                    logger.warning(f"[TTS] Failed to parse error JSON: {parse_error}")

            logger.error(f"[TTS] Failed: code={error_code}, message={error_message}")
            raise TtsError(error_message, error_code)


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
