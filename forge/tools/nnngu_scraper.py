"""锐博官网内容爬虫.

抓取 https://www.nnngu.com/ 的文章内容。
"""

import re
import logging
import requests
from typing import List, Dict, Optional
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://www.nnngu.com"


class NnnguScraper:
    """锐博官网爬虫."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        })

    def get_article_ids_from_list(self, list_url: str, max_pages: int = 50) -> List[str]:
        """从列表页获取文章ID.

        Args:
            list_url: 列表页URL（如 /special-news.html）
            max_pages: 最大爬取页数

        Returns:
            文章ID列表
        """
        article_ids = []
        full_url = f"{BASE_URL}{list_url}"

        for page in range(1, max_pages + 1):
            url = f"{full_url}?page={page}" if page > 1 else full_url
            logger.info(f"[NnnguScraper] Fetching list page: {url}")

            try:
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")

                # 提取文章链接
                links = soup.find_all("a", href=re.compile(r"/article/\d+\.html"))
                page_ids = []
                for link in links:
                    href = link.get("href", "")
                    match = re.search(r"/article/(\d+)\.html", href)
                    if match:
                        article_id = match.group(1)
                        if article_id not in article_ids:
                            page_ids.append(article_id)
                            article_ids.append(article_id)

                logger.info(f"[NnnguScraper] Page {page}: found {len(page_ids)} articles")

                # 如果本页没有新文章，说明到最后一页了
                if not page_ids:
                    logger.info(f"[NnnguScraper] No more articles at page {page}, stopping")
                    break

            except Exception as e:
                logger.error(f"[NnnguScraper] Failed to fetch page {page}: {e}")
                break

        logger.info(f"[NnnguScraper] Total article IDs: {len(article_ids)}")
        return article_ids

    def scrape_article(self, article_id: str) -> Optional[Dict]:
        """抓取单篇文章.

        Args:
            article_id: 文章ID

        Returns:
            文章数据（title, content, date）或 None
        """
        url = f"{BASE_URL}/article/{article_id}.html"
        logger.info(f"[NnnguScraper] Scraping article: {url}")

        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # 提取标题
            title_tag = soup.find("h1") or soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else f"文章{article_id}"
            # 清理标题中的网站名
            title = re.sub(r"_锐博人力|_nnngu\.com", "", title).strip()

            # 提取正文
            # 尝试多种选择器
            content_div = (
                soup.find("div", class_="article-content") or
                soup.find("div", class_="content") or
                soup.find("div", class_="news-content") or
                soup.find("article") or
                soup.find("div", class_="detail-content")
            )

            if content_div:
                # 移除图片、脚本等
                for tag in content_div.find_all(["img", "script", "style", "iframe"]):
                    tag.decompose()
                content = content_div.get_text(separator="\n", strip=True)
            else:
                # 备用：取 body 中所有文本
                body = soup.find("body")
                content = body.get_text(separator="\n", strip=True) if body else ""

            # 提取日期
            date = ""
            date_tag = soup.find("span", class_="date") or soup.find("time")
            if date_tag:
                date = date_tag.get_text(strip=True)
            else:
                # 尝试从文本中提取日期
                date_match = re.search(r"(\d{4}-\d{2}-\d{2})", resp.text)
                if date_match:
                    date = date_match.group(1)

            # 清理内容
            content = self._clean_content(content)

            if not content or len(content) < 50:
                logger.warning(f"[NnnguScraper] Article {article_id} content too short, skipping")
                return None

            logger.info(f"[NnnguScraper] Article {article_id}: {len(content)} chars")

            return {
                "id": article_id,
                "title": title,
                "content": content,
                "date": date,
                "url": url,
            }

        except Exception as e:
            logger.error(f"[NnnguScraper] Failed to scrape {article_id}: {e}")
            return None

    def _clean_content(self, content: str) -> str:
        """清理内容."""
        # 移除多余空白
        content = re.sub(r"\n{3,}", "\n\n", content)
        content = re.sub(r" {2,}", " ", content)
        # 移除常见干扰文本
        patterns = [
            r"服务电话.*400-\d{4}-\d{4}",
            r"全国分站",
            r"对不起，找不到您输入的内容",
            r"点击直达",
            r"请输入关键词",
            r"搜索",
        ]
        for pattern in patterns:
            content = re.sub(pattern, "", content)
        return content.strip()

    def scrape_single_page(self, page_url: str, title_override: str = None) -> Optional[Dict]:
        """抓取单页内容（如集团概况）.

        Args:
            page_url: 页面URL
            title_override: 强制标题

        Returns:
            页面数据
        """
        url = f"{BASE_URL}{page_url}"
        logger.info(f"[NnnguScraper] Scraping single page: {url}")

        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # 提取标题
            if title_override:
                title = title_override
            else:
                title_tag = soup.find("h1") or soup.find("title")
                title = title_tag.get_text(strip=True) if title_tag else page_url
                title = re.sub(r"_锐博人力|_nnngu\.com", "", title).strip()

            # 提取正文
            body = soup.find("body")
            if body:
                # 移除导航、侧边栏等
                for tag in body.find_all(["nav", "header", "footer", "script", "style", "img", "iframe"]):
                    tag.decompose()
                # 尝试移除菜单区域
                for tag in body.find_all(["div"], class_=re.compile(r"nav|menu|sidebar|footer|header")):
                    tag.decompose()
                content = body.get_text(separator="\n", strip=True)

            content = self._clean_content(content)

            if not content or len(content) < 100:
                logger.warning(f"[NnnguScraper] Page {page_url} content too short")
                return None

            return {
                "id": page_url.replace("/", "_").replace(".html", ""),
                "title": title,
                "content": content,
                "url": url,
            }

        except Exception as e:
            logger.error(f"[NnnguScraper] Failed to scrape {page_url}: {e}")
            return None

    def scrape_all(self) -> List[Dict]:
        """抓取所有内容.

        Returns:
            所有文章数据
        """
        articles = []

        # 1. 抓取招聘公告（special-news）
        logger.info("[NnnguScraper] === Scraping 招聘公告 ===")
        ids = self.get_article_ids_from_list("/special-news.html", max_pages=40)
        for article_id in ids:
            article = self.scrape_article(article_id)
            if article:
                article["category"] = "recruitment"
                articles.append(article)

        # 2. 抓取常见问题（common-problem）
        logger.info("[NnnguScraper] === Scraping 常见问题 ===")
        ids = self.get_article_ids_from_list("/common-problem.html", max_pages=5)
        for article_id in ids:
            article = self.scrape_article(article_id)
            if article:
                article["category"] = "faq"
                articles.append(article)

        # 3. 抓取单页内容
        logger.info("[NnnguScraper] === Scraping single pages ===")
        single_pages = [
            ("/groupprofile.html", "集团概况", "company_intro"),
            ("/developmentcourse.html", "历史沿革", "company_intro"),
            ("/framework.html", "旗下机构", "company_intro"),
        ]
        for page_url, title, category in single_pages:
            article = self.scrape_single_page(page_url, title)
            if article:
                article["category"] = category
                articles.append(article)

        logger.info(f"[NnnguScraper] Total articles scraped: {len(articles)}")
        return articles


# 导出函数
def scrape_nnngu() -> List[Dict]:
    """抓取锐博官网所有内容."""
    scraper = NnnguScraper()
    return scraper.scrape_all()