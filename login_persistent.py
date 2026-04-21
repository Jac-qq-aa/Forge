"""Login script for persistent browser sessions.

Run this script once to login to platforms. The login state will be persisted
across sessions, avoiding repeated security verifications.
"""

import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# Persistent browser data directories
XHS_DATA_DIR = os.path.expanduser("~/.forge/browser_data/xhs")
ZHIHU_DATA_DIR = os.path.expanduser("~/.forge/browser_data/zhihu")

# Ensure directories exist
Path(XHS_DATA_DIR).mkdir(parents=True, exist_ok=True)
Path(ZHIHU_DATA_DIR).mkdir(parents=True, exist_ok=True)


async def login_xhs():
    """Login to Xiaohongshu with persistent browser context."""
    print("=" * 60)
    print("小红书登录 (持久化模式)")
    print("=" * 60)
    print(f"浏览器数据目录: {XHS_DATA_DIR}")
    print("1. 浏览器窗口将打开")
    print("2. 请手动登录小红书")
    print("3. 如有验证码，请完成验证")
    print("4. 登录成功后浏览几个页面确认")
    print("5. 确认后关闭浏览器窗口或按 Ctrl+C")
    print("=" * 60)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=XHS_DATA_DIR,
            headless=False,
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='zh-CN',
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox'],
        )

        await Stealth().apply_stealth_async(context)

        page = await context.new_page()
        await page.goto("https://www.xiaohongshu.com")

        print("\n✓ 浏览器已打开，请在浏览器中完成登录...")
        print("  登录成功后，请浏览几个页面确认登录状态")
        print("  确认后关闭浏览器窗口或按 Ctrl+C 退出\n")

        # Wait for user to close browser
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            pass

        await context.close()
        print("\n✓ 登录状态已保存！下次运行 main.py 时将自动使用此登录状态。")


async def login_zhihu():
    """Login to Zhihu with persistent browser context."""
    print("=" * 60)
    print("知乎登录 (持久化模式)")
    print("=" * 60)
    print(f"浏览器数据目录: {ZHIHU_DATA_DIR}")
    print("1. 浏览器窗口将打开")
    print("2. 请手动登录知乎")
    print("3. 如有验证码，请完成验证")
    print("4. 登录成功后浏览几个页面确认")
    print("5. 确认后关闭浏览器窗口或按 Ctrl+C")
    print("=" * 60)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=ZHIHU_DATA_DIR,
            headless=False,
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='zh-CN',
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox'],
        )

        await Stealth().apply_stealth_async(context)

        page = await context.new_page()
        await page.goto("https://www.zhihu.com")

        print("\n✓ 浏览器已打开，请在浏览器中完成登录...")
        print("  登录成功后，请浏览几个页面确认登录状态")
        print("  确认后关闭浏览器窗口或按 Ctrl+C 退出\n")

        # Wait for user to close browser
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            pass

        await context.close()
        print("\n✓ 登录状态已保存！下次运行 main.py 时将自动使用此登录状态。")


def main():
    print("\n选择要登录的平台:")
    print("  1. 小红书")
    print("  2. 知乎")
    print("  3. 两个都登录")
    print()

    choice = input("请输入选项 (1/2/3): ").strip()

    if choice == "1":
        asyncio.run(login_xhs())
    elif choice == "2":
        asyncio.run(login_zhihu())
    elif choice == "3":
        print("\n先登录小红书...")
        asyncio.run(login_xhs())
        print("\n再登录知乎...")
        asyncio.run(login_zhihu())
    else:
        print("无效选项")


if __name__ == "__main__":
    main()