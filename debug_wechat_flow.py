"""Debug WeChat article scraping flow."""

import asyncio
import sys
sys.path.insert(0, "/home/hugo/Forge")

from forge.tools.wechat_scraper import WechatScraper

async def debug():
    async with WechatScraper(headless=False) as scraper:
        # 1. 搜索文章
        print("=== 步骤1: 搜索文章 ===")
        articles = await scraper.search_articles("人力资源", max_results=3)
        print(f"找到 {len(articles)} 篇文章")
        for i, a in enumerate(articles):
            print(f"\n文章{i+1}:")
            print(f"  标题: {a['title']}")
            print(f"  作者: {a['author']}")
            print(f"  URL: {a['source_url'][:80]}...")

        if articles:
            # 2. 抓取第一篇
            print("\n=== 步骤2: 抓取文章内容 ===")
            url = articles[0]["source_url"]
            print(f"访问URL: {url}")

            content = await scraper.scrape_article(url)
            print(f"\n抓取结果:")
            print(f"  标题: {content['title']}")
            print(f"  作者: {content['author']}")
            print(f"  内容长度: {len(content['text'])} 字")
            print(f"  内容预览: {content['text'][:300]}...")

            # 检查是否抓取成功
            if not content['title'] and not content['text']:
                print("\n❌ 抓取失败！检查页面...")
                # 截图
                await scraper.page.screenshot(path="/tmp/wechat_debug.png")
                print("截图保存到: /tmp/wechat_debug.png")

                # 打印当前URL
                print(f"当前页面URL: {scraper.page.url}")

                # 打印页面标题
                page_title = await scraper.page.title()
                print(f"页面标题: {page_title}")

if __name__ == "__main__":
    asyncio.run(debug())