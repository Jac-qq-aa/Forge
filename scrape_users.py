"""Scrape posts from specific Xiaohongshu bloggers.

Usage:
    python scrape_users.py

Configure USER_IDS below with the blogger IDs you want to scrape.
"""

import asyncio
import logging
import sys

from forge.tools.xhs_scraper import XhsScraper

# ============================================================
# Configure your blogger IDs here
# ============================================================
# You can find user ID from URLs like:
# https://www.xiaohongshu.com/user/profile/5f1c7e6e0000000001003c8c
# The ID is: 5f1c7e6e0000000001003c8c

USER_IDS = [
    # Example user IDs (replace with real ones)
    # "5f1c7e6e0000000001003c8c",
    # "5fe2e9e60000000001006c9a",
]

# Max posts to scrape per user
MAX_POSTS_PER_USER = 3


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


async def scrape_all_users():
    """Scrape posts from all configured users."""
    if not USER_IDS:
        print("=" * 60)
        print("请在 USER_IDS 列表中添加博主ID")
        print("=" * 60)
        print("\n如何获取博主ID:")
        print("1. 打开小红书博主主页")
        print("2. URL格式: https://www.xiaohongshu.com/user/profile/博主ID")
        print("3. 复制 URL 中的 ID 部分")
        print("\n示例:")
        print('USER_IDS = ["5f1c7e6e0000000001003c8c", "5fe2e9e60000000001006c9a"]')
        return []

    logger = logging.getLogger("Scraper")
    all_posts = []

    async with XhsScraper() as scraper:
        for user_id in USER_IDS:
            logger.info(f"正在爬取博主: {user_id}")
            try:
                posts = await scraper.scrape_user_posts(user_id, MAX_POSTS_PER_USER)
                for post in posts:
                    post["author_id"] = user_id
                all_posts.extend(posts)
                logger.info(f"博主 {user_id} 爬取完成，获取 {len(posts)} 篇帖子")
            except Exception as e:
                logger.error(f"爬取博主 {user_id} 失败: {e}")

    return all_posts


def main():
    setup_logging()
    posts = asyncio.run(scrape_all_users())

    if posts:
        print("\n" + "=" * 60)
        print(f"总共爬取 {len(posts)} 篇帖子")
        print("=" * 60)

        for i, post in enumerate(posts, 1):
            print(f"\n--- 帖子 {i} ---")
            print(f"博主: {post.get('author_id', 'N/A')}")
            print(f"标题: {post.get('title', 'N/A')}")
            print(f"点赞: {post.get('likes', 0)}")
            print(f"图片: {len(post.get('images', []))} 张")
            print(f"链接: {post.get('source_url', 'N/A')}")
            print(f"内容预览: {post.get('text', '')[:100]}...")


if __name__ == "__main__":
    main()