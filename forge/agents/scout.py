"""Scout node - multi-platform content scraper."""

import logging
import urllib.parse
from forge.graph.state import GraphState
from forge.tools.zhihu_scraper_persistent import ZhihuScraper
from forge.tools.wechat_scraper import WechatScraper

logger = logging.getLogger(__name__)


def detect_platform(url: str) -> str:
    """Auto-detect platform from URL."""
    if "zhihu.com" in url:
        return "zhihu"
    elif "weixin.qq.com" in url or "weixin.sogou.com" in url or "mp.weixin" in url:
        return "wechat"
    return ""


def extract_keyword_from_sogou_url(url: str) -> str:
    """从搜狗搜索URL中提取关键词。"""
    try:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        if 'query' in params:
            return params['query'][0]
    except:
        pass
    return ""


async def scout_node(state: GraphState) -> dict:
    """Scrape content from Zhihu, WeChat, or use manually input content."""
    topic = state.get("topic", "")
    source_platform = state.get("source_platform", "")
    raw_content = state.get("raw_content")

    logger.info(f"[Scout] Starting scrape for: {topic}")
    logger.info(f"[Scout] Specified platform: {source_platform}")

    # Manual input mode: use existing raw_content directly
    if source_platform == "manual":
        if raw_content and isinstance(raw_content, dict) and raw_content.get("title"):
            logger.info(f"[Scout] Manual input mode: title='{raw_content.get('title', '')[:30]}...'")
            logger.info(f"[Scout] Content length: {len(raw_content.get('text', ''))}")
            logger.info("[Scout] Node completed")
            return {"raw_content": raw_content, "source_platform": "manual"}
        else:
            raise ValueError("手动输入模式需要提供 raw_content")

    # If raw_content already exists (e.g., from scrape_user_posts), use it directly
    if raw_content and isinstance(raw_content, dict) and raw_content.get("title"):
        logger.info(f"[Scout] Using existing raw_content: title='{raw_content.get('title', '')[:30]}...'")
        logger.info("[Scout] Node completed (skipped re-scraping)")
        return {"raw_content": raw_content, "source_platform": source_platform or "zhihu"}

    # Auto-detect platform from URL
    if topic.startswith("http"):
        detected = detect_platform(topic)
        if detected:
            source_platform = detected
            logger.info(f"[Scout] Auto-detected platform: {source_platform}")

    # Default to zhihu if no platform specified
    if not source_platform:
        source_platform = "zhihu"
        logger.info(f"[Scout] Using default platform 'zhihu'")

    if source_platform == "zhihu":
        async with ZhihuScraper() as scraper:
            if topic.startswith("http"):
                # 根据URL类型选择正确的抓取方法
                # 注意：question/xxx/answer/yyy 格式要先检查 /answer/
                if "/answer/" in topic:
                    raw_content = await scraper.scrape_answer(topic)
                elif "question" in topic:
                    raw_content = await scraper.scrape_question(topic)
                elif "zhuanlan" in topic or "/p/" in topic:
                    raw_content = await scraper.scrape_article(topic)
                else:
                    # 尝试作为文章处理
                    raw_content = await scraper.scrape_article(topic)
            else:
                raw_content = await scraper.scrape_by_topic(topic)

    elif source_platform == "wechat":
        async with WechatScraper() as scraper:
            if topic.startswith("http"):
                # 如果是搜狗链接，提取关键词重新搜索
                if "sogou.com" in topic:
                    keyword = extract_keyword_from_sogou_url(topic)
                    if keyword:
                        logger.info(f"[Scout] Extracted keyword from URL: {keyword}")
                        raw_content = await scraper.scrape_from_search(keyword, index=0)
                    else:
                        # 尝试直接抓取
                        raw_content = await scraper.scrape_article(topic)
                else:
                    # 直接是微信文章链接
                    raw_content = await scraper.scrape_article(topic)
            else:
                # 关键词搜索
                raw_content = await scraper.scrape_from_search(topic, index=0)

    else:
        raise ValueError(f"无法识别平台: {topic}。请指定 source_platform 或使用有效的 URL。")

    logger.info(f"[Scout] Scraped content: title='{raw_content.get('title', '')[:30]}...'")
    logger.info(f"[Scout] Content length: {len(raw_content.get('text', ''))}")
    logger.info("[Scout] Node completed")

    return {"raw_content": raw_content, "source_platform": source_platform}