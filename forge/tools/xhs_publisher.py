"""Xiaohongshu video publisher using Playwright."""

import json
import logging
import os
from playwright.async_api import async_playwright, Browser, Page, BrowserContext

from forge.config import XHS_BASE_URL, PLAYWRIGHT_TIMEOUT, COOKIES_FILE

logger = logging.getLogger(__name__)


class XhsPublisher:
    """Async Xiaohongshu video publisher with cookie persistence."""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self.playwright = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def __aenter__(self):
        logger.info("[XhsPublisher] Starting browser")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)

        # Create context and load cookies if exist
        self.context = await self.browser.new_context()

        # Load saved cookies
        if os.path.exists(COOKIES_FILE):
            try:
                with open(COOKIES_FILE, "r") as f:
                    cookies = json.load(f)
                    await self.context.add_cookies(cookies)
                    logger.info(f"[XhsPublisher] Loaded {len(cookies)} saved cookies")
            except Exception as e:
                logger.warning(f"[XhsPublisher] Failed to load cookies: {e}")

        self.page = await self.context.new_page()
        self.page.set_default_timeout(PLAYWRIGHT_TIMEOUT)
        return self

    async def __aexit__(self, *args):
        logger.info("[XhsPublisher] Closing browser")
        try:
            # Save cookies before closing
            await self._save_cookies()
            if self.browser is not None:
                await self.browser.close()
        except Exception as e:
            logger.debug(f"[XhsPublisher] Error closing browser: {e}")
        finally:
            if self.playwright is not None:
                try:
                    await self.playwright.stop()
                except Exception as e:
                    logger.debug(f"[XhsPublisher] Error stopping playwright: {e}")

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
                logger.info(f"[XhsPublisher] Saved {len(xhs_cookies)} cookies")
        except Exception as e:
            logger.warning(f"[XhsPublisher] Failed to save cookies: {e}")

    async def login(self) -> bool:
        """Wait for user to login via QR code."""
        logger.info("[XhsPublisher] Opening login page")
        await self.page.goto(f"{XHS_BASE_URL}/login")

        try:
            await self.page.wait_for_url("**/home**", timeout=120000)
            logger.info("[XhsPublisher] Login successful")
            return True
        except Exception as e:
            logger.warning(f"[XhsPublisher] Login timeout: {e}")
            return False

    async def publish_video(self, video_path: str, title: str, description: str) -> dict:
        """Publish video to Xiaohongshu."""
        logger.info(f"[XhsPublisher] Publishing video: {video_path}")

        await self.page.goto(f"{XHS_BASE_URL}/creator/publish")

        try:
            await self.page.locator("input[type='file']").set_input_files(video_path)
            await self.page.wait_for_load_state("networkidle")

            await self.page.locator(".title-input, [placeholder*='标题']").fill(title[:50])
            await self.page.locator(".desc-input, [placeholder*='描述']").fill(description)
            await self.page.locator(".publish-btn, button:has-text('发布')").click()
            await self.page.wait_for_load_state("networkidle")
        except Exception as e:
            logger.error(f"[XhsPublisher] Publish failed: {e}")
            return {"success": False, "error": str(e)}

        result = {"success": True, "post_url": self.page.url}
        logger.info(f"[XhsPublisher] Published: {self.page.url}")
        return result