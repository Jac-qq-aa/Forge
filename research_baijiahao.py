"""Research Baijiahao search and scraping."""

import asyncio
from playwright.async_api import async_playwright

async def research():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        # 百度搜索百家号内容
        # 方法1: 通过百度搜索 site:baijiahao.baidu.com
        print("=== 方法1: 百度搜索百家号 ===")
        url = "https://www.baidu.com/s?wd=人力资源+site%3Abaijiahao.baidu.com"
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        print(f"当前URL: {page.url}")
        print(f"页面标题: {await page.title()}")

        # 截图
        await page.screenshot(path="/tmp/baidu_baijiahao.png")
        print("截图: /tmp/baidu_baijiahao.png")

        # 查找搜索结果
        results = await page.locator("#content_left .result, .c-container").all()
        print(f"\n找到 {len(results)} 个搜索结果")

        for i, r in enumerate(results[:5]):
            try:
                # 获取标题和链接
                title_el = r.locator("h3 a, .t a")
                if await title_el.count() > 0:
                    title = await title_el.first.text_content()
                    href = await title_el.first.get_attribute("href")
                    print(f"\n结果{i+1}:")
                    print(f"  标题: {title[:50] if title else 'N/A'}...")
                    print(f"  链接: {href[:60] if href else 'N/A'}...")

                # 获取摘要
                desc_el = r.locator(".c-abstract, .c-span9, .c-color-text")
                if await desc_el.count() > 0:
                    desc = await desc_el.first.text_content()
                    print(f"  摘要: {desc[:80] if desc else 'N/A'}...")

                # 获取来源
                source_el = r.locator(".c-showurl, .source_1VzA2")
                if await source_el.count() > 0:
                    source = await source_el.first.text_content()
                    print(f"  来源: {source}")

            except Exception as e:
                print(f"  Error: {e}")

        await page.wait_for_timeout(5000)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(research())