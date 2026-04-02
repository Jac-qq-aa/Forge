"""Zhihu article/video publisher using Playwright."""

import logging
from playwright.async_api import async_playwright, Browser, Page

from forge.config import ZHIHU_BASE_URL, PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)


class ZhihuPublisher:
    """Async Zhihu publisher for articles and videos."""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self.playwright = None
        self.browser: Browser = None
        self.page: Page = None

    async def __aenter__(self):
        logger.info("[ZhihuPublisher] Starting browser")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.page = await self.browser.new_page()
        self.page.set_default_timeout(PLAYWRIGHT_TIMEOUT)
        return self

    async def __aexit__(self, *args):
        logger.info("[ZhihuPublisher] Closing browser")
        try:
            if self.browser is not None:
                await self.browser.close()
        except Exception as e:
            logger.debug(f"[ZhihuPublisher] Error closing browser: {e}")
        finally:
            if self.playwright is not None:
                try:
                    await self.playwright.stop()
                except Exception as e:
                    logger.debug(f"[ZhihuPublisher] Error stopping playwright: {e}")

    async def login(self) -> bool:
        """Wait for user to login."""
        logger.info("[ZhihuPublisher] Opening login page")
        await self.page.goto(f"{ZHIHU_BASE_URL}/signin")

        try:
            await self.page.wait_for_url("https://www.zhihu.com/", timeout=120000)
            logger.info("[ZhihuPublisher] Login successful")
            return True
        except Exception as e:
            logger.warning(f"[ZhihuPublisher] Login timeout: {e}")
            return False

    async def publish_article(self, title: str, content: str) -> dict:
        """Publish article to Zhihu."""
        logger.info(f"[ZhihuPublisher] Publishing article: {title[:30]}")

        await self.page.goto(f"{ZHIHU_BASE_URL}/write")

        try:
            await self.page.locator(".WriteIndex-titleInput, input[placeholder*='标题']").fill(title)
            await self.page.locator(".WriteIndex-content, .editor").fill(content)
            await self.page.locator(".WriteIndex-submitBtn, button:has-text('发布')").click()
            await self.page.wait_for_load_state("networkidle")
        except Exception as e:
            logger.error(f"[ZhihuPublisher] Article publish failed: {e}")
            return {"success": False, "error": str(e)}

        result = {"success": True, "post_url": self.page.url}
        logger.info(f"[ZhihuPublisher] Article published: {self.page.url}")
        return result

    async def publish_video(self, video_path: str, title: str, description: str) -> dict:
        """Publish video to Zhihu."""
        logger.info(f"[ZhihuPublisher] Publishing video: {video_path}")

        await self.page.goto(f"{ZHIHU_BASE_URL}/creator/publish/video")

        try:
            await self.page.locator("input[type='file']").set_input_files(video_path)
            await self.page.wait_for_load_state("networkidle")

            await self.page.locator(".VideoUpload-title, input[placeholder*='标题']").fill(title)
            await self.page.locator(".VideoUpload-desc, textarea").fill(description)
            await self.page.locator(".VideoUpload-submit, button:has-text('发布')").click()
            await self.page.wait_for_load_state("networkidle")
        except Exception as e:
            logger.error(f"[ZhihuPublisher] Video publish failed: {e}")
            return {"success": False, "error": str(e)}

        result = {"success": True, "post_url": self.page.url}
        logger.info(f"[ZhihuPublisher] Video published: {self.page.url}")
        return result