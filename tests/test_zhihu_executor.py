"""知乎任务执行器的缓存与输入边界。"""

import pytest

from forge.scraping import zhihu_executor


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/admin",
        "https://zhihu.com.evil.example/question/1",
        "https://example.com/article",
    ],
)
def test_zhihu_url_validation_rejects_non_zhihu_hosts(url):
    with pytest.raises(ValueError, match="zhihu.com"):
        zhihu_executor._require_zhihu_url(url)


def test_zhihu_url_validation_accepts_official_subdomains():
    url = "https://www.zhihu.com/question/123"
    assert zhihu_executor._require_zhihu_url(url) == url
    column_url = "https://zhuanlan.zhihu.com/p/123"
    assert zhihu_executor._require_zhihu_url(column_url) == column_url


def test_login_redirect_is_treated_as_retryable_scrape_failure():
    scraper = type(
        "Scraper",
        (),
        {"page": type("Page", (), {"url": "https://www.zhihu.com/signin"})()},
    )()
    with pytest.raises(RuntimeError, match="人机验证"):
        zhihu_executor._raise_if_blocked(scraper)


@pytest.mark.asyncio
async def test_cached_result_skips_browser_execution(monkeypatch):
    async def cached(_key):
        return {"success": True, "articles": [{"title": "缓存结果"}]}

    def pool_must_not_start():
        raise AssertionError("命中缓存时不应启动浏览器")

    monkeypatch.setattr(zhihu_executor, "_get_cached", cached)
    monkeypatch.setattr(zhihu_executor, "get_zhihu_pool", pool_must_not_start)

    result = await zhihu_executor.execute_zhihu_job(
        {
            "operation": "zhihu_search",
            "payload": {"source": "测试", "max_results": 5, "search_mode": "keyword"},
        }
    )

    assert result["cached"] is True
    assert result["articles"][0]["title"] == "缓存结果"
