"""Scout node - dual-platform content scraper."""

import logging
from forge.graph.state import GraphState
from forge.tools.xhs_scraper import XhsScraper
from forge.tools.zhihu_scraper import ZhihuScraper

logger = logging.getLogger(__name__)


def detect_platform(url: str) -> str:
    """Auto-detect platform from URL."""
    if "xiaohongshu.com" in url:
        return "xhs"
    elif "zhihu.com" in url:
        return "zhihu"
    return ""


async def scout_node(state: GraphState) -> dict:
    """Scrape content from Xiaohongshu or Zhihu."""
    topic = state.get("topic", "")
    source_platform = state.get("source_platform", "")

    logger.info(f"[Scout] Starting scrape for: {topic}")
    logger.info(f"[Scout] Specified platform: {source_platform}")

    # Auto-detect platform from URL
    if topic.startswith("http"):
        detected = detect_platform(topic)
        if detected:
            source_platform = detected
            logger.info(f"[Scout] Auto-detected platform: {source_platform}")

    if source_platform == "xhs":
        async with XhsScraper() as scraper:
            if topic.startswith("http"):
                raw_content = await scraper.scrape_post(topic)
            else:
                raw_content = await scraper.scrape_by_topic(topic)
    elif source_platform == "zhihu":
        async with ZhihuScraper() as scraper:
            if topic.startswith("http"):
                if "question" in topic:
                    raw_content = await scraper.scrape_question(topic)
                else:
                    raw_content = await scraper.scrape_article(topic)
            else:
                raw_content = await scraper.scrape_by_topic(topic)
    else:
        raise ValueError(f"无法识别平台: {topic}。请指定 source_platform 或使用有效的 URL。")

    logger.info(f"[Scout] Scraped content: title='{raw_content.get('title', '')[:30]}...'")
    logger.info("[Scout] Node completed")

    return {"raw_content": raw_content, "source_platform": source_platform}