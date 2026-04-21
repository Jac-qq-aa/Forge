"""Zhihu scraper with persistent browser context."""

import json
import logging
import os
from pathlib import Path
from playwright.async_api import async_playwright, BrowserContext
from playwright_stealth import Stealth

from forge.config import ZHIHU_BASE_URL, PLAYWRIGHT_TIMEOUT

# Persistent browser data directory
BROWSER_DATA_DIR = os.path.expanduser("~/.forge/browser_data/zhihu")
Path(BROWSER_DATA_DIR).mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)


class ZhihuScraper:
    """Async Zhihu scraper with persistent browser context."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.playwright = None
        self.context: BrowserContext = None

    async def __aenter__(self):
        logger.info("[ZhihuScraper] Starting browser with persistent context")
        self.playwright = await async_playwright().start()

        # Use persistent context
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
        logger.info("[ZhihuScraper] Stealth mode applied")

        # Get or create page
        if self.context.pages:
            self.page = self.context.pages[0]
        else:
            self.page = await self.context.new_page()

        self.page.set_default_timeout(PLAYWRIGHT_TIMEOUT)
        return self

    async def __aexit__(self, *args):
        logger.info("[ZhihuScraper] Closing browser")
        try:
            if self.context:
                await self.context.close()
        finally:
            if self.playwright:
                await self.playwright.stop()

    async def scrape_article(self, url: str) -> dict:
        """Scrape a Zhihu article by URL."""
        logger.info(f"[ZhihuScraper] Scraping article: {url}")
        await self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await self.page.wait_for_timeout(3000)

        # Check for login/verification
        current_url = self.page.url
        if "login" in current_url or "signin" in current_url or "unhuman" in current_url:
            logger.warning(f"[ZhihuScraper] Need login/verification, redirected to: {current_url}")
            return {"title": "需要登录", "text": "请先登录知乎", "likes": 0, "source_url": url}

        # Extract content using updated selectors
        title = ""
        text = ""
        likes = 0

        # Try multiple selectors for title
        title_selectors = [
            "h1.Post-Title",
            "[class*='Post-Title']",
            "article h1",
            "h1",
        ]
        for sel in title_selectors:
            try:
                if await self.page.locator(sel).count() > 0:
                    title = await self.page.locator(sel).first.text_content() or ""
                    if title.strip():
                        break
            except:
                continue

        # Try multiple selectors for content
        content_selectors = [
            ".Post-RichTextContainer",
            "[class*='RichText']",
            "article",
            ".RichContent-inner",
            "[class*='content']",
        ]
        for sel in content_selectors:
            try:
                if await self.page.locator(sel).count() > 0:
                    text = await self.page.locator(sel).first.text_content() or ""
                    if text.strip():
                        break
            except:
                continue

        # Extract likes
        try:
            likes_selectors = [
                "[class*='VoteButton']",
                "button[aria-label*='赞同']",
                "[class*='like'] button",
            ]
            for sel in likes_selectors:
                if await self.page.locator(sel).count() > 0:
                    likes_text = await self.page.locator(sel).first.text_content() or "0"
                    likes_text = likes_text.replace("赞同", "").replace("+", "").strip()
                    if "万" in likes_text:
                        likes = int(float(likes_text.replace("万", "")) * 10000)
                    elif likes_text.isdigit():
                        likes = int(likes_text)
                    break
        except:
            likes = 0

        result = {
            "title": title.strip()[:100],
            "text": text.strip(),
            "likes": likes,
            "images": [],
            "source_url": url,
        }
        logger.info(f"[ZhihuScraper] Scraped: title='{title[:30]}...', likes={likes}")
        return result

    async def scrape_question(self, url: str) -> dict:
        """Scrape a Zhihu question page and get the top answer."""
        logger.info(f"[ZhihuScraper] Scraping question: {url}")
        await self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await self.page.wait_for_timeout(3000)

        # Check for login
        current_url = self.page.url
        if "login" in current_url or "signin" in current_url or "unhuman" in current_url:
            logger.warning(f"[ZhihuScraper] Need login, redirected to: {current_url}")
            return {"title": "需要登录", "text": "请先登录知乎", "likes": 0, "source_url": url}

        # Extract question title
        title = ""
        title_selectors = [
            ".QuestionHeader-title",
            "h1.QuestionHeader-title",
            "[class*='QuestionHeader'] h1",
            "h1",
        ]
        for sel in title_selectors:
            try:
                if await self.page.locator(sel).count() > 0:
                    title = await self.page.locator(sel).first.text_content() or ""
                    if title.strip():
                        break
            except:
                continue

        # Get top answer
        answer_text = ""
        try:
            # Find answer items
            answer_selectors = [
                ".List-item",
                "[class*='AnswerItem']",
                "[class*='ContentItem']",
            ]
            for sel in answer_selectors:
                if await self.page.locator(sel).count() > 0:
                    answer_text = await self.page.locator(sel).first.locator("[class*='RichContent'], [class*='content']").text_content() or ""
                    if answer_text.strip():
                        break
        except:
            pass

        # Get likes
        likes = 0
        try:
            likes_text = await self.page.locator("[class*='VoteButton']").first.text_content() or "0"
            likes_text = likes_text.replace("赞同", "").replace("+", "").strip()
            if "万" in likes_text:
                likes = int(float(likes_text.replace("万", "")) * 10000)
            elif likes_text.isdigit():
                likes = int(likes_text)
        except:
            pass

        result = {
            "title": title.strip()[:100],
            "text": answer_text.strip(),
            "question": title.strip(),
            "answer": answer_text.strip(),
            "likes": likes,
            "images": [],
            "source_url": url,
        }
        logger.info(f"[ZhihuScraper] Scraped question: title='{title[:30]}...', likes={likes}")
        return result

    async def scrape_answer(self, url: str) -> dict:
        """Scrape a Zhihu answer page (answer/xxx format)."""
        logger.info(f"[ZhihuScraper] Scraping answer: {url}")
        await self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await self.page.wait_for_timeout(3000)

        # Check for login
        current_url = self.page.url
        if "login" in current_url or "signin" in current_url or "unhuman" in current_url:
            logger.warning(f"[ZhihuScraper] Need login, redirected to: {current_url}")
            return {"title": "需要登录", "text": "请先登录知乎", "likes": 0, "source_url": url}

        # Extract question title (answer pages show the question)
        title = ""
        title_selectors = [
            ".QuestionHeader-title",
            "h1.QuestionHeader-title",
            "[class*='QuestionHeader'] h1",
            "h1",
        ]
        for sel in title_selectors:
            try:
                if await self.page.locator(sel).count() > 0:
                    title = await self.page.locator(sel).first.text_content() or ""
                    if title.strip():
                        break
            except:
                continue

        # Get answer content
        answer_text = ""
        content_selectors = [
            "[class*='RichContent-inner']",
            "[class*='RichText']",
            ".RichContent-inner",
            "article",
        ]
        for sel in content_selectors:
            try:
                if await self.page.locator(sel).count() > 0:
                    answer_text = await self.page.locator(sel).first.text_content() or ""
                    if answer_text.strip():
                        break
            except:
                continue

        # Get likes
        likes = 0
        try:
            likes_text = await self.page.locator("[class*='VoteButton']").first.text_content() or "0"
            likes_text = likes_text.replace("赞同", "").replace("+", "").strip()
            if "万" in likes_text:
                likes = int(float(likes_text.replace("万", "")) * 10000)
            elif likes_text.isdigit():
                likes = int(likes_text)
        except:
            pass

        result = {
            "title": title.strip()[:100] if title else "无标题",
            "text": answer_text.strip(),
            "likes": likes,
            "images": [],
            "source_url": url,
        }
        logger.info(f"[ZhihuScraper] Scraped answer: title='{title[:30] if title else 'N/A'}...', text_len={len(answer_text)}, likes={likes}")
        return result

    async def scrape_by_topic(self, topic: str) -> dict:
        """Search Zhihu by topic (requires login)."""
        logger.info(f"[ZhihuScraper] Searching for topic: {topic}")
        search_url = f"{ZHIHU_BASE_URL}/search?type=content&q={topic}"
        await self.page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        await self.page.wait_for_timeout(5000)

        # Check for login
        current_url = self.page.url
        if "login" in current_url or "signin" in current_url:
            logger.warning(f"[ZhihuScraper] Need login for search")
            return {"title": f"搜索: {topic}", "text": "搜索需要登录", "likes": 0, "source_url": search_url}

        # Find search results - iterate through cards to find a valid link
        try:
            cards = await self.page.locator(".SearchResult-Card, [class*='ContentItem']").all()
            logger.info(f"[ZhihuScraper] Found {len(cards)} search result cards")

            for card in cards:
                # Get all links in the card
                links = await card.locator("a").all()
                for link in links:
                    try:
                        href = await link.get_attribute("href")
                        if not href:
                            continue

                        # Skip ads and promoted content
                        if any(skip in href for skip in ["kvip", "ebook", "market", "ad", "promo"]):
                            continue

                        # Check if it's a valid question or article link
                        if "question" in href or "zhuanlan" in href or "/p/" in href:
                            # Build full URL
                            if href.startswith("/"):
                                href = f"https://www.zhihu.com{href}"

                            logger.info(f"[ZhihuScraper] Found valid result: {href}")

                            # Navigate to the link
                            await self.page.goto(href, wait_until="domcontentloaded", timeout=60000)
                            await self.page.wait_for_timeout(3000)

                            # Determine type and scrape
                            current_url = self.page.url
                            if "zhuanlan" in current_url or "/p/" in current_url:
                                return await self.scrape_article(current_url)
                            else:
                                return await self.scrape_question(current_url)
                    except Exception as e:
                        logger.debug(f"[ZhihuScraper] Error checking link: {e}")
                        continue
        except Exception as e:
            logger.error(f"[ZhihuScraper] Error finding search results: {e}")

        return {"title": f"搜索: {topic}", "text": f"未找到 '{topic}' 相关内容", "likes": 0, "source_url": search_url}

    async def search_articles(self, topic: str, max_results: int = 5) -> list:
        """Search Zhihu by topic and return a list of articles for user selection.

        Args:
            topic: Search keyword.
            max_results: Maximum number of results to return.

        Returns:
            List of dicts with 'title', 'summary', 'source_url', 'type'.
        """
        logger.info(f"[ZhihuScraper] Searching articles for: {topic} (max: {max_results})")
        search_url = f"{ZHIHU_BASE_URL}/search?type=content&q={topic}"
        await self.page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        await self.page.wait_for_timeout(3000)

        # Check for login
        current_url = self.page.url
        if "login" in current_url or "signin" in current_url:
            logger.warning(f"[ZhihuScraper] Need login for search")
            return []

        articles = []
        seen_urls = set()  # 用于去重

        try:
            # 滚动加载更多搜索结果（确保有足够内容）
            logger.info("[ZhihuScraper] Scrolling to load more search results...")
            scroll_attempts = 0
            max_scroll_attempts = 10

            while scroll_attempts < max_scroll_attempts:
                # 先尝试提取当前已有的结果
                cards = await self.page.locator(".SearchResult-Card, [class*='ContentItem'], div[data-zalog-module='SearchResult']").all()

                for i, card in enumerate(cards):
                    if len(articles) >= max_results:
                        break

                    try:
                        # 尝试多种选择器获取链接
                        href = None
                        title = ""

                        # 方法1: 标题链接
                        title_link = card.locator("h2 a").first
                        if await title_link.count() > 0:
                            href = await title_link.get_attribute("href")
                            title = (await title_link.text_content() or "").strip()[:100]

                        # 方法2: 其他标题选择器
                        if not href:
                            title_link2 = card.locator("[class*='title'] a, [class*='Title'] a").first
                            if await title_link2.count() > 0:
                                href = await title_link2.get_attribute("href")
                                title = (await title_link2.text_content() or "").strip()[:100]

                        # 方法3: 任何包含问题的链接
                        if not href:
                            question_link = card.locator("a[href*='question'], a[href*='zhuanlan'], a[href*='/p/']").first
                            if await question_link.count() > 0:
                                href = await question_link.get_attribute("href")
                                title = (await question_link.text_content() or "").strip()[:100]

                        # 方法4: 从卡片中的任何链接获取
                        if not href:
                            all_links = await card.locator("a").all()
                            for link in all_links:
                                try:
                                    link_href = await link.get_attribute("href")
                                    if link_href and ("question" in link_href or "zhuanlan" in link_href or "/p/" in link_href or "answer" in link_href):
                                        href = link_href
                                        title = (await link.text_content() or "").strip()[:100]
                                        if title:
                                            break
                                except:
                                    continue

                        if not href:
                            continue

                        # Skip ads - 放宽条件，只跳过明显的广告
                        if any(skip in href for skip in ["kvip", "ebook", "market", "zvideo"]):
                            continue

                        # Build full URL
                        if href.startswith("//"):
                            href = f"https:{href}"
                        elif href.startswith("/"):
                            href = f"https://www.zhihu.com{href}"
                        elif not href.startswith("http"):
                            href = f"https://www.zhihu.com{href}"

                        # 去重检查
                        if href in seen_urls:
                            continue
                        seen_urls.add(href)

                        if not title:
                            title = "无标题"

                        articles.append({
                            "title": title,
                            "summary": "",
                            "source_url": href,
                            "type": "question" if "question" in href else "article",
                            "likes": 0,
                        })
                        logger.info(f"[ZhihuScraper] Found article {len(articles)}: {title[:30]}...")

                    except Exception as e:
                        logger.debug(f"[ZhihuScraper] Error processing card {i}: {e}")
                        continue

                # 如果已经够了，停止滚动
                if len(articles) >= max_results:
                    break

                # 滚动加载更多
                await self.page.evaluate("window.scrollBy(0, 1000)")
                await self.page.wait_for_timeout(2000)
                scroll_attempts += 1
                logger.debug(f"[ZhihuScraper] Scrolled {scroll_attempts}/{max_scroll_attempts}, found {len(articles)} articles")

        except Exception as e:
            logger.error(f"[ZhihuScraper] Error searching articles: {e}")

        logger.info(f"[ZhihuScraper] Returning {len(articles)} articles for topic: {topic}")
        return articles

    async def scrape_by_url(self, url: str) -> dict:
        """Scrape content by URL (auto-detect type)."""
        if "zhuanlan" in url or "/p/" in url:
            return await self.scrape_article(url)
        elif "question" in url:
            return await self.scrape_question(url)
        else:
            return await self.scrape_article(url)

    async def get_user_articles(self, user_id: str, max_results: int = 5) -> list:
        """Get recent articles from a Zhihu user/blogger.

        Args:
            user_id: Zhihu user ID (from URL like https://www.zhihu.com/people/{user_id}).
            max_results: Maximum number of results to return.

        Returns:
            List of dicts with 'title', 'summary', 'source_url', 'type'.
        """
        logger.info(f"[ZhihuScraper] Getting articles for user: {user_id}")

        # Build user URL
        if user_id.startswith("http"):
            user_url = user_id
        else:
            user_url = f"{ZHIHU_BASE_URL}/people/{user_id}/answers"

        await self.page.goto(user_url, wait_until="domcontentloaded", timeout=60000)
        await self.page.wait_for_timeout(5000)

        # Check for login
        current_url = self.page.url
        if "login" in current_url or "signin" in current_url:
            logger.warning(f"[ZhihuScraper] Need login for user page")
            return []

        articles = []
        try:
            # Find answer/article items on user page
            item_selectors = [
                "[class*='AnswerItem']",
                "[class*='ContentItem']",
                ".List-item",
            ]

            items = []
            for selector in item_selectors:
                found = await self.page.locator(selector).all()
                if found:
                    items = found
                    logger.info(f"[ZhihuScraper] Found {len(items)} items with selector: {selector}")
                    break

            for item in items:
                if len(articles) >= max_results:
                    break

                try:
                    # Get title - 知乎用户页的标题在 h2 元素中
                    title = ""
                    try:
                        if await item.locator("h2").count() > 0:
                            title = await item.locator("h2").first.text_content() or ""
                            title = title.strip()
                    except:
                        pass

                    # Get URL - 从第一个链接获取，需要处理 // 开头的格式
                    source_url = ""
                    try:
                        links = await item.locator("a").all()
                        for link in links:
                            href = await link.get_attribute("href")
                            if href and ("question" in href or "zhuanlan" in href):
                                # 处理 // 开头的 URL
                                if href.startswith("//"):
                                    href = f"https:{href}"
                                elif href.startswith("/"):
                                    href = f"https://www.zhihu.com{href}"
                                source_url = href
                                break
                    except:
                        pass

                    if not source_url:
                        continue

                    # Get summary/excerpt
                    summary = ""
                    try:
                        if await item.locator("[class*='RichContent']").count() > 0:
                            summary = await item.locator("[class*='RichContent']").first.text_content() or ""
                            summary = summary.strip()[:200]
                    except:
                        pass

                    # Determine type
                    article_type = "question" if "question" in source_url else "article"

                    # Avoid duplicates
                    if source_url and not any(a["source_url"] == source_url for a in articles):
                        articles.append({
                            "title": title[:100] if title else "无标题",
                            "summary": summary[:200],
                            "source_url": source_url,
                            "type": article_type,
                            "likes": 0,
                        })
                        logger.info(f"[ZhihuScraper] Found user article: {title[:30] if title else 'N/A'}...")

                except Exception as e:
                    logger.debug(f"[ZhihuScraper] Error processing item: {e}")
                    continue

        except Exception as e:
            logger.error(f"[ZhihuScraper] Error getting user articles: {e}")

        logger.info(f"[ZhihuScraper] Returning {len(articles)} user articles")
        return articles

    async def get_question_answers(self, question_url: str, max_answers: int = 10) -> dict:
        """获取知乎问题下的所有回答，筛选后返回。

        Args:
            question_url: 问题链接（可以是 question/xxx 或 question/xxx/answer/yyy）
            max_answers: 最多返回的回答数

        Returns:
            dict with question_title, question_url, answers list
        """
        logger.info(f"[ZhihuScraper] Getting answers for question: {question_url}")

        # 从 URL 中提取纯问题链接（去除 answer 部分）
        import re
        question_match = re.search(r"question/(\d+)", question_url)
        if question_match:
            question_id = question_match.group(1)
            pure_question_url = f"https://www.zhihu.com/question/{question_id}"
            logger.info(f"[ZhihuScraper] Extracted pure question URL: {pure_question_url}")
        else:
            pure_question_url = question_url

        # 访问纯问题页面（不带 answer）
        await self.page.goto(pure_question_url, wait_until="domcontentloaded", timeout=60000)
        await self.page.wait_for_timeout(5000)

        # Check for login
        current_url = self.page.url
        if "login" in current_url or "signin" in current_url or "unhuman" in current_url:
            logger.warning(f"[ZhihuScraper] Need login, redirected to: {current_url}")
            return {"question_title": "需要登录", "question_url": pure_question_url, "answers": []}

        # 提取问题标题
        question_title = ""
        title_selectors = [
            ".QuestionHeader-title",
            "h1.QuestionHeader-title",
            "[class*='QuestionHeader'] h1",
            "h1",
        ]
        for sel in title_selectors:
            try:
                if await self.page.locator(sel).count() > 0:
                    question_title = await self.page.locator(sel).first.text_content() or ""
                    if question_title.strip():
                        break
            except:
                continue

        # 滚动加载更多回答（增加滚动次数）
        logger.info("[ZhihuScraper] Scrolling to load more answers...")
        for i in range(10):
            await self.page.evaluate("window.scrollBy(0, 1500)")
            await self.page.wait_for_timeout(1500)
            logger.debug(f"[ZhihuScraper] Scrolled {i+1}/10")

        # 获取所有回答
        answers = []
        # 使用更精确的选择器
        answer_selectors = [
            "div.List-item",
            "div[class*='ContentItem-answer']",
            "[class*='AnswerItem']",
        ]

        answer_items = []
        for sel in answer_selectors:
            try:
                items = await self.page.locator(sel).all()
                if items:
                    answer_items = items
                    logger.info(f"[ZhihuScraper] Found {len(items)} answer items with selector: {sel}")
                    break
            except:
                continue

        # 提取每个回答的内容
        for i, item in enumerate(answer_items):
            try:
                # 获取回答内容
                answer_text = ""
                content_selectors = [
                    "[class*='RichContent-inner']",
                    "[class*='RichText']",
                    ".RichContent-inner",
                ]
                for sel in content_selectors:
                    try:
                        if await item.locator(sel).count() > 0:
                            answer_text = await item.locator(sel).first.text_content() or ""
                            if answer_text.strip():
                                break
                    except:
                        continue

                # 获取点赞数 - 使用多种选择器尝试
                likes = 0
                try:
                    # 尝试多种点赞数选择器
                    likes_selectors = [
                        "button[aria-label*='赞同']",
                        "[class*='VoteButton'] button",
                        "button[class*='VoteButton']",
                        "[class*='ActionBar'] button[aria-label]",
                    ]
                    for likes_sel in likes_selectors:
                        likes_el = item.locator(likes_sel)
                        if await likes_el.count() > 0:
                            # 尝试从 aria-label 获取
                            aria_label = await likes_el.first.get_attribute("aria-label") or ""
                            if aria_label and "赞同" in aria_label:
                                # aria-label 格式可能是 "赞同 1234 人"
                                likes_text = aria_label.replace("赞同", "").replace("人", "").strip()
                                if "万" in likes_text:
                                    likes = int(float(likes_text.replace("万", "").strip()) * 10000)
                                elif likes_text.isdigit():
                                    likes = int(likes_text)
                                if likes > 0:
                                    break

                            # 尝试从文本内容获取
                            likes_text = await likes_el.first.text_content() or "0"
                            likes_text = likes_text.replace("赞同", "").replace("+", "").strip()
                            if "万" in likes_text:
                                likes = int(float(likes_text.replace("万", "").strip()) * 10000)
                            elif likes_text.isdigit():
                                likes = int(likes_text)
                            if likes > 0:
                                break
                except Exception as e:
                    logger.debug(f"[ZhihuScraper] Error getting likes: {e}")
                    likes = 0

                # 获取作者
                author = ""
                try:
                    author_el = item.locator("[class*='AuthorInfo-name']")
                    if await author_el.count() > 0:
                        author = await author_el.first.text_content() or ""
                        author = author.strip()
                except:
                    pass

                # 获取回答链接 - 改进逻辑
                answer_url = ""
                try:
                    # 方法1: 从 data-za-index 属性获取（这是最可靠的方式）
                    data_za_index = await item.get_attribute("data-za-index")
                    if data_za_index:
                        # 使用已有的 question_id 构建回答链接
                        answer_url = f"{pure_question_url}/answer/{data_za_index}"
                        logger.debug(f"[ZhihuScraper] Got answer URL from data-za-index: {answer_url}")

                    # 方法2: 从 item 中的链接获取
                    if not answer_url:
                        links = await item.locator("a[href*='/answer/']").all()
                        for link in links:
                            href = await link.get_attribute("href")
                            if href and "/answer/" in href:
                                if href.startswith("//"):
                                    href = f"https:{href}"
                                elif href.startswith("/"):
                                    href = f"https://www.zhihu.com{href}"
                                # 确保链接格式正确
                                if re.match(r"https://www\.zhihu\.com/question/\d+/answer/\d+", href):
                                    answer_url = href
                                    break
                except Exception as e:
                    logger.debug(f"[ZhihuScraper] Error getting answer URL: {e}")

                # 最终确保有回答链接
                if not answer_url:
                    answer_url = pure_question_url  # fallback

                # 筛选：字数 >= 300
                char_count = len(answer_text.strip())
                if char_count < 300:
                    logger.debug(f"[ZhihuScraper] Skipping answer {i}: char_count={char_count} < 300")
                    continue

                logger.info(f"[ZhihuScraper] Answer {i}: likes={likes}, chars={char_count}, url={answer_url}, author={author}")

                answers.append({
                    "id": i + 1,
                    "text": answer_text.strip(),
                    "likes": likes,
                    "author": author,
                    "source_url": answer_url,
                    "char_count": char_count,
                })

            except Exception as e:
                logger.debug(f"[ZhihuScraper] Error extracting answer {i}: {e}")
                continue

        # 按点赞排序
        answers.sort(key=lambda x: x["likes"], reverse=True)

        # 限制数量
        answers = answers[:max_answers]

        logger.info(f"[ZhihuScraper] Returning {len(answers)} answers for question: {question_title[:30]}...")

        return {
            "question_title": question_title.strip()[:100] if question_title else "无标题",
            "question_url": pure_question_url,
            "answers": answers,
        }