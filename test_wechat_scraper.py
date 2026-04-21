"""Test WeChat scraper."""

import asyncio
import sys
sys.path.insert(0, "/home/hugo/Forge")

from forge.tools.wechat_scraper import WechatScraper

async def test():
    async with WechatScraper(headless=False) as scraper:
        # 测试搜索文章
        print("\n=== 测试搜索文章 ===")
        articles = await scraper.search_articles("人力资源", max_results=5)
        print(f"找到 {len(articles)} 篇文章:")
        for a in articles:
            print(f"  - [{a['author']}] {a['title'][:40]}...")
            print(f"    URL: {a['source_url'][:60]}...")

        # 测试抓取第一篇文章
        if articles:
            print("\n=== 测试抓取文章内容 ===")
            content = await scraper.scrape_article(articles[0]["source_url"])
            print(f"标题: {content['title']}")
            print(f"作者: {content['author']}")
            print(f"内容长度: {len(content['text'])} 字")
            print(f"内容预览: {content['text'][:200]}...")

if __name__ == "__main__":
    asyncio.run(test())