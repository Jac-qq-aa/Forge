"""Test WeChat search via Sogou."""

import asyncio
from playwright.async_api import async_playwright

async def test_sogou_wechat():
    print("测试搜狗微信搜索...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        # 访问搜狗微信搜索
        url = "https://weixin.sogou.com/weixin?type=2&query=人力资源"
        print(f"访问: {url}")

        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)

        # 检查当前URL（看是否被重定向或需要验证码）
        current_url = page.url
        print(f"当前URL: {current_url}")

        # 截图
        await page.screenshot(path="/tmp/sogou_wechat.png")
        print("截图保存: /tmp/sogou_wechat.png")

        # 检查是否有验证码
        has_captcha = await page.locator("#seccodeDialog, .verify-wrap, [class*='captcha']").count() > 0
        print(f"验证码检测: {'有' if has_captcha else '无'}")

        # 尝试获取搜索结果
        results = await page.locator(".news-box, .news-list li, [class*='result']").all()
        print(f"搜索结果数: {len(results)}")

        if results:
            for i, r in enumerate(results[:3]):
                try:
                    text = await r.text_content()
                    print(f"\n结果{i+1}: {text[:100]}...")
                except:
                    pass

        # 等待用户查看
        await page.wait_for_timeout(10000)

        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_sogou_wechat())