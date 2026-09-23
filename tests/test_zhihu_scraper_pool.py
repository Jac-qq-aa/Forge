"""知乎浏览器池的容量、复用和故障回收契约。"""

import asyncio
from pathlib import Path

import pytest

from forge.scraping.zhihu_pool import ZhihuScraperPool


class FakeLimiter:
    def __init__(self):
        self.calls = 0

    async def acquire(self):
        self.calls += 1


class FakeScraper:
    def __init__(self, *, profile_dir, **_kwargs):
        self.profile_dir = Path(profile_dir)
        self.entered = 0
        self.closed = 0

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, *_args):
        self.closed += 1


@pytest.mark.asyncio
async def test_pool_reuses_long_lived_scraper_and_applies_limit(tmp_path):
    created = []
    limiter = FakeLimiter()

    def factory(**kwargs):
        scraper = FakeScraper(**kwargs)
        created.append(scraper)
        return scraper

    pool = ZhihuScraperPool(
        1,
        scraper_factory=factory,
        limiter=limiter,
        profile_root=tmp_path,
    )
    async with pool.lease() as first:
        pass
    async with pool.lease() as second:
        pass

    assert first is second
    assert len(created) == 1
    assert limiter.calls == 2
    await pool.close()
    assert created[0].closed == 1


@pytest.mark.asyncio
async def test_pool_never_leases_more_than_configured_capacity(tmp_path):
    limiter = FakeLimiter()
    active = 0
    maximum_active = 0
    lock = asyncio.Lock()

    async def use_slot(pool):
        nonlocal active, maximum_active
        async with pool.lease():
            async with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.02)
            async with lock:
                active -= 1

    pool = ZhihuScraperPool(
        2,
        scraper_factory=FakeScraper,
        limiter=limiter,
        profile_root=tmp_path,
    )
    await asyncio.gather(*(use_slot(pool) for _ in range(6)))

    assert maximum_active == 2
    assert len({scraper.profile_dir for scraper in pool._scrapers}) == 2
    await pool.close()


@pytest.mark.asyncio
async def test_failed_lease_discards_browser_before_next_request(tmp_path):
    created = []

    def factory(**kwargs):
        scraper = FakeScraper(**kwargs)
        created.append(scraper)
        return scraper

    pool = ZhihuScraperPool(
        1,
        scraper_factory=factory,
        limiter=FakeLimiter(),
        profile_root=tmp_path,
    )
    with pytest.raises(RuntimeError, match="page crashed"):
        async with pool.lease():
            raise RuntimeError("page crashed")
    async with pool.lease() as replacement:
        pass

    assert len(created) == 2
    assert created[0].closed == 1
    assert replacement is created[1]
    await pool.close()
