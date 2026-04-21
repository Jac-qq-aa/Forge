"""Main entry point for the Forge workflow.

Interactive workflow with article selection and user confirmation.
"""

import asyncio
import logging
import sys

from forge.graph import workflow, create_initial_state


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging for the workflow."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(name)-12s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def print_separator(char: str = "=", length: int = 60):
    """Print a separator line."""
    print(char * length)


def display_articles(articles: list) -> None:
    """Display article list for user selection."""
    print_separator()
    print("搜索到的文章列表:")
    print_separator()

    for i, article in enumerate(articles, 1):
        title = article.get("title", "无标题")[:50]
        summary = article.get("summary", "")[:80]
        source_url = article.get("source_url", "")
        article_type = article.get("type", "unknown")

        type_label = {
            "question": "知乎问答",
            "article": "知乎文章",
            "post": "小红书笔记",
        }.get(article_type, article_type)

        print(f"\n[{i}] {title}")
        print(f"    类型: {type_label}")
        if summary:
            print(f"    摘要: {summary}...")
        print(f"    链接: {source_url}")

    print_separator()


def get_user_choice(max_count: int) -> int:
    """Get user's article selection."""
    while True:
        try:
            choice = input(f"\n请选择要处理的文章序号 (1-{max_count}): ").strip()
            choice_num = int(choice)
            if 1 <= choice_num <= max_count:
                return choice_num
            else:
                print(f"请输入 1 到 {max_count} 之间的数字")
        except ValueError:
            print("请输入有效的数字")
        except KeyboardInterrupt:
            print("\n用户取消操作")
            return -1


def get_user_confirmation(prompt: str) -> bool:
    """Get user confirmation (Y/N)."""
    while True:
        try:
            response = input(f"{prompt} (Y/N): ").strip().upper()
            if response in ["Y", "YES", "是"]:
                return True
            elif response in ["N", "NO", "否"]:
                return False
            else:
                print("请输入 Y 或 N")
        except KeyboardInterrupt:
            print("\n用户取消操作")
            return False


def display_final_script(script_path: str) -> None:
    """Display the generated script content."""
    try:
        with open(script_path, "r", encoding="utf-8") as f:
            content = f.read()
        print_separator()
        print("生成的文案内容:")
        print_separator()
        print(content)
        print_separator()
    except Exception as e:
        print(f"无法读取文案文件: {e}")


async def search_and_select(
    source: str,
    source_type: str = "keyword",
    platform: str = "zhihu",
    max_results: int = 5,
) -> list:
    """Search articles and return list for selection.

    Args:
        source: Keyword or user_id depending on source_type.
        source_type: "keyword" for search, "user" for blogger posts.
        platform: "zhihu" or "xhs".
        max_results: Maximum number of results.

    Returns:
        List of articles.
    """
    logger = logging.getLogger("Forge")

    if platform == "zhihu":
        from forge.tools.zhihu_scraper_persistent import ZhihuScraper
        async with ZhihuScraper() as scraper:
            if source_type == "keyword":
                logger.info(f"在知乎搜索: {source}")
                articles = await scraper.search_articles(source, max_results)
            else:  # user
                logger.info(f"获取知乎用户文章: {source}")
                # For zhihu user posts, we need to implement user post fetching
                # For now, return empty list
                print("知乎博主文章功能暂未实现，请使用关键词搜索")
                return []
    else:  # xhs
        from forge.tools.xhs_scraper_persistent import XhsScraper
        async with XhsScraper() as scraper:
            if source_type == "keyword":
                logger.info(f"在小红书搜索: {source}")
                articles = await scraper.search_articles(source, max_results)
            else:  # user
                logger.info(f"获取小红书博主文章: {source}")
                posts = await scraper.scrape_user_posts(source, max_results)
                # Convert posts to article format
                articles = []
                for post in posts:
                    articles.append({
                        "title": post.get("title", "无标题"),
                        "summary": post.get("text", "")[:200],
                        "source_url": post.get("source_url", ""),
                        "type": "post",
                        "raw_content": post,  # Already have full content
                    })

    return articles


async def process_article(article: dict, target_platform: str) -> dict:
    """Process selected article through the workflow.

    Args:
        article: Selected article with source_url and optionally raw_content.
        target_platform: Target platform for output.

    Returns:
        Workflow result.
    """
    logger = logging.getLogger("Forge")

    # Create state
    state = create_initial_state(article.get("source_url", ""))
    state["target_platform"] = target_platform
    state["skip_publish"] = True  # Always skip actual publishing for now

    # If article already has raw_content (e.g., from user posts), use it
    if article.get("raw_content"):
        state["raw_content"] = article["raw_content"]
        state["source_platform"] = "xhs" if article.get("type") == "post" else "zhihu"

    # Run workflow
    result = await workflow.ainvoke(state)

    return result


async def run_interactive_workflow(
    source: str,
    source_type: str = "keyword",
    source_platform: str = "zhihu",
    target_platform: str = "zhihu_article",
    max_results: int = 5,
) -> None:
    """Run the interactive workflow with article selection.

    Args:
        source: Keyword or user_id.
        source_type: "keyword" for search, "user" for blogger posts.
        source_platform: "zhihu" or "xhs" for source.
        target_platform: "zhihu_article" or "xhs_video" for output.
        max_results: Maximum search results.
    """
    logger = logging.getLogger("Forge")

    print_separator()
    print("Forge 内容转换工作流")
    print_separator()
    print(f"来源平台: {source_platform}")
    print(f"目标平台: {target_platform}")
    print(f"搜索方式: {'关键词搜索' if source_type == 'keyword' else '博主文章'}")
    print(f"搜索内容: {source}")
    print_separator()

    # Step 1: Search articles
    print("\n正在搜索文章...")
    articles = await search_and_select(source, source_type, source_platform, max_results)

    if not articles:
        print("未找到相关文章")
        return

    # Step 2: Display and select
    display_articles(articles)

    if AUTO_SELECT_FIRST:
        print("\n[自动模式] 选择第1篇文章")
        choice = 1
    else:
        choice = get_user_choice(len(articles))
        if choice == -1:
            return

    selected_article = articles[choice - 1]
    print(f"\n已选择: {selected_article.get('title', 'N/A')[:50]}")

    # Step 3: Process article
    print("\n正在处理文章...")
    print_separator()

    result = await process_article(selected_article, target_platform)

    # Step 4: Display result
    script_path = result.get("script_path", "")
    if script_path:
        display_final_script(script_path)

    # Step 5: User confirmation
    print(f"\n文案已保存至: {script_path}")
    print(f"视频路径: {result.get('video_path', '无')}")

    # For now, skip publishing
    print("\n注意: 当前为测试模式，已跳过实际发布")


# ============================================================
# Configuration
# ============================================================

# Source type: "keyword" or "user"
SOURCE_TYPE = "keyword"

# Source content (keyword or user_id)
SOURCE = "人力资源"

# Platform settings
SOURCE_PLATFORM = "zhihu"  # "zhihu" or "xhs"
TARGET_PLATFORM = "zhihu_article"  # "zhihu_article" or "xhs_video"

# Search settings
MAX_RESULTS = 5

# Non-interactive mode: auto-select first article (for testing)
AUTO_SELECT_FIRST = False


def main() -> None:
    """Main entry point."""
    global AUTO_SELECT_FIRST
    setup_logging()

    # Check if running interactively
    import sys
    if not sys.stdin.isatty():
        AUTO_SELECT_FIRST = True

    asyncio.run(run_interactive_workflow(
        source=SOURCE,
        source_type=SOURCE_TYPE,
        source_platform=SOURCE_PLATFORM,
        target_platform=TARGET_PLATFORM,
        max_results=MAX_RESULTS,
    ))


if __name__ == "__main__":
    main()