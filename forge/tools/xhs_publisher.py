"""Xiaohongshu video publisher using Playwright."""

import logging
from playwright.async_api import async_playwright, Browser, Page

from forge.config import XHS_BASE_URL, PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)


class XhsPublisher:
    """Async Xiaohongshu video publisher."""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self.playwright = None
        self.browser: Browser = None
        self.page: Page = None

    async def __aenter__(self):
        logger.info("[XhsPublisher] Starting browser")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.page = await self.browser.new_page()
        self.page.set_default_timeout(PLAYWRIGHT_TIMEOUT)
        return self

    async def __aexit__(self, *args):
        logger.info("[XhsPublisher] Closing browser")
        try:
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