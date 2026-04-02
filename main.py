"""Main entry point for the Forge workflow.

Run the complete LangGraph pipeline with logging output.
"""

import asyncio
import logging
import sys

from forge.graph import workflow, create_initial_state


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging for the workflow."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(name)-12s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


async def run_workflow(topic: str, target_platform: str = "xhs_video") -> dict:
    """Run the complete Forge workflow.

    Args:
        topic: Input topic or Xiaohongshu link.
        target_platform: Target platform for publishing (default: xhs_video).

    Returns:
        Final state after workflow completion.
    """
    logger = logging.getLogger("Forge")
    logger.info("=" * 60)
    logger.info(f"Starting Forge workflow with topic: {topic}")
    logger.info(f"Target platform: {target_platform}")
    logger.info("=" * 60)

    # Create initial state
    initial_state = create_initial_state(topic)
    initial_state["target_platform"] = target_platform
    logger.info(f"Initial state: {list(initial_state.keys())}")

    # Invoke the workflow asynchronously
    result = await workflow.ainvoke(initial_state)

    # Print final results
    logger.info("=" * 60)
    logger.info("WORKFLOW COMPLETED")
    logger.info("=" * 60)

    logger.info("Final State Summary:")
    logger.info(f"  - Topic: {result.get('topic')}")
    logger.info(f"  - Revision Count: {result.get('revision_count')}")
    logger.info(f"  - Final Script: {result.get('final_script', 'N/A')[:100]}...")
    logger.info(f"  - Video Path: {result.get('video_path', 'N/A')}")
    logger.info(f"  - Publish Status: {result.get('publish_status', 'N/A')}")

    return result


def main() -> None:
    """Main entry point."""
    setup_logging()

    # Test topic
    test_topic = "如何提高工作效率"
    asyncio.run(run_workflow(test_topic))


if __name__ == "__main__":
    main()