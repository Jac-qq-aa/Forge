"""知乎扫码登录脚本 - 单独保存浏览器缓存"""

import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# 浏览器数据目录
BROWSER_DATA_DIR = os.path.expanduser("~/.forge/browser_data/zhihu")
Path(BROWSER_DATA_DIR).mkdir(parents=True, exist_ok=True)

ZHIHU_BASE_URL = "https://www.zhihu.com"


async def login_zhihu():
    """打开知乎登录页面，等待用户扫码"""
    print("=" * 50)
    print("知乎扫码登录")
    print("=" * 50)
    print(f"缓存目录: {BROWSER_DATA_DIR}")
    print("请在弹出的浏览器窗口中扫码登录...")
    print("登录成功后，按 Ctrl+C 退出即可保存登录状态")
    print("=" * 50)

    playwright = await async_playwright().start()

    # 使用持久化上下文
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=BROWSER_DATA_DIR,
        headless=False,  # 显示界面
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        locale='zh-CN',
        args=[
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
            '--disable-dev-shm-usage',
        ]
    )

    # 应用反检测
    await Stealth().apply_stealth_async(context)

    # 获取或创建页面
    if context.pages:
        page = context.pages[0]
    else:
        page = await context.new_page()

    # 访问知乎首页
    print("\n正在打开知乎首页...")
    await page.goto(ZHIHU_BASE_URL, wait_until="domcontentloaded", timeout=60000)

    # 检查是否已登录
    current_url = page.url
    if "login" in current_url or "signin" in current_url:
        print("检测到登录页面，请扫码登录...")
    else:
        # 尝试访问登录页面
        try:
            await page.click("text=登录", timeout=5000)
            print("点击登录按钮，请扫码...")
        except:
            print("已打开知乎首页，请手动点击登录按钮扫码")

    # 等待用户操作
    print("\n等待登录完成...")
    print("登录成功后，浏览器会自动跳转到首页")
    print("看到首页后，请在此终端按 Ctrl+C 退出并保存状态")

    # 等待用户退出
    try:
        while True:
            await asyncio.sleep(1)
            # 检查是否已登录成功
            try:
                # 查找登录按钮，如果不存在说明已登录
                login_btn = await page.query_selector("text=登录")
                if not login_btn:
                    # 再检查是否有用户头像
                    avatar = await page.query_selector("[class*='Avatar']")
                    if avatar:
                        print("\n✅ 检测到已登录成功！")
                        print("登录状态已自动保存到缓存目录")
                        print("请按 Ctrl+C 退出...")
            except:
                pass
    except KeyboardInterrupt:
        print("\n\n正在保存浏览器状态...")

    # 关闭浏览器
    await context.close()
    await playwright.stop()

    print("✅ 浏览器状态已保存！")
    print(f"缓存位置: {BROWSER_DATA_DIR}")
    print("\n现在可以在 Forge 平台使用知乎功能了")


if __name__ == "__main__":
    asyncio.run(login_zhihu())