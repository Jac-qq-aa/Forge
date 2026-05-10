"""文生视频节点 - AI 视频生成。

从改写后的脚本内容生成视频。
"""

import logging
import os
import hashlib
import time
from langsmith import traceable
from forge.graph.state import GraphState
from forge.tools.video_generator import VideoGenerator, VideoGeneratorError
from forge.config import VIDEO_OUTPUT_DIR
from forge.evaluation.probe_decorator import with_probe

logger = logging.getLogger(__name__)


@traceable(name="Video_Generator")
@with_probe("video_generator")
async def video_generator_node(state: GraphState) -> dict:
    """从改写后的脚本生成视频。

    输入：
    - rewritten_draft：改写后的文案
    - target_platform：目标平台
    - generate_video：是否生成视频（用户选择）

    输出：
    - video_path：生成的视频路径
    - video_error：错误信息（如有）
    """
    # 检查是否启用视频生成
    generate_video = state.get("generate_video", False)
    if not generate_video:
        logger.info("[VideoGeneratorNode] Skip: generate_video is False")
        return {"video_path": ""}

    rewritten_draft = state.get("rewritten_draft", "")
    final_script = state.get("final_script", "")
    target_platform = state.get("target_platform", "xhs_video")

    # 判断是否需要生成视频
    if target_platform not in ["xhs_video", "zhihu_video"]:
        logger.info("[VideoGeneratorNode] Skip: target platform is not video type")
        return {"video_path": ""}

    # 获取内容（优先用 final_script，fallback 到 rewritten_draft）
    content = final_script or rewritten_draft
    if not content:
        logger.warning("[VideoGeneratorNode] No content to generate video")
        return {"video_path": "", "video_error": "No content"}

    logger.info(f"[VideoGeneratorNode] Starting video generation")
    logger.info(f"[VideoGeneratorNode] Content length: {len(content)} chars")

    # 确保输出目录存在
    os.makedirs(VIDEO_OUTPUT_DIR, exist_ok=True)

    # 生成视频文件名
    hash_key = hashlib.md5(f"{content}{time.time()}".encode()).hexdigest()[:8]
    video_path = f"{VIDEO_OUTPUT_DIR}/video_{hash_key}.mp4"

    try:
        generator = VideoGenerator()

        # 提取脚本内容作为视频提示词
        prompt = content

        # 生成视频
        await generator.generate(
            prompt=prompt,
            output_path=video_path,
            duration=5,  # 默认5秒视频
            resolution="720p"
        )

        logger.info(f"[VideoGeneratorNode] Video generated: {video_path}")
        return {"video_path": video_path}

    except VideoGeneratorError as e:
        logger.error(f"[VideoGeneratorNode] Video generation failed: {e}")
        return {"video_path": "", "video_error": str(e)}

    except ValueError as e:
        # API Key 未配置
        logger.error(f"[VideoGeneratorNode] Config error: {e}")
        return {"video_path": "", "video_error": f"API Key not configured: {e}"}

    except Exception as e:
        logger.error(f"[VideoGeneratorNode] Unexpected error: {e}")
        return {"video_path": "", "video_error": str(e)}