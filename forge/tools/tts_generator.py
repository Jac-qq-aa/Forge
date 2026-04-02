"""Edge TTS wrapper for text-to-speech generation."""

import logging
import edge_tts

logger = logging.getLogger(__name__)


class TtsGenerator:
    """Async TTS generator using Microsoft Edge TTS."""

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural"):
        self.voice = voice

    async def generate(self, text: str, output_path: str) -> str:
        """Generate audio from text.

        Args:
            text: Text to convert to speech.
            output_path: Path to save MP3 file.

        Returns:
            Path to generated audio file.
        """
        logger.info(f"[TTS] Generating audio for {len(text)} chars")
        logger.info(f"[TTS] Voice: {self.voice}")

        communicate = edge_tts.Communicate(text, self.voice)
        await communicate.save(output_path)

        logger.info(f"[TTS] Audio saved to: {output_path}")
        return output_path