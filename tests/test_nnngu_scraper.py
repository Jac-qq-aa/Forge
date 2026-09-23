"""Tests for precise Ruibo website list extraction."""

from bs4 import BeautifulSoup

from forge.tools.nnngu_scraper import NnnguScraper


def test_extracts_primary_list_and_excludes_hot_sidebar():
    soup = BeautifulSoup(
        """
        <div class="news-content-view">
          <a href="/article/100.html">正文文章</a>
        </div>
        <div class="news-hot-list">
          <a href="/article/999.html">侧边栏推荐</a>
        </div>
        """,
        "html.parser",
    )

    assert NnnguScraper._extract_list_article_ids(soup) == ["100"]
