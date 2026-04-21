"""Xiaohongshu scraper using Playwright with stealth mode."""

import json
import logging
import os
from pathlib import Path
from playwright.async_api import async_playwright, Browser, Page, BrowserContext
from playwright_stealth import Stealth

from forge.config import XHS_BASE_URL, PLAYWRIGHT_TIMEOUT, COOKIES_FILE

logger = logging.getLogger(__name__)


class XhsScraper:
    """Async Xiaohongshu scraper with browser automation and cookie persistence."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.playwright = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def __aenter__(self):
        logger.info("[XhsScraper] Starting browser")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
            ]
        )

        # Create context with realistic settings
        self.context = await self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='zh-CN',
        )

        # Apply stealth mode
        await Stealth().apply_stealth_async(self.context)
        logger.info("[XhsScraper] Stealth mode applied")

        # Load saved cookies
        if os.path.exists(COOKIES_FILE):
            try:
                with open(COOKIES_FILE, "r") as f:
                    cookies = json.load(f)
                    await self.context.add_cookies(cookies)
                    logger.info(f"[XhsScraper] Loaded {len(cookies)} saved cookies")
            except Exception as e:
                logger.warning(f"[XhsScraper] Failed to load cookies: {e}")

        self.page = await self.context.new_page()
        self.page.set_default_timeout(PLAYWRIGHT_TIMEOUT)
        return self

    async def __aexit__(self, *args):
        logger.info("[XhsScraper] Closing browser")
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
            # Filter only xiaohongshu cookies
            xhs_cookies = [c for c in cookies if "xiaohongshu" in c.get("domain", "")]

            if xhs_cookies:
                os.makedirs(os.path.dirname(COOKIES_FILE), exist_ok=True)
                with open(COOKIES_FILE, "w") as f:
                    json.dump(xhs_cookies, f)
                logger.info(f"[XhsScraper] Saved {len(xhs_cookies)} cookies")
        except Exception as e:
            logger.warning(f"[XhsScraper] Failed to save cookies: {e}")

    async def scrape_post(self, url: str) -> dict:
        """Scrape a specific Xiaohongshu post by URL.

        Args:
            url: Full URL to the post.

        Returns:
            dict with title, text, images, likes, comments, source_url.
        """
        logger.info(f"[XhsScraper] Scraping post: {url}")
        await self.page.goto(url)

        # Wait for page to load
        await self.page.wait_for_load_state("networkidle")

        # Check if login is required
        try:
            login_prompt = await self.page.locator(".login-container, .login-modal").count()
            if login_prompt > 0:
                logger.warning("[XhsScraper] Login required - waiting for user login")
                await self.page.wait_for_url("**/home**", timeout=120000)
                logger.info("[XhsScraper] Login successful, retrying scrape")
                await self.page.goto(url)
                await self.page.wait_for_load_state("networkidle")
        except Exception:
            pass  # No login prompt detected

        # Try multiple selectors for content (XHS page structure varies)
        selectors = {
            "desc": ["#detail-desc", ".note-text", ".content", "[class*='note-content']"],
            "title": ["#detail-title", ".title", "[class*='title']"],
            "images": [".swiper-slide img", ".note-image img", "[class*='image'] img"],
        }

        # Extract text content
        text = ""
        for sel in selectors["desc"]:
            try:
                if await self.page.locator(sel).count() > 0:
                    text = await self.page.locator(sel).first.text_content() or ""
                    if text.strip():
                        break
            except Exception:
                continue

        # Extract title
        title = ""
        for sel in selectors["title"]:
            try:
                if await self.page.locator(sel).count() > 0:
                    title = await self.page.locator(sel).first.text_content() or ""
                    if title.strip():
                        break
            except Exception:
                continue

        # If no title found, use first line of text
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
            except Exception:
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
        except Exception:
            likes = 0

        result = {
            "title": title.strip()[:100],
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

        # Wait for page to load
        await self.page.wait_for_load_state("networkidle")

        # Check if login is required
        try:
            login_prompt = await self.page.locator(".login-container, .login-modal").count()
            if login_prompt > 0:
                logger.warning("[XhsScraper] Login required - waiting for user login")
                await self.page.wait_for_url("**/home**", timeout=120000)
                logger.info("[XhsScraper] Login successful, retrying search")
                await self.page.goto(search_url)
                await self.page.wait_for_load_state("networkidle")
        except Exception:
            pass

        # Wait for search results to load
        await self.page.wait_for_timeout(3000)

        # Debug: take screenshot and log page structure
        try:
            await self.page.screenshot(path="/tmp/forge_videos/search_debug.png")
            logger.info("[XhsScraper] Saved debug screenshot to /tmp/forge_videos/search_debug.png")
        except Exception:
            pass

        # Try to find any clickable search results
        # Strategy 1: Look for links to explore pages
        explore_links = await self.page.locator("a[href*='/explore/']").all()
        logger.info(f"[XhsScraper] Found {len(explore_links)} explore links on page")

        if explore_links:
            # Click the first explore link (skip login/signup links)
            for link in explore_links:
                href = await link.get_attribute("href")
                if href and "/explore/" in href and "login" not in href:
                    logger.info(f"[XhsScraper] Clicking explore link: {href[:80]}...")
                    await link.click()
                    await self.page.wait_for_load_state("networkidle")
                    logger.info(f"[XhsScraper] Navigated to: {self.page.url}")
                    return await self.scrape_post(self.page.url)

        # Strategy 2: Look for note cards/sections
        card_selectors = [
            "section a",
            "[class*='note'] a",
            "[class*='card'] a",
            "[class*='item'] a",
        ]

        for sel in card_selectors:
            try:
                count = await self.page.locator(sel).count()
                if count > 0:
                    logger.info(f"[XhsScraper] Found {count} results with selector: {sel}")
                    first = self.page.locator(sel).first
                    href = await first.get_attribute("href")
                    logger.info(f"[XhsScraper] First result href: {href}")
                    if href:
                        await first.click()
                        await self.page.wait_for_load_state("networkidle")
                        logger.info(f"[XhsScraper] Navigated to: {self.page.url}")
                        return await self.scrape_post(self.page.url)
            except Exception as e:
                logger.debug(f"[XhsScraper] Selector {sel} failed: {e}")
                continue

        logger.warning("[XhsScraper] No search results found, returning empty content")
        return {
            "title": f"搜索: {topic}",
            "text": f"未找到关键词 '{topic}' 的相关内容",
            "images": [],
            "likes": 0,
            "comments": 0,
            "source_url": search_url,
        }

    async def scrape_user_posts(self, user_id: str, max_posts: int = 5) -> list[dict]:
        """Scrape recent posts from a specific user/blogger.

        Args:
            user_id: Xiaohongshu user ID (from URL like /user/profile/xxx)
            max_posts: Maximum number of posts to scrape (default 5).

        Returns:
            List of scraped post contents.
        """
        logger.info(f"[XhsScraper] Scraping user: {user_id}")

        # Handle both full URL and just user ID
        if user_id.startswith("http"):
            user_url = user_id
        else:
            user_url = f"{XHS_BASE_URL}/user/profile/{user_id}"

        try:
            # Use domcontentloaded instead of networkidle (more reliable)
            await self.page.goto(user_url, wait_until="domcontentloaded", timeout=60000)
            logger.info(f"[XhsScraper] Page loaded: {user_url}")
        except Exception as e:
            logger.warning(f"[XhsScraper] Page load warning: {e}")

        # Wait for page to stabilize and handle login popup
        await self.page.wait_for_timeout(5000)

        # Try to close any login popup/alert
        try:
            # Check for reds-alert (XHS login popup)
            alert_close = await self.page.locator(".reds-alert .close-icon, .reds-button-new.text").count()
            if alert_close > 0:
                logger.info("[XhsScraper] Found login popup, attempting to close")
                # Click close button or dismiss button
                close_btns = await self.page.locator(".reds-alert .close-icon, .reds-button-new.text").all()
                for btn in close_btns:
                    try:
                        text = await btn.text_content()
                        if text and ("关闭" in text or "取消" in text or "暂不" in text):
                            await btn.click()
                            logger.info("[XhsScraper] Closed login popup")
                            await self.page.wait_for_timeout(1000)
                            break
                    except:
                        pass
        except Exception as e:
            logger.debug(f"[XhsScraper] No popup to close: {e}")

        # Debug screenshot
        try:
            await self.page.screenshot(path="/tmp/forge_videos/user_page_debug.png")
            logger.info("[XhsScraper] Saved debug screenshot to /tmp/forge_videos/user_page_debug.png")
        except Exception:
            pass

        # Wait for content to load (scroll to trigger lazy loading)
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await self.page.wait_for_timeout(2000)

        # Find post links on user profile - ONLY from the user's notes section
        post_links = []

        # More specific selectors for user's own posts (not recommendations)
        # XHS user profile typically has posts in a specific container
        link_selectors = [
            # User's notes container (most specific)
            "[class*='user-notes'] a[href*='/explore/']",
            "[class*='notesContainer'] a[href*='/explore/']",
            # Note items in user profile
            "[class*='note-item'] a[href*='/explore/']",
            # Section containing user posts
            "section[class*='notes'] a[href*='/explore/']",
            # Fallback: section links (but exclude sidebar/recommendations)
            "main a[href*='/explore/']",
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
                            logger.debug(f"[XhsScraper] Found post: {href}")
                            if len(post_links) >= max_posts:
                                break
                if len(post_links) >= max_posts:
                    break
            except Exception as e:
                logger.debug(f"[XhsScraper] Selector '{selector}' failed: {e}")
                continue

        # If no posts found with specific selectors, log warning
        if not post_links:
            logger.warning(f"[XhsScraper] No posts found for user {user_id}. Page may require login or user has no posts.")

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
        """Extract content from current page (internal helper)."""
        # Try multiple selectors for content (XHS page structure varies)
        selectors = {
            "desc": ["#detail-desc", ".note-text", ".content", "[class*='note-content']"],
            "title": ["#detail-title", ".title", "[class*='title']"],
            "images": [".swiper-slide img", ".note-image img", "[class*='image'] img"],
        }

        # Extract text content
        text = ""
        for sel in selectors["desc"]:
            try:
                if await self.page.locator(sel).count() > 0:
                    text = await self.page.locator(sel).first.text_content() or ""
                    if text.strip():
                        break
            except Exception:
                continue

        # Extract title
        title = ""
        for sel in selectors["title"]:
            try:
                if await self.page.locator(sel).count() > 0:
                    title = await self.page.locator(sel).first.text_content() or ""
                    if title.strip():
                        break
            except Exception:
                continue

        # If no title found, use first line of text
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
            except Exception:
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
        except Exception:
            likes = 0

        return {
            "title": title.strip()[:100],
            "text": text.strip(),
            "images": images,
            "likes": likes,
            "comments": 0,
            "source_url": url,
        }