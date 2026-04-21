"""WeChat Official Account scraper via Sogou search.

使用持久化浏览器上下文避免反爬虫检测。
"""

import logging
import re
import os
from pathlib import Path
from typing import Optional
from playwright.async_api import async_playwright, BrowserContext

from forge.config import PLAYWRIGHT_TIMEOUT

logger = logging.getLogger(__name__)

# Sogou WeChat search URL
SOGOU_WECHAT_URL = "https://weixin.sogou.com/weixin"
SOGOU_BASE_URL = "https://weixin.sogou.com"

# Persistent browser data directory
BROWSER_DATA_DIR = os.path.expanduser("~/.forge/browser_data/wechat")
Path(BROWSER_DATA_DIR).mkdir(parents=True, exist_ok=True)


class WechatScraper:
    """Async WeChat Official Account scraper with persistent context."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.playwright = None
        self.context: BrowserContext = None
        self.page = None

    async def __aenter__(self):
        logger.info("[WechatScraper] Starting browser with persistent context")
        self.playwright = await async_playwright().start()

        # Use persistent context to avoid anti-spider
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

        # Get or create page
        if self.context.pages:
            self.page = self.context.pages[0]
        else:
            self.page = await self.context.new_page()

        self.page.set_default_timeout(PLAYWRIGHT_TIMEOUT)
        logger.info("[WechatScraper] Browser started")
        return self

    async def __aexit__(self, *args):
        logger.info("[WechatScraper] Closing browser")
        try:
            if self.context:
                await self.context.close()
        finally:
            if self.playwright:
                await self.playwright.stop()

    async def search_articles(self, keyword: str, max_results: int = 5) -> list:
        """Search WeChat articles by keyword.

        Args:
            keyword: Search keyword.
            max_results: Maximum number of results to return.

        Returns:
            List of dicts with 'title', 'summary', 'source_url', 'type', 'author'.
        """
        logger.info(f"[WechatScraper] Searching articles: {keyword}")

        # type=2 表示搜索文章
        search_url = f"{SOGOU_WECHAT_URL}?type=2&query={keyword}"
        await self.page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await self.page.wait_for_timeout(3000)

        # 检查是否被反爬虫
        if "antispider" in self.page.url:
            logger.warning("[WechatScraper] Anti-spider detected, waiting...")
            await self.page.wait_for_timeout(5000)
            # 尝试刷新
            await self.page.reload()
            await self.page.wait_for_timeout(5000)

        articles = []

        try:
            # 搜索结果列表
            items = await self.page.locator(".news-list li").all()
            logger.info(f"[WechatScraper] Found {len(items)} items")

            for item in items:
                if len(articles) >= max_results:
                    break

                try:
                    # 获取标题和链接
                    title_link = item.locator("h3 a, .txt-box h3 a")
                    if await title_link.count() == 0:
                        continue

                    title = await title_link.first.text_content() or ""
                    title = title.strip()

                    href = await title_link.first.get_attribute("href")
                    if href:
                        if href.startswith("/"):
                            href = f"{SOGOU_BASE_URL}{href}"

                    if not title or not href:
                        continue

                    # 获取摘要
                    summary = ""
                    try:
                        summary_el = item.locator(".txt-box p")
                        if await summary_el.count() > 0:
                            summary = await summary_el.first.text_content() or ""
                            summary = summary.strip()[:200]
                    except:
                        pass

                    # 获取公众号名称
                    author = ""
                    try:
                        author_el = item.locator(".s-p")
                        if await author_el.count() > 0:
                            author_text = await author_el.first.text_content() or ""
                            author_text = re.sub(r"document\.write.*", "", author_text)
                            author_text = re.sub(r"\d{4}-\d{2}-\d{2}", "", author_text)
                            author_text = author_text.strip()
                            author = author_text.split()[0] if author_text else ""
                    except:
                        pass

                    articles.append({
                        "title": title[:100],
                        "summary": summary,
                        "source_url": href,  # 搜狗跳转链接
                        "type": "wechat_article",
                        "author": author,
                    })
                    logger.info(f"[WechatScraper] Found: {title[:30]}...")

                except Exception as e:
                    logger.debug(f"[WechatScraper] Error processing item: {e}")
                    continue

        except Exception as e:
            logger.error(f"[WechatScraper] Search error: {e}")

        logger.info(f"[WechatScraper] Returning {len(articles)} articles")
        return articles

    async def scrape_article(self, url: str) -> dict:
        """Scrape a WeChat article by URL.

        由于搜狗跳转链接可能触发反爬虫，推荐使用 scrape_from_search 方法。

        Args:
            url: 搜狗跳转链接或微信文章链接。

        Returns:
            Dict with 'title', 'text', 'author', 'source_url'.
        """
        logger.info(f"[WechatScraper] Scraping article: {url}")

        result = {
            "title": "",
            "text": "",
            "author": "",
            "source_url": url,
        }

        try:
            # 如果是搜狗链接，需要特殊处理
            if "sogou.com" in url:
                # 先访问搜狗主页建立cookie
                await self.page.goto(SOGOU_BASE_URL, wait_until="domcontentloaded", timeout=30000)
                await self.page.wait_for_timeout(2000)

                # 然后访问跳转链接
                await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await self.page.wait_for_timeout(3000)

                # 检查反爬虫
                if "antispider" in self.page.url:
                    logger.warning("[WechatScraper] Anti-spider triggered")
                    return result
            else:
                await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await self.page.wait_for_timeout(3000)

            # 检查是否跳转到微信文章
            current_url = self.page.url
            if "mp.weixin" in current_url or "weixin.qq.com" in current_url:
                result = await self._extract_wechat_content(url)
            elif "sogou.com" in current_url:
                # 还在搜狗页面，可能需要点击跳转
                logger.info("[WechatScraper] Still on Sogou, looking for jump link...")
                # 尝试找跳转链接
                jump_link = self.page.locator("a[href*='mp.weixin'], a[href*='weixin.qq.com']")
                if await jump_link.count() > 0:
                    await jump_link.first.click()
                    await self.page.wait_for_timeout(3000)
                    result = await self._extract_wechat_content(url)

        except Exception as e:
            logger.error(f"[WechatScraper] Scrape error: {e}")

        return result

    async def scrape_from_search(self, keyword: str, index: int = 0) -> dict:
        """从搜索结果中抓取文章（推荐方法）。

        先搜索关键词，然后点击搜索结果进入文章页面，避免直接访问搜狗跳转链接。

        Args:
            keyword: 搜索关键词。
            index: 搜索结果索引（从0开始）。

        Returns:
            Dict with 'title', 'text', 'author', 'source_url'.
        """
        logger.info(f"[WechatScraper] scrape_from_search: keyword='{keyword}', index={index}")

        result = {
            "title": "",
            "text": "",
            "author": "",
            "source_url": "",
        }

        try:
            # 搜索
            search_url = f"{SOGOU_WECHAT_URL}?type=2&query={keyword}"
            await self.page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await self.page.wait_for_timeout(3000)

            # 检查反爬虫
            if "antispider" in self.page.url:
                logger.warning("[WechatScraper] Anti-spider detected")
                await self.page.wait_for_timeout(5000)
                await self.page.reload()
                await self.page.wait_for_timeout(3000)

            # 找到搜索结果
            items = await self.page.locator(".news-list li").all()
            if index >= len(items):
                logger.error(f"[WechatScraper] Index {index} out of range (total {len(items)})")
                return result

            item = items[index]
            title_link = item.locator("h3 a, .txt-box h3 a").first

            # 记录标题
            title = await title_link.text_content() or ""
            result["title"] = title.strip()

            # 记录公众号
            author_el = item.locator(".s-p")
            if await author_el.count() > 0:
                author_text = await author_el.first.text_content() or ""
                author_text = re.sub(r"document\.write.*", "", author_text)
                author_text = re.sub(r"\d{4}-\d{2}-\d{2}", "", author_text)
                result["author"] = author_text.strip().split()[0] if author_text.strip() else ""

            # 点击进入文章（不直接访问跳转链接）
            logger.info(f"[WechatScraper] Clicking article: {title[:30]}...")
            async with self.context.expect_page() as new_page_info:
                await title_link.click()

            new_page = await new_page_info.value
            await new_page.wait_for_load_state("domcontentloaded", timeout=30000)
            await new_page.wait_for_timeout(3000)

            # 获取真实URL
            result["source_url"] = new_page.url

            # 在新页面提取内容
            content = await self._extract_content_from_page(new_page)
            result["text"] = content

            # 关闭新页面
            await new_page.close()

        except Exception as e:
            logger.error(f"[WechatScraper] scrape_from_search error: {e}")

        return result

    async def _extract_wechat_content(self, original_url: str) -> dict:
        """从当前页面提取微信文章内容。"""
        result = {
            "title": "",
            "text": "",
            "author": "",
            "source_url": original_url,
        }

        # 获取标题
        for sel in ["#activity-name", ".rich_media_title", "h1"]:
            if await self.page.locator(sel).count() > 0:
                result["title"] = await self.page.locator(sel).first.text_content() or ""
                result["title"] = result["title"].strip()
                if result["title"]:
                    break

        # 获取作者
        for sel in ["#js_name", ".rich_media_meta_nickname"]:
            if await self.page.locator(sel).count() > 0:
                result["author"] = await self.page.locator(sel).first.text_content() or ""
                result["author"] = result["author"].strip()
                if result["author"]:
                    break

        # 获取正文
        for sel in ["#js_content", ".rich_media_content", "article"]:
            if await self.page.locator(sel).count() > 0:
                result["text"] = await self.page.locator(sel).first.text_content() or ""
                result["text"] = result["text"].strip()
                if result["text"]:
                    break

        logger.info(f"[WechatScraper] Extracted: title='{result['title'][:30]}...', text_len={len(result['text'])}")
        return result

    async def _extract_content_from_page(self, page) -> str:
        """从指定页面提取内容。"""
        for sel in ["#js_content", ".rich_media_content", "article"]:
            if await page.locator(sel).count() > 0:
                content = await page.locator(sel).first.text_content() or ""
                return content.strip()
        return ""


# 测试
if __name__ == "__main__":
    async def test():
        async with WechatScraper(headless=False) as scraper:
            # 测试从搜索抓取
            print("\n=== 测试 scrape_from_search ===")
            result = await scraper.scrape_from_search("人力资源", index=0)
            print(f"标题: {result['title']}")
            print(f"作者: {result['author']}")
            print(f"内容长度: {len(result['text'])}")
            print(f"URL: {result['source_url'][:60]}...")
            print(f"内容预览: {result['text'][:200]}...")

    asyncio.run(test())