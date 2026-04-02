"""Director node - video generation with TTS and FFmpeg."""

import logging
import os
import uuid
from forge.graph.state import GraphState
from forge.tools.tts_generator import TtsGenerator
from forge.tools.video_composer import VideoComposer
from forge.config import VIDEO_OUTPUT_DIR

logger = logging.getLogger(__name__)


async def director_node(state: GraphState) -> dict:
    """Generate video from final script."""
    final_script = state.get("final_script", "")
    raw_content = state.get("raw_content", {})
    images = raw_content.get("images", [])

    logger.info("[Director] Starting video generation")
    logger.info(f"[Director] Script length: {len(final_script)} chars")
    logger.info(f"[Director] Available images: {len(images)}")

    # Ensure output directory exists
    os.makedirs(VIDEO_OUTPUT_DIR, exist_ok=True)

    video_id = uuid.uuid4().hex[:8]
    audio_path = f"{VIDEO_OUTPUT_DIR}/audio_{video_id}.mp3"
    video_path = f"{VIDEO_OUTPUT_DIR}/output_{video_id}.mp4"

    # Generate TTS audio
    tts = TtsGenerator()
    await tts.generate(final_script, audio_path)

    # Compose video
    composer = VideoComposer()
    await composer.compose(audio_path, images, video_path)

    logger.info(f"[Director] Video generated: {video_path}")
    logger.info("[Director] Node completed")

    return {"video_path": video_path}