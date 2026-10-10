from __future__ import annotations

import asyncio
import selectors
import sys
from collections.abc import Coroutine
from typing import Any


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """在 Windows 和 Unix 上使用受支持的事件循环运行异步入口。"""
    if sys.platform == "win32":
        def loop_factory() -> asyncio.AbstractEventLoop:
            return asyncio.SelectorEventLoop(selectors.SelectSelector())

        with asyncio.Runner(loop_factory=loop_factory) as runner:
            return runner.run(coro)
    return asyncio.run(coro)
