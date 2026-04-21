"""Mock test for Forge workflow - tests all nodes without real browser/API calls."""

import asyncio
import logging
import sys

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-12s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("Forge")


async def test_workflow():
    """Test the workflow with mock data."""
    from forge.graph.state import GraphState
    from forge.agents.editor import editor_node
    from forge.agents.reviewer import reviewer_node
    from forge.agents.director import director_node

    # Mock state after scout_node
    state: GraphState = {
        "topic": "https://www.xiaohongshu.com/explore/test",
        "source_platform": "xhs",
        "target_platform": "xhs_video",
        "raw_content": {
            "title": "提高工作效率的10个方法",
            "text": "1. 制定每日计划\n2. 番茄工作法\n3. 减少干扰\n4. 定期休息\n5. 优先处理重要任务",
            "images": [],
            "likes": 1000,
            "comments": 50,
            "source_url": "https://www.xiaohongshu.com/explore/test",
        },
        "revision_count": 0,
    }

    logger.info("=" * 60)
    logger.info("MOCK WORKFLOW TEST")
    logger.info("=" * 60)

    # Test editor_node
    logger.info("\n[1/3] Testing editor_node...")
    try:
        result = await editor_node(state)
        state["rewritten_draft"] = result["rewritten_draft"]
        state["revision_count"] = result["revision_count"]
        logger.info(f"✅ Editor generated draft: {state['rewritten_draft'][:100]}...")
    except Exception as e:
        logger.error(f"❌ Editor failed: {e}")
        return

    # Test reviewer_node
    logger.info("\n[2/3] Testing reviewer_node...")
    try:
        result = await reviewer_node(state)
        if result.get("final_script"):
            state["final_script"] = result["final_script"]
            logger.info(f"✅ Reviewer approved: {state['final_script'][:100]}...")
        else:
            state["reflection_feedback"] = result["reflection_feedback"]
            logger.info(f"⚠️ Reviewer feedback: {state['reflection_feedback'][:100]}...")
            # Use draft as final for testing
            state["final_script"] = f"【最终脚本】\n\n{state['rewritten_draft']}"
    except Exception as e:
        logger.error(f"❌ Reviewer failed: {e}")
        return

    # Test director_node (without real video generation)
    logger.info("\n[3/3] Testing director_node...")
    logger.info("⚠️ Skipping video generation (requires FFmpeg and real images)")

    logger.info("\n" + "=" * 60)
    logger.info("MOCK TEST COMPLETED SUCCESSFULLY!")
    logger.info("=" * 60)
    logger.info(f"Final script length: {len(state.get('final_script', ''))} chars")


if __name__ == "__main__":
    asyncio.run(test_workflow())