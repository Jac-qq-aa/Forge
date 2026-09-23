"""固定容量、长驻浏览器的知乎采集池。"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from .config import (
    ZHIHU_BROWSER_POOL_SIZE,
    ZHIHU_GLOBAL_BURST,
    ZHIHU_GLOBAL_REQUESTS_PER_SECOND,
    ZHIHU_PROFILE_ROOT,
)
from .rate_limit import DistributedTokenBucket

logger = logging.getLogger(__name__)


class ZhihuScraperPool:
    """每个槽位独占浏览器配置目录，一次只执行一个页面任务。"""

    def __init__(
        self,
        size: int = ZHIHU_BROWSER_POOL_SIZE,
        *,
        scraper_factory: Callable[..., Any] | None = None,
        limiter: Any | None = None,
        profile_root: str | Path = ZHIHU_PROFILE_ROOT,
    ):
        if size <= 0:
            raise ValueError("知乎浏览器池大小必须大于 0")
        self.size = size
        self._factory = scraper_factory or self._default_factory
        self._limiter = limiter or DistributedTokenBucket(
            "rate-limit:zhihu:global",
            ZHIHU_GLOBAL_REQUESTS_PER_SECOND,
            ZHIHU_GLOBAL_BURST,
        )
        instance_id = os.getenv("ZHIHU_SCRAPER_INSTANCE_ID") or (
            f"{socket.gethostname()}-{os.getpid()}"
        )
        self._profile_root = Path(profile_root) / instance_id
        self._queue: asyncio.Queue[int] = asyncio.Queue(maxsize=size)
        for index in range(size):
            self._queue.put_nowait(index)
        self._scrapers: list[Any | None] = [None] * size
        self._closed = False

    @staticmethod
    def _default_factory(**kwargs):
        from forge.tools.zhihu_scraper_persistent import ZhihuScraper

        return ZhihuScraper(**kwargs)

    async def _scraper(self, index: int):
        scraper = self._scrapers[index]
        if scraper is None:
            profile_dir = self._profile_root / f"slot-{index}"
            scraper = self._factory(headless=True, profile_dir=profile_dir)
            scraper = await scraper.__aenter__()
            self._scrapers[index] = scraper
            logger.info("知乎浏览器槽位已启动 slot=%s", index)
        return scraper

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[Any]:
        if self._closed:
            raise RuntimeError("知乎浏览器池已关闭")
        index = await self._queue.get()
        try:
            await self._limiter.acquire()
            yield await self._scraper(index)
        except Exception:
            await self._discard(index)
            raise
        finally:
            self._queue.put_nowait(index)

    async def _discard(self, index: int) -> None:
        scraper = self._scrapers[index]
        self._scrapers[index] = None
        if scraper is not None:
            try:
                await scraper.__aexit__(None, None, None)
            except Exception:
                logger.exception("关闭异常知乎浏览器槽位失败 slot=%s", index)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await asyncio.gather(
            *(self._discard(index) for index in range(self.size)),
            return_exceptions=True,
        )
