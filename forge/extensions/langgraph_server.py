"""LangGraph Server 扩展入口；初版本地 worker 不加载此模块。"""

from forge.bootstrap import build_runtime

_runtime = None


async def graph(_config=None):
    global _runtime
    if _runtime is None:
        _runtime = await build_runtime(with_graph=True)
    return _runtime.compiled_graph


async def shutdown() -> None:
    global _runtime
    if _runtime is not None:
        await _runtime.close()
        _runtime = None
