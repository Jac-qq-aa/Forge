"""LangGraph graph definitions for the Forge workflow."""

import importlib

# 先导入 state（不依赖其他模块）
from .state import (
    GraphState,
    create_initial_state,
    UnifiedState,
    create_unified_state,
    STAGE_PLANNING,
    STAGE_WAITING_OUTLINE,
    STAGE_TUNING,
    STAGE_COMPLETED,
)

# 导入官方 checkpointer（延迟初始化）
from .checkpointer import get_checkpointer, close_checkpointer

# unified_workflow 需要异步初始化
async def get_unified_workflow_async():
    """获取统一 workflow（带持久化）。"""
    from .unified_workflow import get_unified_workflow
    return await get_unified_workflow()


# 延迟导入 workflow（避免循环导入）
# forge.agents.nodes -> forge.graph.state 是可以的
# 但 forge.graph.workflow -> forge.agents.nodes 会循环
# 使用 importlib.import_module 避免触发 __getattr__ 无限循环
def __getattr__(name):
    if name == "workflow":
        # 使用绝对路径导入，避免触发 __getattr__
        wf_module = importlib.import_module("forge.graph.workflow")
        return wf_module.workflow  # 返回 CompiledStateGraph 变量
    elif name == "create_workflow":
        wf_module = importlib.import_module("forge.graph.workflow")
        return wf_module.create_workflow
    elif name == "visualize_graph":
        wf_module = importlib.import_module("forge.graph.workflow")
        return wf_module.visualize_graph
    elif name == "build_graph":
        wf_module = importlib.import_module("forge.graph.workflow")
        return wf_module.build_graph
    elif name == "unified_workflow":
        raise RuntimeError(
            "unified_workflow is now async. "
            "Use get_unified_workflow_async() or forge.graph.unified_workflow.get_unified_workflow()"
        )
    elif name == "unified_workflow_memory":
        raise RuntimeError(
            "unified_workflow_memory is now async. "
            "Use get_unified_workflow_async(with_checkpointer=False)"
        )
    elif name == "create_unified_workflow":
        uw_module = importlib.import_module("forge.graph.unified_workflow")
        return uw_module.create_unified_workflow
    elif name == "visualize_unified_workflow":
        uw_module = importlib.import_module("forge.graph.unified_workflow")
        return uw_module.visualize_unified_workflow
    raise AttributeError(f"module {__name__} has no attribute {name}")

__all__ = [
    # 快速模式（旧）- 通过 __getattr__ 延迟导入
    "GraphState",
    "create_initial_state",
    "workflow",
    "create_workflow",
    "visualize_graph",
    "build_graph",
    # 统一模式（新）
    "UnifiedState",
    "create_unified_state",
    "get_unified_workflow_async",
    "create_unified_workflow",
    "visualize_unified_workflow",
    # Checkpointer
    "get_checkpointer",
    "close_checkpointer",
    # Stage 常量
    "STAGE_PLANNING",
    "STAGE_WAITING_OUTLINE",
    "STAGE_TUNING",
    "STAGE_COMPLETED",
]