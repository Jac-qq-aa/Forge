#!/usr/bin/env python
"""抓取锐博集团知乎机构号和公众号文章并导入向量数据库。

知乎机构号: https://www.zhihu.com/org/rui-bo-ji-tuan-5
公众号关键词: 锐博集团
"""

import asyncio
import json
import logging
import os
import sys
import re
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from forge.tools.zhihu_scraper_persistent import ZhihuScraper
from forge.tools.wechat_scraper import WechatScraper
from forge.knowledge.article_importer import ArticleImporter, ImportResult
from forge.knowledge.config import CATEGORIES

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 输出目录
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "articles"
ZHIHU_DIR = OUTPUT_DIR / "zhihu"
WECHAT_DIR = OUTPUT_DIR / "wechat"


async def scrape_zhihu_org(org_url: str, max_articles: int = 20) -> list:
    """抓取知乎机构号的所有文章。

    Args:
        org_url: 机构号URL，如 https://www.zhihu.com/org/rui-bo-ji-tuan-5
        max_articles: 最大抓取数量
    """
    articles = []

    async with ZhihuScraper(headless=False) as scraper:
        logger.info(f"[Zhihu] 访问机构号页面: {org_url}")
        await scraper.page.goto(org_url, wait_until="domcontentloaded", timeout=60000)
        await scraper.page.wait_for_timeout(5000)

        # 检查是否需要登录
        current_url = scraper.page.url
        if "login" in current_url or "signin" in current_url:
            logger.warning(f"[Zhihu] 需要登录，跳转到: {current_url}")
            logger.info("[Zhihu] 请在浏览器中手动登录，登录后继续...")
            # 等待用户登录（最多等待120秒）
            for i in range(120):
                await scraper.page.wait_for_timeout(1000)
                current_url = scraper.page.url
                if "login" not in current_url and "signin" not in current_url:
                    logger.info("[Zhihu] 登录成功，继续抓取...")
                    break
            if "login" in current_url or "signin" in current_url:
                logger.error("[Zhihu] 登录超时，退出")
                return []

        # 获取机构名称
        org_name = ""
        try:
            name_el = scraper.page.locator(".OrgHeader-title, [class*='OrgHeader'] h1, h1")
            if await name_el.count() > 0:
                org_name = await name_el.first.text_content() or ""
                org_name = org_name.strip()
                logger.info(f"[Zhihu] 机构名称: {org_name}")
        except:
            org_name = "锐博集团"

        # 滚动加载更多文章
        logger.info("[Zhihu] 滚动加载更多文章...")
        for i in range(15):
            await scraper.page.evaluate("window.scrollBy(0, 1500)")
            await scraper.page.wait_for_timeout(2000)
            logger.debug(f"[Zhihu] 滚动 {i+1}/15")

        # 提取文章链接
        # 机构号页面通常有 "文章" 标签页
        try:
            # 尝试点击"文章"标签
            articles_tab = scraper.page.locator("a[href*='articles'], button:has-text('文章'), [class*='Tab']:has-text('文章')")
            if await articles_tab.count() > 0:
                logger.info("[Zhihu] 点击'文章'标签...")
                await articles_tab.first.click()
                await scraper.page.wait_for_timeout(3000)
                # 再次滚动
                for i in range(10):
                    await scraper.page.evaluate("window.scrollBy(0, 1500)")
                    await scraper.page.wait_for_timeout(2000)
        except:
            logger.info("[Zhihu] 未找到文章标签，继续在当前页面抓取")

        # 查找所有文章/回答链接
        logger.info("[Zhihu] 提取文章链接...")
        seen_urls = set()

        # 使用多种选择器查找文章
        selectors = [
            "a[href*='zhuanlan']",  # 专栏文章
            "a[href*='/p/']",       # 文章
            "a[href*='question']",  # 问题回答
            "[class*='ContentItem'] a",
            "[class*='Item'] a",
        ]

        for selector in selectors:
            try:
                links = await scraper.page.locator(selector).all()
                for link in links:
                    try:
                        href = await link.get_attribute("href")
                        if not href:
                            continue

                        # 处理URL格式
                        if href.startswith("//"):
                            href = f"https:{href}"
                        elif href.startswith("/"):
                            href = f"https://www.zhihu.com{href}"

                        # 跳过非文章链接
                        if not any(x in href for x in ["zhuanlan", "/p/", "question"]):
                            continue

                        # 去重
                        if href in seen_urls:
                            continue
                        seen_urls.add(href)

                        # 获取标题
                        title = ""
                        try:
                            title = await link.text_content() or ""
                            title = title.strip()[:100]
                        except:
                            pass

                        if not title:
                            # 尝试从父元素获取标题
                            try:
                                parent = link.locator("xpath=..")
                                if await parent.count() > 0:
                                    title = await parent.text_content() or ""
                                    title = title.strip()[:100]
                            except:
                                pass

                        articles.append({
                            "title": title or "无标题",
                            "source_url": href,
                            "type": "article" if ("zhuanlan" in href or "/p/" in href) else "question",
                        })

                        logger.info(f"[Zhihu] 找到文章 {len(articles)}: {title[:30] if title else 'N/A'}...")

                        if len(articles) >= max_articles:
                            break

                    except Exception as e:
                        logger.debug(f"[Zhihu] 处理链接失败: {e}")
                        continue

                if len(articles) >= max_articles:
                    break

            except Exception as e:
                logger.debug(f"[Zhihu] 选择器 {selector} 失败: {e}")
                continue

        logger.info(f"[Zhihu] 找到 {len(articles)} 个文章链接")

        # 抓取每个文章的内容
        logger.info("[Zhihu] 开始抓取文章内容...")
        for i, article in enumerate(articles):
            logger.info(f"[Zhihu] 抓取文章 {i+1}/{len(articles)}: {article['title'][:30]}...")

            try:
                url = article["source_url"]

                if "zhuanlan" in url or "/p/" in url:
                    result = await scraper.scrape_article(url)
                else:
                    # 机构号可能是回答，用question方法
                    result = await scraper.scrape_question(url)

                article["content"] = result.get("text", "")
                article["likes"] = result.get("likes", 0)

                # 更新标题（如果抓取到的更准确）
                if result.get("title") and len(result["title"]) > len(article["title"]):
                    article["title"] = result["title"]

                logger.info(f"[Zhihu] 抓取完成: 内容长度 {len(article['content'])} 字符")

                # 等待一下避免反爬虫
                await scraper.page.wait_for_timeout(1500)

            except Exception as e:
                logger.error(f"[Zhihu] 抓取文章失败: {e}")
                article["content"] = ""
                article["likes"] = 0

    return articles


async def scrape_wechat_articles(keyword: str, max_articles: int = 20) -> list:
    """搜索并抓取公众号文章。

    Args:
        keyword: 搜索关键词
        max_articles: 最大抓取数量
    """
    articles = []

    async with WechatScraper(headless=False) as scraper:
        logger.info(f"[Wechat] 搜索公众号文章: {keyword}")

        # 搜索获取文章列表
        search_results = await scraper.search_articles(keyword, max_results=max_articles)

        if not search_results:
            logger.warning("[Wechat] 搜索未找到结果")
            return []

        logger.info(f"[Wechat] 搜索找到 {len(search_results)} 个结果")

        # 抓取每个文章内容
        for i, result in enumerate(search_results):
            logger.info(f"[Wechat] 抓取文章 {i+1}/{len(search_results)}: {result['title'][:30]}...")

            try:
                # 使用 scrape_from_search 方法（避免直接访问跳转链接）
                scraped = await scraper.scrape_from_search(keyword, index=i)

                articles.append({
                    "title": scraped.get("title", result["title"]),
                    "content": scraped.get("text", ""),
                    "author": scraped.get("author", result.get("author", "")),
                    "source_url": scraped.get("source_url", result["source_url"]),
                    "summary": result.get("summary", ""),
                })

                logger.info(f"[Wechat] 抓取完成: 内容长度 {len(articles[-1]['content'])} 字符")

                # 等待避免反爬虫
                await scraper.page.wait_for_timeout(2000)

            except Exception as e:
                logger.error(f"[Wechat] 抓取文章失败: {e}")
                # 尝试直接抓取
                try:
                    scraped = await scraper.scrape_article(result["source_url"])
                    articles.append({
                        "title": scraped.get("title", result["title"]),
                        "content": scraped.get("text", ""),
                        "author": scraped.get("author", result.get("author", "")),
                        "source_url": scraped.get("source_url", result["source_url"]),
                    })
                except:
                    logger.error(f"[Wechat] 直接抓取也失败，跳过")

    return articles


def save_articles(articles: list, platform: str) -> int:
    """保存文章到JSON文件。

    Returns:
        成功保存的文章数量
    """
    output_dir = ZHIHU_DIR if platform == "zhihu" else WECHAT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    saved_count = 0
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    for i, article in enumerate(articles):
        # 过滤内容为空的文章
        if not article.get("content") or len(article["content"]) < 100:
            logger.warning(f"[Save] 跳过内容过短的文章: {article.get('title', 'N/A')[:30]}")
            continue

        # 确定category
        content = article.get("content", "")
        title = article.get("title", "")

        # 自动分类逻辑
        category = "company_intro"  # 默认
        if any(kw in title or kw in content for kw in ["招聘", "校园招聘", "岗位", "求职", "面试"]):
            category = "recruitment"
        elif any(kw in title or kw in content for kw in ["企业文化", "价值观", "团队", "员工"]):
            category = "culture"
        elif any(kw in title or kw in content for kw in ["案例", "客户", "合作", "项目"]):
            category = "success_cases"
        elif any(kw in title or kw in content for kw in ["行业", "趋势", "分析", "洞察", "观点"]):
            category = "industry_insights"
        elif any(kw in title or kw in content for kw in ["培训", "学习", "发展", "成长"]):
            category = "training"
        elif any(kw in title or kw in content for kw in ["新闻", "动态", "公告", "发布"]):
            category = "news"

        # 生成唯一ID
        article_id = f"{platform}_{timestamp}_{i:03d}"

        # 构建保存格式
        data = {
            "id": article_id,
            "title": title[:100],
            "content": content,
            "category": category,
            "source_url": article.get("source_url", ""),
            "author": article.get("author", ""),
            "likes": article.get("likes", 0),
            "publish_date": "",  # 公众号/知乎可能没有明确的发布日期
        }

        # 保存文件
        file_path = output_dir / f"{article_id}.json"
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        saved_count += 1
        logger.info(f"[Save] 保存文章: {file_path.name}")

    return saved_count


async def main():
    """主函数"""
    print("=" * 60)
    print("锐博集团文章抓取与导入")
    print("=" * 60)

    # 1. 抓取知乎机构号文章
    print("\n[步骤1] 抓取知乎机构号文章...")
    zhihu_url = "https://www.zhihu.com/org/rui-bo-ji-tuan-5"
    zhihu_articles = await scrape_zhihu_org(zhihu_url, max_articles=30)

    if zhihu_articles:
        print(f"  找到 {len(zhihu_articles)} 篇知乎文章")
        zhihu_saved = save_articles(zhihu_articles, "zhihu")
        print(f"  保存 {zhihu_saved} 篇有效文章")
    else:
        print("  未找到知乎文章")

    # 2. 抓取公众号文章
    print("\n[步骤2] 抓取公众号文章...")
    wechat_keyword = "锐博集团"
    wechat_articles = await scrape_wechat_articles(wechat_keyword, max_articles=30)

    if wechat_articles:
        print(f"  找到 {len(wechat_articles)} 篇公众号文章")
        wechat_saved = save_articles(wechat_articles, "wechat")
        print(f"  保存 {wechat_saved} 篇有效文章")
    else:
        print("  未找到公众号文章")

    # 3. 导入向量数据库
    print("\n[步骤3] 导入向量数据库...")
    importer = ArticleImporter()

    total_result = {
        "zhihu": ImportResult(),
        "wechat": ImportResult(),
    }

    # 导入知乎
    if zhihu_saved > 0:
        result = importer.import_from_directory(str(ZHIHU_DIR), platform="zhihu")
        total_result["zhihu"] = result
        print(result.generate_report())

    # 导入公众号
    if wechat_saved > 0:
        result = importer.import_from_directory(str(WECHAT_DIR), platform="wechat")
        total_result["wechat"] = result
        print(result.generate_report())

    # 4. 验证
    print("\n[步骤4] 验证导入结果...")
    verify_result = importer.verify_import()
    print(f"  总文档数: {verify_result.get('total_documents', 0)}")

    print("\n" + "=" * 60)
    print("抓取与导入完成！")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())