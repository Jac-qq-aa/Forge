"""深度生成模式模块 - 多智能体协作内容生成系统。"""

from forge.deep_mode.session_state import (
    DeepModeSession,
    SessionStage,
    TuningMessage,
)
from forge.deep_mode.errors import (
    DeepModeError,
    SessionNotFoundError,
    InvalidStageError,
    OutlineRevisionLimitError,
    AgentTimeoutError,
    RAGSearchFailedError,
)
from forge.deep_mode.session_manager import (
    SessionManager,
    get_session_manager,
)

# Workflow exports (deer-flow pattern)
from forge.deep_mode.workflow import (
    DeepModeState,
    run_plan_execute_workflow,
    run_executor_only,
    run_tuning_agent,
    run_plan_execute,  # API-compatible function
    get_tuning_agent,
    # Node functions
    rag_search,
    generate_outline,
    revise_outline,
    generate_content,
)

# Compatibility alias
WorkflowState = DeepModeState

__all__ = [
    "DeepModeSession",
    "SessionStage",
    "TuningMessage",
    "DeepModeError",
    "SessionNotFoundError",
    "InvalidStageError",
    "OutlineRevisionLimitError",
    "AgentTimeoutError",
    "RAGSearchFailedError",
    "SessionManager",
    "get_session_manager",
    # Workflow (deer-flow pattern)
    "DeepModeState",
    "WorkflowState",  # compatibility alias
    "run_plan_execute_workflow",
    "run_executor_only",
    "run_tuning_agent",
    "run_plan_execute",
    "get_tuning_agent",
    "rag_search",
    "generate_outline",
    "revise_outline",
    "generate_content",
]