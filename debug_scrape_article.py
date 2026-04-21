"""Debug scrape_article step by step."""

import asyncio
from playwright.async_api import async_playwright

async def debug_scrape():
    url = "https://weixin.sogou.com/link?url=dn9a_-gY295K0Rci_xozVXfdMkSQTLW6cwJThYulHEtVjXrGTiVgS3KZKBG3T9-gQYMjgjCSTVqGqn3WhpcTb0VqXa8Fplpd9AwsytHsQc3JAPN41C0ymKIgio9mTKFJJXdjBXSWN3YjAuiI476GutxGMS2rlkC76O4y90QTOf6ZTVfZxoip7vpCmWSvEHn87JdGFOGHeXy8lqjeIq5zO4F5EDwz3d92lPdgCr9manCHiVbBL6ZqWiGiUiFf9SPXyghqUu0gB3tXRtmyIGw2VAg..&type=2&query=%E4%BA%BA%E5%8A%9B%E8%B5%84%E6%BA%90&token=7F9E4AA9FE8BECF2FBFDB4EEFAD3A594FC38D0C969D89494"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        print(f"[1] 访问搜狗链接...")
        print(f"    URL: {url[:80]}...")

        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        print(f"[2] 当前URL: {page.url}")
        print(f"[3] 页面标题: {await page.title()}")

        # 检查是否在搜狗中间页
        if "sogou.com" in page.url:
            print("[4] 还在搜狗页面，检查跳转按钮...")

            # 查找所有可能的跳转按钮
            selectors = [
                "a[href*='mp.weixin']",
                ".btn-continue",
                "#continue",
                "a.btn",
                "button",
            ]
            for sel in selectors:
                count = await page.locator(sel).count()
                if count > 0:
                    text = await page.locator(sel).first.text_content()
                    href = await page.locator(sel).first.get_attribute("href")
                    print(f"    {sel}: count={count}, text='{text}', href='{href}'")

            # 截图
            await page.screenshot(path="/tmp/sogou_jump.png")
            print("    截图保存: /tmp/sogou_jump.png")

        # 检查是否在微信文章页面
        if "mp.weixin" in page.url or "weixin.qq.com" in page.url:
            print("[5] 已跳转到微信文章页面")

            # 获取标题
            title_selectors = ["#activity-name", ".rich_media_title", "h1"]
            for sel in title_selectors:
                count = await page.locator(sel).count()
                if count > 0:
                    title = await page.locator(sel).first.text_content()
                    print(f"    标题({sel}): '{title}'")

            # 获取内容
            content_selectors = ["#js_content", ".rich_media_content", "article"]
            for sel in content_selectors:
                count = await page.locator(sel).count()
                if count > 0:
                    content = await page.locator(sel).first.text_content()
                    print(f"    内容({sel}): {len(content)} 字")
                    print(f"    预览: {content[:200]}...")

        # 等待用户查看
        await page.wait_for_timeout(10000)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(debug_scrape())