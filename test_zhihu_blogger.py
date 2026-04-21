"""Test zhihu scraper for blogger mode after fix."""

import asyncio
import sys
sys.path.insert(0, "/home/hugo/Forge")

from forge.tools.zhihu_scraper_persistent import ZhihuScraper

async def test():
    print("Starting ZhihuScraper...")
    try:
        async with ZhihuScraper(headless=False) as scraper:
            user_id = "zhang-jia-wei"
            print(f"\nTesting get_user_articles for: {user_id}")

            articles = await scraper.get_user_articles(user_id, max_results=5)
            print(f"\nFound {len(articles)} articles:")
            for a in articles:
                print(f"  - {a['title'][:60]}...")
                print(f"    URL: {a['source_url']}")
                print(f"    Summary: {a['summary'][:80]}...")
                print()

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())