"""Xiaohongshu scraper using Playwright."""

import logging
from playwright.async_api import async_playwright, Browser, Page

from forge.config import XHS_BASE_URL, PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)


class XhsScraper:
    """Async Xiaohongshu scraper with browser automation."""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self.playwright = None
        self.browser: Browser = None
        self.page: Page = None

    async def __aenter__(self):
        logger.info("[XhsScraper] Starting browser")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.page = await self.browser.new_page()
        self.page.set_default_timeout(PLAYWRIGHT_TIMEOUT)
        return self

    async def __aexit__(self, *args):
        logger.info("[XhsScraper] Closing browser")
        await self.browser.close()
        await self.playwright.stop()

    async def scrape_post(self, url: str) -> dict:
        """Scrape a specific Xiaohongshu post by URL.

        Args:
            url: Full URL to the post.

        Returns:
            dict with title, text, images, likes, comments, source_url.
        """
        logger.info(f"[XhsScraper] Scraping post: {url}")
        await self.page.goto(url)

        # Wait for content to load
        await self.page.wait_for_selector("#detail-desc", timeout=PLAYWRIGHT_TIMEOUT)

        # Extract content - selectors may need adjustment based on actual page
        try:
            title = await self.page.locator("#detail-title").text_content() or ""
        except:
            title = ""

        try:
            text = await self.page.locator("#detail-desc").text_content() or ""
        except:
            text = ""

        try:
            images = await self.page.locator(".swiper-slide img").evaluate_all(
                "imgs => imgs.map(i => i.src).filter(s => s)"
            )
        except:
            images = []

        try:
            likes_text = await self.page.locator(".like-wrapper .count").text_content() or "0"
            likes = int(likes_text.replace("+", "").replace("万", "0000"))
        except:
            likes = 0

        result = {
            "title": title.strip(),
            "text": text.strip(),
            "images": images,
            "likes": likes,
            "comments": 0,
            "source_url": url,
        }
        logger.info(f"[XhsScraper] Scraped: title='{title[:30]}...', images={len(images)}")
        return result

    async def scrape_by_topic(self, topic: str) -> dict:
        """Search and scrape a post by topic keyword.

        Args:
            topic: Search keyword.

        Returns:
            Scraped content from first search result.
        """
        logger.info(f"[XhsScraper] Searching for topic: {topic}")
        search_url = f"{XHS_BASE_URL}/search?keyword={topic}"
        await self.page.goto(search_url)

        # Click first result
        await self.page.wait_for_selector(".search-result", timeout=PLAYWRIGHT_TIMEOUT)
        await self.page.locator(".search-result").first.click()

        # Wait for navigation
        await self.page.wait_for_load_state("networkidle")

        return await self.scrape_post(self.page.url)