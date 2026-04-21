"""Xiaohongshu scraper with persistent browser context."""

import json
import logging
import os
from pathlib import Path
from playwright.async_api import async_playwright, BrowserContext
from playwright_stealth import Stealth

from forge.config import XHS_BASE_URL, PLAYWRIGHT_TIMEOUT

# Persistent browser data directory
BROWSER_DATA_DIR = os.path.expanduser("~/.forge/browser_data/xhs")
Path(BROWSER_DATA_DIR).mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)


class XhsScraper:
    """Async Xiaohongshu scraper with persistent browser context."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.playwright = None
        self.context: BrowserContext = None

    async def __aenter__(self):
        logger.info("[XhsScraper] Starting browser with persistent context")
        self.playwright = await async_playwright().start()

        # Use persistent context - this saves all browser data including cookies
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=BROWSER_DATA_DIR,
            headless=self.headless,
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='zh-CN',
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
            ]
        )

        # Apply stealth mode
        await Stealth().apply_stealth_async(self.context)
        logger.info("[XhsScraper] Stealth mode applied")

        # Get or create page
        if self.context.pages:
            self.page = self.context.pages[0]
        else:
            self.page = await self.context.new_page()

        self.page.set_default_timeout(PLAYWRIGHT_TIMEOUT)
        return self

    async def __aexit__(self, *args):
        logger.info("[XhsScraper] Closing browser")
        try:
            if self.context:
                await self.context.close()
        finally:
            if self.playwright:
                await self.playwright.stop()

    async def scrape_post(self, url: str) -> dict:
        """Scrape a specific Xiaohongshu post by URL."""
        logger.info(f"[XhsScraper] Scraping post: {url}")
        await self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await self.page.wait_for_timeout(3000)

        # Check if login is required
        await self._handle_login_popup()

        return await self._extract_post_content(url)

    async def _handle_login_popup(self):
        """Handle login popup if present."""
        try:
            # Wait a bit for any popup to appear
            await self.page.wait_for_timeout(2000)

            # Check for various popup types
            popup_selectors = [
                ".reds-alert",
                ".login-container",
                ".login-modal",
                "[class*='login-modal']",
            ]

            for selector in popup_selectors:
                if await self.page.locator(selector).count() > 0:
                    logger.info(f"[XhsScraper] Found popup: {selector}")
                    # Try to close it
                    close_selectors = [
                        ".reds-alert .close-icon",
                        ".reds-button-new.text",
                        "[class*='close']",
                    ]
                    for close_sel in close_selectors:
                        btns = await self.page.locator(close_sel).all()
                        for btn in btns:
                            try:
                                text = await btn.text_content()
                                if text and ("我知道了" in text or "关闭" in text or "暂不" in text or "取消" in text):
                                    await btn.click()
                                    logger.info(f"[XhsScraper] Closed popup: {text}")
                                    await self.page.wait_for_timeout(1000)
                                    return
                            except:
                                pass
        except Exception as e:
            logger.debug(f"[XhsScraper] Popup handling: {e}")

    async def scrape_by_topic(self, topic: str) -> dict:
        """Search and scrape a post by topic keyword."""
        logger.info(f"[XhsScraper] Searching for topic: {topic}")
        search_url = f"{XHS_BASE_URL}/search?keyword={topic}"
        await self.page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        await self.page.wait_for_timeout(3000)

        await self._handle_login_popup()

        # Scroll to trigger lazy loading
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await self.page.wait_for_timeout(2000)

        # Find and click first post
        explore_links = await self.page.locator("a[href*='/explore/']").all()
        logger.info(f"[XhsScraper] Found {len(explore_links)} explore links")

        if explore_links:
            href = await explore_links[0].get_attribute("href")
            if href:
                if href.startswith("/"):
                    href = f"{XHS_BASE_URL}{href}"
                await self.page.goto(href, wait_until="domcontentloaded", timeout=60000)
                await self.page.wait_for_timeout(2000)
                return await self._extract_post_content(href)

        return {"title": f"搜索: {topic}", "text": f"未找到关键词 '{topic}' 的相关内容", "images": [], "likes": 0, "source_url": search_url}

    async def scrape_user_posts(self, user_id: str, max_posts: int = 5) -> list[dict]:
        """Scrape recent posts from a specific user/blogger."""
        logger.info(f"[XhsScraper] Scraping user: {user_id}")

        if user_id.startswith("http"):
            user_url = user_id
        else:
            user_url = f"{XHS_BASE_URL}/user/profile/{user_id}"

        await self.page.goto(user_url, wait_until="domcontentloaded", timeout=60000)
        await self.page.wait_for_timeout(5000)

        await self._handle_login_popup()

        # Scroll to trigger lazy loading
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await self.page.wait_for_timeout(3000)

        # Find post links - specific to user's notes section
        post_links = []
        link_selectors = [
            "[class*='user-notes'] a[href*='/explore/']",
            "[class*='notesContainer'] a[href*='/explore/']",
            "[class*='note-item'] a[href*='/explore/']",
            "main a[href*='/explore/']",
            "a[href*='/explore/']",
        ]

        for selector in link_selectors:
            try:
                links = await self.page.locator(selector).all()
                logger.info(f"[XhsScraper] Selector '{selector}': {len(links)} links")
                for link in links:
                    href = await link.get_attribute("href")
                    if href and "/explore/" in href and "login" not in href:
                        if href.startswith("/"):
                            href = f"{XHS_BASE_URL}{href}"
                        if href not in post_links:
                            post_links.append(href)
                            if len(post_links) >= max_posts:
                                break
                if len(post_links) >= max_posts:
                    break
            except Exception as e:
                logger.debug(f"[XhsScraper] Selector '{selector}' failed: {e}")

        logger.info(f"[XhsScraper] Collected {len(post_links)} posts for user {user_id}")

        # Scrape each post
        results = []
        for i, post_url in enumerate(post_links):
            try:
                logger.info(f"[XhsScraper] Scraping post {i+1}/{len(post_links)}: {post_url}")
                await self.page.goto(post_url, wait_until="domcontentloaded", timeout=60000)
                await self.page.wait_for_timeout(2000)
                content = await self._extract_post_content(post_url)
                results.append(content)
                logger.info(f"[XhsScraper] Scraped: {content.get('title', 'N/A')[:30]}...")
            except Exception as e:
                logger.warning(f"[XhsScraper] Failed to scrape post {post_url}: {e}")

        logger.info(f"[XhsScraper] Scraped {len(results)} posts from user {user_id}")
        return results

    async def _extract_post_content(self, url: str) -> dict:
        """Extract content from current page."""
        selectors = {
            "desc": ["#detail-desc", ".note-text", ".content", "[class*='note-content']"],
            "title": ["#detail-title", ".title", "[class*='title']"],
            "images": [".swiper-slide img", ".note-image img", "[class*='image'] img"],
        }

        # Extract text
        text = ""
        for sel in selectors["desc"]:
            try:
                if await self.page.locator(sel).count() > 0:
                    text = await self.page.locator(sel).first.text_content() or ""
                    if text.strip():
                        break
            except:
                continue

        # Extract title
        title = ""
        for sel in selectors["title"]:
            try:
                if await self.page.locator(sel).count() > 0:
                    title = await self.page.locator(sel).first.text_content() or ""
                    if title.strip():
                        break
            except:
                continue

        if not title and text:
            title = text.strip().split("\n")[0][:50]

        # Extract images
        images = []
        for sel in selectors["images"]:
            try:
                imgs = await self.page.locator(sel).evaluate_all(
                    "imgs => imgs.map(i => i.src).filter(s => s && s.startsWith('http'))"
                )
                if imgs:
                    images = imgs
                    break
            except:
                continue

        # Extract likes
        likes = 0
        try:
            likes_text = await self.page.locator(".like-wrapper .count, [class*='like'] .count").text_content() or "0"
            likes_text = likes_text.replace("+", "").strip()
            if "万" in likes_text:
                likes = int(float(likes_text.replace("万", "")) * 10000)
            elif likes_text.isdigit():
                likes = int(likes_text)
        except:
            likes = 0

        return {
            "title": title.strip()[:100],
            "text": text.strip(),
            "images": images,
            "likes": likes,
            "comments": 0,
            "source_url": url,
        }

    async def search_articles(self, topic: str, max_results: int = 5) -> list:
        """Search Xiaohongshu by topic and return a list of posts for user selection.

        Args:
            topic: Search keyword.
            max_results: Maximum number of results to return.

        Returns:
            List of dicts with 'title', 'summary', 'source_url'.
        """
        logger.info(f"[XhsScraper] Searching posts for: {topic}")
        search_url = f"{XHS_BASE_URL}/search?keyword={topic}"
        await self.page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        await self.page.wait_for_timeout(3000)

        await self._handle_login_popup()

        # Scroll to trigger lazy loading
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await self.page.wait_for_timeout(2000)

        articles = []
        try:
            # Find post links
            link_selectors = [
                "a[href*='/explore/']",
                "[class*='note-item'] a",
                "[class*='search-result'] a",
            ]

            found_urls = set()
            for selector in link_selectors:
                if len(articles) >= max_results:
                    break

                try:
                    links = await self.page.locator(selector).all()
                    logger.info(f"[XhsScraper] Found {len(links)} links with selector: {selector}")

                    for link in links:
                        if len(articles) >= max_results:
                            break

                        href = await link.get_attribute("href")
                        if not href or "/explore/" not in href:
                            continue
                        if "login" in href:
                            continue

                        # Build full URL
                        if href.startswith("/"):
                            href = f"{XHS_BASE_URL}{href}"

                        # Avoid duplicates
                        if href in found_urls:
                            continue
                        found_urls.add(href)

                        # Try to get title from link or parent
                        title = ""
                        try:
                            # Try to get title from various elements
                            parent = link.locator("xpath=..")
                            title_elem = await parent.locator("[class*='title'], .title, h3, h4").first.text_content()
                            if title_elem:
                                title = title_elem.strip()[:100]
                        except:
                            pass

                        if not title:
                            try:
                                title = (await link.text_content() or "").strip()[:100]
                            except:
                                title = "无标题"

                        # Get summary from nearby text
                        summary = ""
                        try:
                            parent = link.locator("xpath=..")
                            text_elem = await parent.locator("[class*='desc'], [class*='content'], [class*='text']").first.text_content()
                            if text_elem:
                                summary = text_elem.strip()[:200]
                        except:
                            pass

                        articles.append({
                            "title": title if title else "无标题",
                            "summary": summary,
                            "source_url": href,
                            "type": "post",
                            "likes": 0,
                        })
                        logger.info(f"[XhsScraper] Found post: {title[:30] if title else 'N/A'}...")

                except Exception as e:
                    logger.debug(f"[XhsScraper] Error with selector {selector}: {e}")
                    continue

        except Exception as e:
            logger.error(f"[XhsScraper] Error searching posts: {e}")

        logger.info(f"[XhsScraper] Returning {len(articles)} posts")
        return articles