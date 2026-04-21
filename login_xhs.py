"""Manual login script to get valid XHS cookies.

Run this script with headless=False, login manually in the browser window,
then cookies will be saved for future use.
"""

import asyncio
import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

COOKIES_FILE = "/tmp/forge_cookies/xhs_cookies.json"
XHS_URL = "https://www.xiaohongshu.com"


async def manual_login():
    """Open browser for manual login and save cookies."""
    print("=" * 60)
    print("小红书手动登录脚本")
    print("=" * 60)
    print("1. 浏览器窗口将打开")
    print("2. 请在浏览器中手动登录小红书")
    print("3. 如果出现验证码，请完成验证")
    print("4. 登录成功后能看到主页内容，再按 Enter 键保存 cookie")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,  # Show browser window
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
            ]
        )
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='zh-CN',
        )

        await Stealth().apply_stealth_async(context)

        page = await context.new_page()
        page.set_default_timeout(120000)  # 2 minutes timeout

        # Go to XHS homepage
        await page.goto(XHS_URL)
        print(f"\n已打开: {XHS_URL}")
        print("\n⚠️ 重要提示:")
        print("   - 如果出现验证码，请手动完成验证")
        print("   - 登录后请浏览几个页面确认登录状态")
        print("   - 确认能看到内容后再按 Enter")

        # Wait for user input
        input("\n登录完成并确认能看到主页内容后，按 Enter 继续...")

        # Save cookies
        cookies = await context.cookies()
        xhs_cookies = [c for c in cookies if "xiaohongshu" in c.get("domain", "")]

        os.makedirs(os.path.dirname(COOKIES_FILE), exist_ok=True)
        with open(COOKIES_FILE, "w") as f:
            json.dump(xhs_cookies, f)

        print(f"\n已保存 {len(xhs_cookies)} 个 cookies 到: {COOKIES_FILE}")

        # Check if login successful
        web_session = [c for c in xhs_cookies if c.get("name") == "web_session"]
        if web_session:
            print("✓ 检测到 web_session cookie，登录成功！")
        else:
            print("⚠ 未检测到 web_session，请确认是否登录成功")

        await browser.close()
        print("\n完成！现在可以运行 main.py 了。")


if __name__ == "__main__":
    asyncio.run(manual_login())