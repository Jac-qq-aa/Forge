"""统一 Workflow - 融合快速模式和深度模式的 StateGraph。

架构：
- 一个统一的 workflow 图
- 通过 mode 参数路由：mode="fast" 或 mode="deep"
- 快速模式：Editor → AI_Detector → [循环] → Reviewer → Director
- 深度模式：Outline → HumanReview → Content → Tuning → Director
- 状态持久化：Redis + PG Checkpointer
- 用户交互：interrupt_before 在关键节点暂停

使用方式：
```python
from forge.graph.unified_workflow import unified_workflow

# 快速模式
result = await unified_workflow.ainvoke(
    {"mode": "fast", "topic": "https://zhihu.com/...", ...},
    config={"configurable": {"thread_id": session_id}}
)

# 深度模式
result = await unified_workflow.ainvoke(
    {"mode": "deep", "raw_content": {...}, "user_input": "...", ...},
    config={"configurable": {"thread_id": session_id}}
)
# 在 human_review 前暂停，等待用户决策
result = await unified_workflow.ainvoke(
    {"human_decision": "accept"},
    config={"configurable": {"thread_id": session_id}}
)
```
"""

import logging
from typing import Literal, Optional

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

from forge.graph.state import UnifiedState

# 快速模式节点
from forge.agents.nodes import (
    scout_node,
    editor_node,
    ai_detector_node,
    humanizer_editor_node,
    reviewer_node,
    director_node,
    video_generator_node,
    publisher_node,
)

# 深度模式节点
from forge.agents.deep_nodes import (
    deep_outline_generator_node,
    human_review_node,
    deep_outline_reviser_node,
    tuning_agent_node,
    finalize_node,
    route_after_human_review,
    route_after_tuning,
)

# Research + Reflection 节点（新增）
from forge.agents.research_agent import run_research_agent
from forge.agents.reflection_writer import run_reflection_writer

# 配置
from forge.config import AI_THRESHOLD, MAX_HUMANIZE_REVISIONS, MAX_REVISIONS

# 评估探针装饰器
from forge.evaluation.probe_decorator import with_probe

logger = logging.getLogger(__name__)


# ============================================================================
# Research + Reflection 节点（深度模式新增）
# ============================================================================

async def research_agent_node(state: UnifiedState) -> dict:
    """Research Agent 节点 - 收集素材生成事实清单。

    输入：
    - outline: 大纲
    - raw_content: 原文章
    - user_input: 用户需求
    - rag_context: RAG 素材

    输出：
    - fact_sheet: 事实清单
    """
    outline = state.get("outline", "")
    raw_content = state.get("raw_content", {})
    user_input = state.get("user_input", "")
    rag_context = state.get("rag_context", "")

    logger.info("[ResearchAgent] Starting research...")

    try:
        fact_sheet = await run_research_agent(
            outline=outline,
            raw_content=raw_content,
            user_input=user_input,
            rag_context=rag_context,
        )
        logger.info(f"[ResearchAgent] Fact sheet: {len(fact_sheet)} chars")
        return {"fact_sheet": fact_sheet}

    except Exception as e:
        logger.error(f"[ResearchAgent] Failed: {e}")
        # Fallback：使用大纲和 RAG 素材作为事实清单
        return {
            "fact_sheet": f"## 大纲\n{outline}\n\n## RAG素材\n{rag_context}",
        }


# 包装带探针的节点
research_agent_node = with_probe("research_agent")(research_agent_node)


async def reflection_writer_node(state: UnifiedState) -> dict:
    """Reflection Writer 节点 - Generator + Critic 循环写作。

    输入：
    - fact_sheet: 事实清单
    - raw_content: 原文章
    - target_platform: 目标平台
    - user_input: 用户需求

    输出：
    - current_draft: 当前草稿
    - rewritten_draft: 兼容字段
    - draft_v1: 初版草稿
    """
    fact_sheet = state.get("fact_sheet", "")
    raw_content = state.get("raw_content", {})
    target_platform = state.get("target_platform", "zhihu_article")
    user_input = state.get("user_input", "")

    logger.info("[ReflectionWriter] Starting writing...")

    try:
        draft = await run_reflection_writer(
            fact_sheet=fact_sheet,
            raw_content=raw_content,
            target_platform=target_platform,
            user_input=user_input,
        )
        logger.info(f"[ReflectionWriter] Draft: {len(draft)} chars")
        return {
            "current_draft": draft,
            "rewritten_draft": draft,  # 兼容后续节点
            "draft_v1": draft,
            "stage": "tuning",
        }

    except Exception as e:
        logger.error(f"[ReflectionWriter] Failed: {e}")
        return {
            "current_draft": f"写作失败: {e}",
            "rewritten_draft": f"写作失败: {e}",
            "stage": "tuning",
        }


# 包装带探针的节点（带循环类型）
reflection_writer_node = with_probe("reflection_writer", loop_type="reflection_loop")(reflection_writer_node)


# ============================================================================
# 路由函数
# ============================================================================

def route_by_mode(state: UnifiedState) -> Literal["fast", "deep"]:
    """模式路由 - 根据 mode 字段选择分支。

    Returns:
        "fast": 快速改写分支
        "deep": 深度生成分支
    """
    mode = state.get("mode", "fast")
    logger.info(f"[Route] Mode: {mode}")
    return mode


def route_after_ai_detector(state: UnifiedState) -> Literal["humanizer_editor", "reviewer"]:
    """AI 检测后的路由（快速模式）。

    路由逻辑：
    - ai_score > AI_THRESHOLD AND humanize_revisions < MAX → humanizer_editor
    - ai_score <= AI_THRESHOLD OR 达到最大迭代 → reviewer

    Returns:
        "humanizer_editor": 需要人性化改写
        "reviewer": 通过检测或达到最大迭代
    """
    ai_score = state.get("ai_score", 0.0)
    humanize_revisions = state.get("humanize_revisions", 0)

    logger.info(f"[Route] AI_Detector: score={ai_score:.2f}, revisions={humanize_revisions}")

    needs_humanization = ai_score > AI_THRESHOLD
    can_continue = humanize_revisions < MAX_HUMANIZE_REVISIONS

    if needs_humanization and can_continue:
        logger.info("[Route] → Humanizer_Editor")
        return "humanizer_editor"

    logger.info("[Route] → Reviewer")
    return "reviewer"


def route_after_review(state: UnifiedState) -> Literal["director", "editor"]:
    """审核后的路由（快速模式）。

    路由逻辑：
    - 有反馈 AND revision_count < MAX → editor（重新改写）
    - 通过审核 OR 达到最大迭代 → director

    Returns:
        "editor": 需要重新改写
        "director": 通过审核，进入输出
    """
    final_script = state.get("final_script", "")
    reflection_feedback = state.get("reflection_feedback", "")
    revision_count = state.get("revision_count", 0)

    logger.info(f"[Route] Reviewer: revision_count={revision_count}, has_feedback={bool(reflection_feedback)}")

    # 需要修改
    if reflection_feedback and revision_count < MAX_REVISIONS:
        logger.info("[Route] → Editor (revision needed)")
        return "editor"

    # 通过审核
    logger.info("[Route] → Director (approved)")
    return "director"


# ============================================================================
# 构建图
# ============================================================================

def build_unified_graph() -> StateGraph:
    """构建统一 StateGraph。

    结构：
    START → Scout → mode_router → [fast/deep 分支] → Director → VideoGenerator → Publisher → END
    """
    logger.info("[UnifiedWorkflow] Building graph...")

    graph = StateGraph(UnifiedState)

    # ===== 共享节点 =====
    graph.add_node("scout", scout_node)

    # ===== 模式路由（占位节点） =====
    # 使用 lambda 返回原状态，实际路由通过 conditional_edges 实现
    graph.add_node("mode_router", lambda s: s)

    # ===== 快速模式分支 =====
    graph.add_node("editor", editor_node)
    graph.add_node("ai_detector", ai_detector_node)
    graph.add_node("humanizer_editor", humanizer_editor_node)
    graph.add_node("reviewer", reviewer_node)

    # ===== 深度模式分支 =====
    graph.add_node("deep_outline_generator", deep_outline_generator_node)
    graph.add_node("human_review", human_review_node)  # interrupt 点
    graph.add_node("deep_outline_reviser", deep_outline_reviser_node)

    # Research + Reflection 节点（新增）
    graph.add_node("research_agent", research_agent_node)
    graph.add_node("reflection_writer", reflection_writer_node)

    graph.add_node("tuning_agent", tuning_agent_node)  # interrupt 点
    graph.add_node("finalize", finalize_node)

    # ===== 共享输出节点 =====
    graph.add_node("director", director_node)
    graph.add_node("video_generator", video_generator_node)
    graph.add_node("publisher", publisher_node)

    # ===== 边：START → Scout → mode_router =====
    graph.add_edge(START, "scout")
    graph.add_edge("scout", "mode_router")

    # ===== 模式路由 =====
    graph.add_conditional_edges(
        "mode_router",
        route_by_mode,
        {
            "fast": "editor",
            "deep": "deep_outline_generator",
        }
    )

    # ===== 快速模式分支 =====
    # Editor → AI_Detector → [条件路由]
    graph.add_edge("editor", "ai_detector")
    graph.add_conditional_edges(
        "ai_detector",
        route_after_ai_detector,
        {
            "humanizer_editor": "humanizer_editor",
            "reviewer": "reviewer",
        }
    )
    # Humanizer → AI_Detector（循环）
    graph.add_edge("humanizer_editor", "ai_detector")
    # Reviewer → [条件路由]
    graph.add_conditional_edges(
        "reviewer",
        route_after_review,
        {
            "director": "director",
            "editor": "editor",
        }
    )

    # ===== 深度模式分支 =====
    # OutlineGenerator → HumanReview (interrupt)
    graph.add_edge("deep_outline_generator", "human_review")
    # HumanReview → [条件路由]
    graph.add_conditional_edges(
        "human_review",
        route_after_human_review,
        {
            "accept": "research_agent",  # 先收集素材（新增）
            "modify": "deep_outline_reviser",
            "finalize": "finalize",
        }
    )
    # OutlineReviser → HumanReview（循环）
    graph.add_edge("deep_outline_reviser", "human_review")

    # Research → Reflection → TuningAgent（新增流程）
    graph.add_edge("research_agent", "reflection_writer")
    graph.add_edge("reflection_writer", "tuning_agent")
    # TuningAgent → [条件路由]
    graph.add_conditional_edges(
        "tuning_agent",
        route_after_tuning,
        {
            "finalize": "finalize",
            "director": "director",
        }
    )
    # Finalize → Director
    graph.add_edge("finalize", "director")

    # ===== 输出分支 =====
    graph.add_edge("director", "video_generator")
    graph.add_edge("video_generator", "publisher")
    graph.add_edge("publisher", END)

    logger.info("[UnifiedWorkflow] Graph built successfully")
    return graph


# ============================================================================
# 编译 Workflow
# ============================================================================

async def create_unified_workflow(
    with_checkpointer: bool = True,
    interrupt_nodes: list[str] = None,
) -> CompiledStateGraph:
    """创建并编译统一 Workflow。

    Args:
        with_checkpointer: 是否启用持久化（默认 True）
        interrupt_nodes: 指定暂停节点（默认 ["human_review", "tuning_agent"]）

    Returns:
        编译后的 StateGraph，可通过 ainvoke 执行
    """
    graph = build_unified_graph()

    # 默认暂停节点
    if interrupt_nodes is None:
        interrupt_nodes = ["human_review", "tuning_agent"]

    # 配置 checkpointer（使用官方 AsyncPostgresSaver）
    checkpointer = None
    if with_checkpointer:
        try:
            from forge.graph.checkpointer import get_checkpointer
            checkpointer = await get_checkpointer()
            logger.info("[UnifiedWorkflow] Official AsyncPostgresSaver enabled")
        except Exception as e:
            logger.warning(f"[UnifiedWorkflow] Checkpointer init failed: {e}, using memory saver")
            from langgraph.checkpoint.memory import MemorySaver
            checkpointer = MemorySaver()

    # 编译
    workflow = graph.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_nodes,
    )

    logger.info(f"[UnifiedWorkflow] Workflow compiled with interrupt_before={interrupt_nodes}")
    return workflow


# ============================================================================
# 全局实例（延迟初始化）
# ============================================================================

_unified_workflow: Optional[CompiledStateGraph] = None
_unified_workflow_memory: Optional[CompiledStateGraph] = None


async def get_unified_workflow() -> CompiledStateGraph:
    """获取统一 workflow（带持久化）。

    延迟初始化，首次调用时创建。
    """
    global _unified_workflow
    if _unified_workflow is None:
        _unified_workflow = await create_unified_workflow(with_checkpointer=True)
    return _unified_workflow


async def get_unified_workflow_memory() -> CompiledStateGraph:
    """获取统一 workflow（无持久化）。

    用于测试或纯内存场景。
    """
    global _unified_workflow_memory
    if _unified_workflow_memory is None:
        _unified_workflow_memory = await create_unified_workflow(with_checkpointer=False)
    return _unified_workflow_memory


# ============================================================================
# 辅助函数
# ============================================================================

async def visualize_unified_workflow(output_path: str = None) -> str:
    """生成 ASCII 可视化图。"""
    try:
        workflow = await get_unified_workflow()
        ascii_graph = workflow.get_graph().draw_ascii()

        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(ascii_graph)
            logger.info(f"[Visualize] Graph saved to {output_path}")

        return ascii_graph
    except Exception as e:
        logger.warning(f"[Visualize] Failed: {e}")
        return "Visualization not available"


# ============================================================================
# 导出
# ============================================================================

__all__ = [
    "get_unified_workflow",
    "get_unified_workflow_memory",
    "create_unified_workflow",
    "build_unified_graph",
    "visualize_unified_workflow",
    "route_by_mode",
    "route_after_ai_detector",
    "route_after_review",
]