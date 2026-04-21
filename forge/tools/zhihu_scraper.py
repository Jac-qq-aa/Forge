"""Zhihu scraper using Playwright with stealth mode."""

import logging
import json
import os
from playwright.async_api import async_playwright, Browser, Page, BrowserContext
from playwright_stealth import Stealth

from forge.config import ZHIHU_BASE_URL, PLAYWRIGHT_TIMEOUT

ZHIHU_COOKIES_FILE = "/tmp/forge_cookies/zhihu_cookies.json"

logger = logging.getLogger(__name__)


class ZhihuScraper:
    """Async Zhihu scraper with browser automation and stealth mode."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.playwright = None
        self.browser: Browser = None
        self.context: BrowserContext = None
        self.page: Page = None

    async def __aenter__(self):
        logger.info("[ZhihuScraper] Starting browser")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
            ]
        )
        self.context = await self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='zh-CN',
        )

        # Apply stealth mode
        await Stealth().apply_stealth_async(self.context)
        logger.info("[ZhihuScraper] Stealth mode applied")

        # Load saved cookies
        if os.path.exists(ZHIHU_COOKIES_FILE):
            try:
                with open(ZHIHU_COOKIES_FILE, "r") as f:
                    cookies = json.load(f)
                    await self.context.add_cookies(cookies)
                    logger.info(f"[ZhihuScraper] Loaded {len(cookies)} saved cookies")
            except Exception as e:
                logger.warning(f"[ZhihuScraper] Failed to load cookies: {e}")

        self.page = await self.context.new_page()
        self.page.set_default_timeout(PLAYWRIGHT_TIMEOUT)
        return self

    async def __aexit__(self, *args):
        logger.info("[ZhihuScraper] Closing browser")
        try:
            # Save cookies before closing
            await self._save_cookies()
            if self.browser:
                await self.browser.close()
        finally:
            if self.playwright:
                await self.playwright.stop()

    async def _save_cookies(self):
        """Save cookies to file for login persistence."""
        try:
            cookies = await self.context.cookies()
            zhihu_cookies = [c for c in cookies if "zhihu" in c.get("domain", "")]
            if zhihu_cookies:
                os.makedirs(os.path.dirname(ZHIHU_COOKIES_FILE), exist_ok=True)
                with open(ZHIHU_COOKIES_FILE, "w") as f:
                    json.dump(zhihu_cookies, f)
                logger.info(f"[ZhihuScraper] Saved {len(zhihu_cookies)} cookies")
        except Exception as e:
            logger.warning(f"[ZhihuScraper] Failed to save cookies: {e}")

    async def scrape_article(self, url: str) -> dict:
        """Scrape a specific Zhihu article by URL.

        Args:
            url: Full URL to the Zhihu article.

        Returns:
            dict with title, text, likes, source_url.
        """
        logger.info(f"[ZhihuScraper] Scraping article: {url}")
        await self.page.goto(url)

        # Wait for content to load
        await self.page.wait_for_selector(".Post-Title", timeout=PLAYWRIGHT_TIMEOUT)

        # Extract content - selectors may need adjustment based on actual page
        try:
            title = await self.page.locator(".Post-Title").text_content() or ""
        except Exception as e:
            logger.debug(f"[ZhihuScraper] Failed to extract article title: {e}")
            title = ""

        try:
            text = await self.page.locator(".Post-RichText").text_content() or ""
        except Exception as e:
            logger.debug(f"[ZhihuScraper] Failed to extract article text: {e}")
            text = ""

        try:
            likes_text = await self.page.locator(".VoteButton--up").text_content() or "0"
            # Handle "赞同" prefix (e.g., "赞同 123" -> 123)
            likes_text = likes_text.replace("赞同", "").strip()
            likes_text = likes_text.replace("+", "").strip()
            if "万" in likes_text:
                likes = int(float(likes_text.replace("万", "")) * 10000)
            else:
                likes = int(likes_text) if likes_text else 0
        except Exception as e:
            logger.debug(f"[ZhihuScraper] Failed to extract article likes: {e}")
            likes = 0

        result = {
            "title": title.strip(),
            "text": text.strip(),
            "likes": likes,
            "source_url": url,
        }
        logger.info(f"[ZhihuScraper] Scraped article: title='{title[:30]}...', likes={likes}")
        return result

    async def scrape_question(self, url: str) -> dict:
        """Scrape a Zhihu question page and get the top answer.

        Args:
            url: Full URL to the Zhihu question page.

        Returns:
            dict with title, question, answer, likes, source_url.
        """
        logger.info(f"[ZhihuScraper] Scraping question: {url}")
        await self.page.goto(url)

        # Wait for content to load
        await self.page.wait_for_selector(".QuestionHeader-title", timeout=PLAYWRIGHT_TIMEOUT)

        # Extract question title
        try:
            title = await self.page.locator(".QuestionHeader-title").text_content() or ""
        except Exception as e:
            logger.debug(f"[ZhihuScraper] Failed to extract question title: {e}")
            title = ""

        # Get top answer (first List-item)
        top_answer_item = self.page.locator(".List-item").first
        try:
            answer_text = await top_answer_item.locator(".RichContent-inner").text_content() or ""
        except Exception as e:
            logger.debug(f"[ZhihuScraper] Failed to extract answer text: {e}")
            answer_text = ""

        # Get likes from top answer
        try:
            likes_text = await top_answer_item.locator(".VoteButton--up").text_content() or "0"
            # Handle "赞同" prefix (e.g., "赞同 123" -> 123)
            likes_text = likes_text.replace("赞同", "").strip()
            likes_text = likes_text.replace("+", "").strip()
            if "万" in likes_text:
                likes = int(float(likes_text.replace("万", "")) * 10000)
            else:
                likes = int(likes_text) if likes_text else 0
        except Exception as e:
            logger.debug(f"[ZhihuScraper] Failed to extract likes: {e}")
            likes = 0

        result = {
            "title": title.strip(),
            "question": title.strip(),
            "answer": answer_text.strip(),
            "likes": likes,
            "source_url": url,
        }
        logger.info(f"[ZhihuScraper] Scraped question: title='{title[:30]}...', likes={likes}")
        return result

    async def scrape_by_topic(self, topic: str) -> dict:
        """Search and scrape a Zhihu article by topic keyword.

        Args:
            topic: Search keyword.

        Returns:
            Scraped content from first search result.
        """
        logger.info(f"[ZhihuScraper] Searching for topic: {topic}")
        search_url = f"{ZHIHU_BASE_URL}/search?type=content&q={topic}"
        await self.page.goto(search_url)

        # Wait for search results
        await self.page.wait_for_selector(".SearchResult-Card", timeout=PLAYWRIGHT_TIMEOUT)

        # Click first result
        try:
            first_result = self.page.locator(".SearchResult-Card").first
            await first_result.click()

            # Wait for navigation
            await self.page.wait_for_load_state("networkidle")

            # Determine if it's an article or question and scrape accordingly
            current_url = self.page.url
            if "zhuanlan.zhihu.com" in current_url or "/p/" in current_url:
                return await self.scrape_article(current_url)
            else:
                return await self.scrape_question(current_url)
        except Exception as e:
            logger.error(f"[ZhihuScraper] Failed to scrape search result: {e}")
            return {
                "title": "",
                "text": "",
                "likes": 0,
                "source_url": search_url,
                "error": str(e),
            }