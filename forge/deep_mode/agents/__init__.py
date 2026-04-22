# forge/deep_mode/agents/__init__.py

"""深度生成模式 Agent 模块 - 已迁移到 workflow.py。

旧版 plan_execute_agent.py 和 react_agent.py 已删除。
新版使用 LangGraph create_agent + AgentState 实现（deer-flow 模式）。

请使用 forge.deep_mode.workflow 中的函数：
- run_plan_execute() - 运行指定阶段
- run_plan_execute_workflow() - 运行完整的 Plan-Execute 流程
- run_executor_only() - 只运行 Executor 生成全文
- run_tuning_agent() - 运行微调 Agent
"""

# 从 workflow 导出以保持兼容性
from forge.deep_mode.workflow import (
    run_plan_execute_workflow,
    run_executor_only,
    run_tuning_agent,
    run_plan_execute,  # API-compatible function
    TuningAgentFallback,  # Deprecated but kept for compatibility
)

__all__ = [
    "run_plan_execute_workflow",
    "run_executor_only",
    "run_tuning_agent",
    "run_plan_execute",
    "TuningAgentFallback",
]