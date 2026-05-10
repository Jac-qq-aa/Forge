"""Reflection Writer - Generator + Critic 循环写作。

三阶段写作流程：
1. Fact Checker：验证 Fact Sheet 内容，标注可疑项
2. Generator：根据验证后的 Fact Sheet 起草文章
3. Critic：审查文章（事实完整性 + 风格一致性 + 逻辑连贯）
4. 循环修改直到满意（最多 3 次）

使用方式：
```python
from forge.agents.reflection_writer import run_reflection_writer

draft = await run_reflection_writer(
    fact_sheet="...",
    raw_content={...},
    target_platform="xhs_video"
)
```
"""

import logging
import re
from typing import Dict, Any, List, Tuple

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from forge.tools.llm_client import LLMClient, SyncLLMClient

logger = logging.getLogger(__name__)


# ============================================================================
# State Definition
# ============================================================================

class ReflectionState(TypedDict, total=False):
    """Reflection Writer 状态。"""

    # 输入
    fact_sheet: str              # Research Agent 输出的事实清单
    raw_content: Dict[str, Any]  # 原文章内容
    target_platform: str         # 目标平台（影响风格）
    user_input: str              # 用户改写需求

    # Fact Checker 输出
    verified_fact_sheet: str     # 验证后的 Fact Sheet（标注可疑项）
    suspicious_items: List[str]  # 可疑项列表
    verification_log: str        # 验证日志

    # 输出
    current_draft: str           # 当前草稿
    critique: str                # Critic 反馈
    revision_count: int          # 修改次数（默认 0）
    is_approved: bool            # 是否通过审查


# ============================================================================
# Fact Checker Node
# ============================================================================

FACT_CHECKER_PROMPT = """请分析以下事实清单，识别需要验证的关键事实：

## 事实清单
{fact_sheet}

## 验证任务

1. **提取关键数据项**：
   - 数字、百分比、统计数据
   - 日期、时间线
   - 人名、机构名、专业术语

2. **判断置信度**：
   - 高置信度：来自原文章/RAG知识库（标注来源可靠）
   - 低置信度：来自网络搜索、无来源标注（需要验证）

3. **输出格式**：
```
## 高置信度事实（来源可靠，可直接使用）
- 事实1
- 事实2

## 低置信度事实（需验证或谨慎使用）
- 事实3 [来源不明]
- 事实4 [来自网络搜索]

## 需验证的数据项（优先级排序）
1. [数据项] - 验证理由
2. [数据项] - 验证理由
```

只输出分析结果，不要修改事实内容。"""


async def fact_checker_node(state: ReflectionState) -> Dict[str, Any]:
    """Fact Checker：验证 Fact Sheet 内容，标注可疑项。

    工作流程：
    1. 解析 Fact Sheet，提取关键数据项
    2. 对低置信度事实进行验证（最多 3 项）
    3. 标注可疑项，生成验证后的 Fact Sheet
    """
    logger.info("[FactChecker] Starting fact verification")

    fact_sheet = state.get("fact_sheet", "")

    # 1. 提取关键事实项
    key_items = _extract_key_items(fact_sheet)
    logger.info(f"[FactChecker] Extracted {len(key_items)} key items")

    # 2. 使用 LLM 分析置信度
    try:
        llm = LLMClient()
        analysis = await llm.chat_with_retry(
            FACT_CHECKER_PROMPT.format(fact_sheet=fact_sheet)
        )
        logger.info(f"[FactChecker] Analysis complete: {len(analysis)} chars")
    except Exception as e:
        logger.warning(f"[FactChecker] LLM analysis failed: {e}")
        analysis = "分析失败，使用原始 Fact Sheet"

    # 3. 对低置信度数据进行验证（最多 3 项）
    suspicious_items = []
    verification_log = ""

    items_to_verify = _extract_items_to_verify(analysis)
    if items_to_verify:
        logger.info(f"[FactChecker] Verifying {len(items_to_verify)} items")
        verification_log, suspicious_items = await _verify_items(items_to_verify[:3])

    # 4. 生成验证后的 Fact Sheet（标注可疑项）
    verified_fact_sheet = _annotate_fact_sheet(fact_sheet, suspicious_items)

    logger.info(f"[FactChecker] Verification complete: {len(suspicious_items)} suspicious items")

    return {
        "verified_fact_sheet": verified_fact_sheet,
        "suspicious_items": suspicious_items,
        "verification_log": verification_log,
    }


def _extract_key_items(fact_sheet: str) -> List[str]:
    """从 Fact Sheet 提取关键数据项。

    提取模式：
    - 数字（百分比、统计数据）
    - 日期格式
    - 人名/机构名
    """
    items = []

    # 数字和百分比
    numbers = re.findall(r'\d+(?:\.\d+)?%', fact_sheet)
    items.extend(numbers)

    # 年份和日期
    dates = re.findall(r'\d{4}年|\d{4}-\d{2}-\d{2}|\d{1,2}月\d{1,2}日', fact_sheet)
    items.extend(dates)

    # 人名（中文姓名模式）
    names = re.findall(r'[一-龥]{2,4}(?:说|认为|指出|表示|发现)', fact_sheet)
    items.extend(names)

    return items


def _extract_items_to_verify(analysis: str) -> List[str]:
    """从 LLM 分析结果中提取需要验证的项。"""
    items = []

    # 匹配"需验证的数据项"部分
    verify_section = re.search(
        r'## 需验证的数据项.*?\n(.*?)(?:##|$)',
        analysis,
        re.DOTALL
    )

    if verify_section:
        lines = verify_section.group(1).strip().split('\n')
        for line in lines:
            # 提取数据项（去掉序号和理由）
            match = re.match(r'\d+\.\s*\[(.*?)\]', line)
            if match:
                items.append(match.group(1))

    return items


async def _verify_items(items: List[str]) -> Tuple[str, List[str]]:
    """验证数据项，返回验证日志和可疑项列表。"""
    verification_log = "## 验证结果\n"
    suspicious_items = []

    for item in items:
        try:
            # 使用 LLM 快速验证（不调用外部搜索，避免耗时）
            llm = SyncLLMClient()
            result = llm.chat_with_retry(
                f"请验证以下数据是否合理（不需要精确验证，只判断是否明显错误）：\n\n数据：{item}\n\n回复：合理/可疑/无法判断"
            )

            if "可疑" in result:
                suspicious_items.append(item)
                verification_log += f"- {item}: {result}\n"
            else:
                verification_log += f"- {item}: {result}\n"

        except Exception as e:
            logger.warning(f"[FactChecker] Verify failed for {item}: {e}")
            verification_log += f"- {item}: 验证失败\n"

    return verification_log, suspicious_items


def _annotate_fact_sheet(fact_sheet: str, suspicious_items: List[str]) -> str:
    """在 Fact Sheet 中标注可疑项。"""
    if not suspicious_items:
        return fact_sheet

    annotation = "\n\n## ⚠️ 可疑项提示（谨慎使用或验证后再用）\n"
    for item in suspicious_items:
        annotation += f"- {item}\n"

    return fact_sheet + annotation


# ============================================================================
# Generator Node
# ============================================================================

GENERATOR_PROMPT_TEMPLATE = """请根据以下事实清单撰写文章：

## 事实清单
{fact_sheet}

## 原文章参考
标题：{raw_title}
内容摘要：{raw_text}

## 用户改写需求
{user_input}

## 目标平台风格
{platform_style}

## 写作要求

1. **事实准确**：所有事实必须来自 Fact Sheet，注明来源
2. **结构清晰**：按大纲组织段落，逻辑连贯
3. **风格匹配**：符合目标平台的表达风格
4. **内容完整**：不遗漏 Fact Sheet 中的关键信息
5. **严禁编造**：没有的信息用模糊表述，不能虚构

直接输出完整文章（不要加任何说明或标题）："""


async def generator_node(state: ReflectionState) -> Dict[str, Any]:
    """Generator：根据验证后的 Fact Sheet 生成草稿。"""
    logger.info("[Generator] Starting draft generation")

    # 使用验证后的 Fact Sheet（如果有）
    fact_sheet = state.get("verified_fact_sheet") or state.get("fact_sheet", "")
    raw_content = state.get("raw_content", {})
    user_input = state.get("user_input", "")
    target_platform = state.get("target_platform", "zhihu_article")

    # 获取平台风格描述
    platform_style = _get_platform_style(target_platform)

    prompt = GENERATOR_PROMPT_TEMPLATE.format(
        fact_sheet=fact_sheet,
        raw_title=raw_content.get("title", ""),
        raw_text=raw_content.get("text", "")[:500],
        user_input=user_input,
        platform_style=platform_style,
    )

    try:
        llm = LLMClient()
        draft = await llm.chat_with_retry(prompt)
        logger.info(f"[Generator] Draft generated: {len(draft)} chars")
        return {"current_draft": draft}

    except Exception as e:
        logger.error(f"[Generator] Failed: {e}")
        return {"current_draft": f"生成失败: {e}"}


# ============================================================================
# Critic Node
# ============================================================================

CRITIC_PROMPT_TEMPLATE = """请审查以下文章：

## 当前文章
{current_draft}

## 事实清单（对照检查）
{fact_sheet}

## 审查标准（基础审查）

请逐项检查：

1. **事实完整性**：
   - Fact Sheet 中的核心论点是否都包含？
   - 关键数据是否准确引用？
   - 是否遗漏重要案例或观点？

2. **风格一致性**：
   - 是否符合{platform}的表达风格？
   - 语言是否生动、有吸引力？

3. **逻辑连贯性**：
   - 段落衔接是否自然？
   - 论证过程是否清晰？

## 输出格式

如果文章完美符合所有标准，回复：
**通过**

如果有问题，回复（逐条列出）：
**问题清单**
1. [具体问题描述]
2. [具体问题描述]
...

不要修改文章，只列出问题。"""


async def critic_node(state: ReflectionState) -> Dict[str, Any]:
    """Critic：审查草稿质量。"""
    logger.info("[Critic] Starting review")

    current_draft = state.get("current_draft", "")
    # 使用验证后的 Fact Sheet（如果有）
    fact_sheet = state.get("verified_fact_sheet") or state.get("fact_sheet", "")
    target_platform = state.get("target_platform", "zhihu_article")
    revision_count = state.get("revision_count", 0)

    prompt = CRITIC_PROMPT_TEMPLATE.format(
        current_draft=current_draft,
        fact_sheet=fact_sheet,
        platform=_get_platform_name(target_platform),
    )

    try:
        llm = LLMClient()
        critique = await llm.chat_with_retry(prompt)

        # 判断是否通过
        is_approved = "**通过**" in critique or critique.strip() == "通过"
        new_revision_count = revision_count + 1

        logger.info(f"[Critic] Review complete: approved={is_approved}, revision={new_revision_count}")

        return {
            "critique": critique,
            "is_approved": is_approved,
            "revision_count": new_revision_count,
        }

    except Exception as e:
        logger.error(f"[Critic] Failed: {e}")
        # 审查失败，视为通过，避免阻塞
        return {
            "critique": f"审查失败: {e}",
            "is_approved": True,
            "revision_count": revision_count + 1,
        }


# ============================================================================
# Reviser Node
# ============================================================================

REVISER_PROMPT_TEMPLATE = """请根据审查反馈修改文章：

## 当前文章
{current_draft}

## 审查反馈（需要解决的问题）
{critique}

## 修改要求

1. **针对每个问题逐一修改**
2. **保持其他部分的完整性**（不要改动已通过的部分）
3. **不添加未验证的新内容**
4. **保持 Fact Sheet 中的所有事实**

直接输出修改后的完整文章："""


async def reviser_node(state: ReflectionState) -> Dict[str, Any]:
    """Reviser：根据 Critic 反馈修改草稿。"""
    logger.info("[Reviser] Starting revision")

    current_draft = state.get("current_draft", "")
    critique = state.get("critique", "")

    prompt = REVISER_PROMPT_TEMPLATE.format(
        current_draft=current_draft,
        critique=critique,
    )

    try:
        llm = LLMClient()
        revised_draft = await llm.chat_with_retry(prompt)
        logger.info(f"[Reviser] Draft revised: {len(revised_draft)} chars")
        return {"current_draft": revised_draft}

    except Exception as e:
        logger.error(f"[Reviser] Failed: {e}")
        # 修改失败，保持原草稿
        return {"current_draft": current_draft}


# ============================================================================
# Routing Function
# ============================================================================

def route_after_critic(state: ReflectionState) -> str:
    """Critic 后路由。

    路由逻辑：
    - is_approved=True → 结束
    - revision_count >= 3 → 结束（超过最大迭代）
    - 否则 → reviser
    """
    is_approved = state.get("is_approved", False)
    revision_count = state.get("revision_count", 0)

    if is_approved:
        logger.info("[Route] Approved, finish")
        return "approved"

    if revision_count >= 3:
        logger.info("[Route] Max revisions reached, finish")
        return "max_revisions"

    logger.info("[Route] Needs revision")
    return "reviser"


# ============================================================================
# Build Reflection Graph
# ============================================================================

def build_reflection_graph() -> StateGraph:
    """构建 Reflection Writer 的 StateGraph。

    流程：
    START → fact_checker → generator → critic → [approved/max_revisions: END, reviser: 循环]
    """
    graph = StateGraph(ReflectionState)

    graph.add_node("fact_checker", fact_checker_node)
    graph.add_node("generator", generator_node)
    graph.add_node("critic", critic_node)
    graph.add_node("reviser", reviser_node)

    graph.add_edge(START, "fact_checker")
    graph.add_edge("fact_checker", "generator")
    graph.add_edge("generator", "critic")
    graph.add_conditional_edges(
        "critic",
        route_after_critic,
        {
            "approved": END,
            "max_revisions": END,
            "reviser": "reviser",
        },
    )
    graph.add_edge("reviser", "critic")

    logger.info("[ReflectionWriter] Graph built with Fact Checker")
    return graph


# ============================================================================
# Run Reflection Writer
# ============================================================================

async def run_reflection_writer(
    fact_sheet: str,
    raw_content: Dict[str, Any],
    target_platform: str = "zhihu_article",
    user_input: str = "",
) -> str:
    """运行 Reflection Writer 生成文章。

    Args:
        fact_sheet: Research Agent 输出的事实清单
        raw_content: 原文章内容
        target_platform: 目标平台
        user_input: 用户改写需求

    Returns:
        最终文章草稿

    流程：
    1. Fact Checker: 验证事实，标注可疑项
    2. Generator: 根据验证后的 Fact Sheet 生成草稿
    3. Critic: 审查草稿
    4. Reviser: 修改草稿（最多 3 次循环）
    """
    logger.info("[ReflectionWriter] Starting...")
    logger.info(f"[ReflectionWriter] Fact sheet: {len(fact_sheet)} chars")

    # 构建并编译 graph
    graph = build_reflection_graph()
    workflow = graph.compile()

    # 初始状态
    initial_state = {
        "fact_sheet": fact_sheet,
        "raw_content": raw_content,
        "target_platform": target_platform,
        "user_input": user_input,
        "revision_count": 0,
        "is_approved": False,
    }

    try:
        result = await workflow.ainvoke(initial_state)

        draft = result.get("current_draft", "")
        revision_count = result.get("revision_count", 0)
        is_approved = result.get("is_approved", False)
        suspicious_items = result.get("suspicious_items", [])

        logger.info(f"[ReflectionWriter] Complete: revisions={revision_count}, approved={is_approved}, suspicious={len(suspicious_items)}")
        return draft

    except Exception as e:
        logger.error(f"[ReflectionWriter] Failed: {e}")
        # Fallback：直接调用 Generator
        return await _fallback_generator(fact_sheet, raw_content, user_input, target_platform)


async def _fallback_generator(
    fact_sheet: str,
    raw_content: Dict[str, Any],
    user_input: str,
    target_platform: str,
) -> str:
    """Fallback：直接生成（Reflection 流程失败时）。"""
    logger.info("[ReflectionWriter] Using fallback generator")

    state = {
        "fact_sheet": fact_sheet,
        "raw_content": raw_content,
        "user_input": user_input,
        "target_platform": target_platform,
    }

    result = await generator_node(state)
    return result.get("current_draft", "")


# ============================================================================
# Platform Style Helpers
# ============================================================================

def _get_platform_style(platform: str) -> str:
    """获取平台风格描述。"""
    styles = {
        "xhs_video": "小红书短视频脚本风格：口语化、轻松活泼、多用感叹号、适合朗读、长度适中",
        "zhihu_article": "知乎文章风格：专业严谨、逻辑清晰、适度引用数据、语言有深度",
        "zhihu_video": "知乎视频脚本风格：叙述感强、引人入胜、适合视频朗读、有故事性",
    }
    return styles.get(platform, "通用文章风格：清晰、准确、有逻辑")


def _get_platform_name(platform: str) -> str:
    """获取平台名称。"""
    names = {
        "xhs_video": "小红书短视频",
        "zhihu_article": "知乎文章",
        "zhihu_video": "知乎视频",
    }
    return names.get(platform, "通用平台")


# ============================================================================
# 导出
# ============================================================================

__all__ = [
    "build_reflection_graph",
    "run_reflection_writer",
    "ReflectionState",
    "fact_checker_node",
    "generator_node",
    "critic_node",
    "reviser_node",
]