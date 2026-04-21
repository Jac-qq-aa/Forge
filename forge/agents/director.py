"""Director node - video generation with TTS and FFmpeg, or text-only output for articles."""

import logging
import os
import uuid
from forge.graph.state import GraphState
from forge.tools.tts_generator import TtsGenerator
from forge.tools.video_composer import VideoComposer
from forge.config import VIDEO_OUTPUT_DIR

logger = logging.getLogger(__name__)


async def director_node(state: GraphState) -> dict:
    """Generate output based on target platform.

    For video platforms (xhs_video, zhihu_video):
    - Generate TTS audio
    - Compose video with FFmpeg
    - Save script as txt file

    For article platforms (zhihu_article):
    - Only save script as txt file (no video generation)
    """
    final_script = state.get("final_script", "")
    raw_content = state.get("raw_content", {})
    target_platform = state.get("target_platform", "xhs_video")
    images = raw_content.get("images", [])
    source_url = raw_content.get("source_url", "")

    is_video_platform = target_platform in ["xhs_video", "zhihu_video"]
    is_article_platform = target_platform == "zhihu_article"

    logger.info(f"[Director] Starting output generation for platform: {target_platform}")
    logger.info(f"[Director] Script length: {len(final_script)} chars")
    if is_video_platform:
        logger.info(f"[Director] Available images: {len(images)}")

    # Ensure output directory exists
    os.makedirs(VIDEO_OUTPUT_DIR, exist_ok=True)

    # Generate unique ID for this output
    output_id = uuid.uuid4().hex[:8]
    script_path = f"{VIDEO_OUTPUT_DIR}/script_{output_id}.txt"

    # Save script as txt file
    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(f"{'='*60}\n")
            f.write(f"来源: {source_url}\n")
            f.write(f"目标平台: {target_platform}\n")
            f.write(f"{'='*60}\n\n")
            f.write(final_script)
        logger.info(f"[Director] Script saved to: {script_path}")
    except Exception as e:
        logger.warning(f"[Director] Failed to save script: {e}")

    # Video platforms: generate audio and video
    if is_video_platform:
        audio_path = f"{VIDEO_OUTPUT_DIR}/audio_{output_id}.mp3"
        video_path = f"{VIDEO_OUTPUT_DIR}/output_{output_id}.mp4"

        # Generate TTS audio
        tts = TtsGenerator()
        await tts.generate(final_script, audio_path)

        # Compose video
        composer = VideoComposer()
        await composer.compose(audio_path, images, video_path)

        logger.info(f"[Director] Video generated: {video_path}")
        logger.info(f"[Director] Script saved: {script_path}")
        logger.info("[Director] Node completed")

        return {"video_path": video_path, "script_path": script_path}

    # Article platforms: only save text, skip video generation
    elif is_article_platform:
        logger.info(f"[Director] Article saved: {script_path}")
        logger.info("[Director] Node completed (text-only, no video)")

        return {"video_path": "", "script_path": script_path}

    # Fallback
    else:
        logger.warning(f"[Director] Unknown platform: {target_platform}, defaulting to text-only")
        return {"video_path": "", "script_path": script_path}