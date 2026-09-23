"""按文章来源复用现有采集器补齐正文。"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


async def fetch_article_content(url: str) -> dict[str, str]:
    if not url:
        return {"title": "", "text": ""}
    domain = urlparse(url).netloc.lower()
    if "zhihu.com" in domain:
        from forge.scraping.zhihu_executor import get_zhihu_pool

        try:
            async with get_zhihu_pool().lease() as scraper:
                result = await scraper.scrape_by_url(url)
                return {
                    "title": result.get("title", ""),
                    "text": result.get("text", ""),
                }
        except Exception:
            logger.exception("知乎文章抓取失败 url=%s", url)
            return {"title": "", "text": ""}
    if "mp.weixin.qq.com" in domain or "weixin.qq.com" in domain:
        from forge.tools.wechat_scraper import WechatScraper

        try:
            scraper = WechatScraper(headless=True)
            async with scraper:
                result = await scraper.scrape_article(url)
                return {
                    "title": result.get("title", ""),
                    "text": result.get("text", ""),
                }
        except Exception:
            logger.exception("微信文章抓取失败 url=%s", url)
            return {"title": "", "text": ""}
    logger.warning("不支持的文章来源 url=%s", url)
    return {"title": "", "text": ""}
