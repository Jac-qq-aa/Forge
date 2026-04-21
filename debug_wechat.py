"""Debug WeChat search result structure."""

import asyncio
from playwright.async_api import async_playwright

async def debug():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        url = "https://weixin.sogou.com/weixin?type=2&query=人力资源"
        print(f"访问: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        # 获取第一个搜索结果的HTML
        items = await page.locator(".news-list li").all()
        print(f"\n找到 {len(items)} 个结果")

        if items:
            item = items[0]
            html = await item.inner_html()
            print("\n=== 第一个结果的HTML ===")
            print(html[:1500])

            # 尝试各种选择器
            print("\n=== 选择器测试 ===")

            # 标题
            for sel in ["h3 a", ".txt-box h3 a", "a"]:
                count = await item.locator(sel).count()
                if count > 0:
                    text = await item.locator(sel).first.text_content()
                    href = await item.locator(sel).first.get_attribute("href")
                    print(f"  {sel}: text='{text[:30] if text else 'N/A'}...', href='{href[:50] if href else 'N/A'}...'")

            # 作者
            for sel in [".account", ".s-p", ".s-p a", "[class*='account']"]:
                count = await item.locator(sel).count()
                if count > 0:
                    text = await item.locator(sel).first.text_content()
                    print(f"  作者选择器 '{sel}': '{text.strip() if text else 'N/A'}'")

        await page.wait_for_timeout(10000)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(debug())