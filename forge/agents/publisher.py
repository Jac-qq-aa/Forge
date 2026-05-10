"""Publisher node - dual-platform content publishing."""

import logging
from langsmith import traceable
from forge.graph.state import GraphState
from forge.tools.xhs_publisher import XhsPublisher
from forge.tools.zhihu_publisher import ZhihuPublisher
from forge.evaluation.probe_decorator import with_probe

logger = logging.getLogger(__name__)


@traceable(name="Publisher")
@with_probe("publisher")
async def publisher_node(state: GraphState) -> dict:
    """Publish content to target platform."""
    target_platform = state.get("target_platform", "xhs_video")
    video_path = state.get("video_path", "")
    final_script = state.get("final_script", "")
    skip_publish = state.get("skip_publish", False)

    logger.info(f"[Publisher] Starting publication to: {target_platform}")
    logger.info(f"[Publisher] Video path: {video_path}")
    logger.info(f"[Publisher] Skip publish (dry-run): {skip_publish}")

    # Extract title from script (first line or first 50 chars)
    lines = final_script.strip().split("\n")
    title = lines[0][:50] if lines else "无标题"
    description = final_script

    # Dry-run mode: skip actual browser publishing
    if skip_publish:
        logger.info("[Publisher] DRY-RUN MODE: Skipping browser automation")
        publish_status = "DRY-RUN: 跳过实际发布（测试模式）"
        logger.info(f"[Publisher] Would have published: title='{title}'")
        logger.info("[Publisher] Node completed (dry-run)")
        return {"publish_status": publish_status}

    result = {"success": False, "error": "Unknown platform"}

    try:
        if target_platform == "xhs_video":
            async with XhsPublisher() as publisher:
                await publisher.login()
                result = await publisher.publish_video(video_path, title, description)

        elif target_platform == "zhihu_article":
            async with ZhihuPublisher() as publisher:
                await publisher.login()
                result = await publisher.publish_article(title, description)

        elif target_platform == "zhihu_video":
            async with ZhihuPublisher() as publisher:
                await publisher.login()
                result = await publisher.publish_video(video_path, title, description)

        else:
            logger.error(f"[Publisher] Unknown target platform: {target_platform}")
            return {"publish_status": f"FAILED: 未知目标平台 {target_platform}"}

    except Exception as e:
        logger.error(f"[Publisher] Publication error: {e}")
        result = {"success": False, "error": str(e)}

    if result.get("success"):
        publish_status = f"SUCCESS: {result.get('post_url', '已发布')}"
        logger.info(f"[Publisher] {publish_status}")
    else:
        publish_status = f"FAILED: {result.get('error', '未知错误')}"
        logger.warning(f"[Publisher] {publish_status}")

    logger.info("[Publisher] Node completed")
    return {"publish_status": publish_status}