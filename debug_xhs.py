"""Debug script to analyze XHS page structure."""

import asyncio
import json
import os
import re
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

USER_ID = "5f1c7e6e0000000001003c8c"  # 不同博主测试
XHS_URL = f"https://www.xiaohongshu.com/user/profile/{USER_ID}"

async def debug_page():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
            ]
        )
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='zh-CN',
        )

        # Apply stealth to context
        await Stealth().apply_stealth_async(context)
        print("Stealth mode applied")

        page = await context.new_page()
        page.set_default_timeout(30000)

        # Load cookies if exist
        cookie_file = "/tmp/forge_cookies/xhs_cookies.json"
        if os.path.exists(cookie_file):
            with open(cookie_file, "r") as f:
                cookies = json.load(f)
                await context.add_cookies(cookies)
                print(f"Loaded {len(cookies)} cookies")

        print(f"Loading: {XHS_URL}")
        await page.goto(XHS_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

        # Try to close popup
        try:
            close_btns = await page.locator(".reds-alert .close-icon, .reds-button-new.text").all()
            for btn in close_btns:
                text = await btn.text_content()
                if text and ("关闭" in text or "取消" in text or "暂不" in text):
                    await btn.click()
                    print(f"Closed popup: {text}")
                    await page.wait_for_timeout(1000)
                    break
        except Exception as e:
            print(f"Popup handling: {e}")

        # Scroll to trigger lazy loading
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(3000)

        # Save screenshot
        await page.screenshot(path="debug_full.png", full_page=True)
        print("Screenshot saved: debug_full.png")

        # Get page title
        title = await page.title()
        print(f"Page title: {title}")

        # Get all links
        all_links = await page.locator("a").all()
        print(f"Total links on page: {len(all_links)}")

        # Analyze each link
        hrefs = []
        for link in all_links[:50]:
            try:
                href = await link.get_attribute("href")
                text = await link.text_content()
                if href:
                    hrefs.append((href[:60], text[:30] if text else ""))
            except:
                pass

        print("\nAll links (first 50):")
        for href, text in hrefs:
            print(f"  {href}... | {text}")

        # Get page HTML and search for post references
        html = await page.content()

        # Search for noteId patterns in HTML
        note_ids = re.findall(r'noteId["\s:=]+(["\']?)([a-f0-9]{24})', html)
        print(f"\nNote IDs found in HTML: {len(note_ids)}")
        for nid in note_ids[:10]:
            print(f"  {nid[1]}")

        # Search for explore patterns
        explores = re.findall(r'/explore/[a-f0-9]{24}', html)
        print(f"\nExplore URLs found: {len(explores)}")
        for exp in explores[:10]:
            print(f"  {exp}")

        # Save HTML
        with open("debug_html.html", "w") as f:
            f.write(html)
        print("\nHTML saved: debug_html.html")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(debug_page())