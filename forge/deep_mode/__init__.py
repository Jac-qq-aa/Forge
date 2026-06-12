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

# Paragraph generation (LongWriter AgentWrite style)
from forge.deep_mode.paragraph_generator import (
    ParagraphGenerator,
    ParagraphTask,
    get_paragraph_generator,
    generate_article_with_paragraphs,
    estimate_target_length,
    parse_paragraph_outline,
)
from forge.deep_mode.paragraph_cache import (
    ParagraphCache,
    get_paragraph_cache,
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
    # Tuning tools - basic
    rewrite_section,
    adjust_tone,
    check_fact,
    search_knowledge,
    # Tuning tools - enhanced (paragraph-level)
    expand_paragraph,
    shrink_paragraph,
    merge_paragraphs,
    split_paragraph,
    check_coherence,
    get_word_count,
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
    # Paragraph generation
    "ParagraphGenerator",
    "ParagraphTask",
    "get_paragraph_generator",
    "generate_article_with_paragraphs",
    "estimate_target_length",
    "parse_paragraph_outline",
    "ParagraphCache",
    "get_paragraph_cache",
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
    # Tuning tools - basic
    "rewrite_section",
    "adjust_tone",
    "check_fact",
    "search_knowledge",
    # Tuning tools - enhanced
    "expand_paragraph",
    "shrink_paragraph",
    "merge_paragraphs",
    "split_paragraph",
    "check_coherence",
    "get_word_count",
]