"""ReAct Research Agent - 收集素材生成事实清单。

使用 ReAct 循环调用搜索工具，最终输出 Fact Sheet（事实清单）。

目标：收集素材，不是写文章！

工作流程：
1. 根据大纲分析需要搜索的信息点
2. 使用 web_search 搜索相关信息
3. 使用 web_search_with_content 深度获取内容
4. 整理成结构化的 Fact Sheet

使用方式：
```python
from forge.agents.research_agent import run_research_agent

fact_sheet = await run_research_agent(
    outline="...",
    raw_content={...},
    user_input="...",
    rag_context="..."
)
```
"""

import logging
from typing import Dict, Any

from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage

from forge.tools.web_search import web_search, web_search_with_content, search_and_extract
from forge.tools.llm_client import LLMClient

logger = logging.getLogger(__name__)


# ============================================================================
# Research Agent System Prompt
# ============================================================================

RESEARCH_SYSTEM_PROMPT = """你是一个专业的 Research Agent。

**核心目标**：收集素材，生成事实清单（Fact Sheet），**不是写文章**！

## 工作流程

1. **分析大纲**：识别需要搜索的关键信息点
2. **搜索验证**：使用 web_search 搜索相关事实
3. **深度获取**：对关键信息使用 web_search_with_content 获取详细内容
4. **整理输出**：生成结构化的 Fact Sheet

## Fact Sheet 格式（必须按此格式输出）

```
## 核心论点
- 论点1：...
- 论点2：...

## 关键数据
- 数据1：...（来源：URL）
- 数据2：...（来源：URL）

## 案例素材
- 案例1：...
- 案例2：...

## 专家观点
- 观点1：...（来源：URL）
- 观点2：...（来源：URL）

## 补充信息
- 相关背景：...

## 参考资料
- [1] URL1
- [2] URL2
```

## 注意事项

1. **只收集，不创作**：不要写文章段落，只收集事实
2. **注明来源**：每个事实都要注明来源 URL
3. **验证事实**：对不确定的信息使用搜索验证
4. **相关性优先**：优先收集与大纲直接相关的内容
5. **避免冗余**：同一事实不要重复收集

## 搜索策略

- 原文章已经提供的信息 → 不再搜索
- 大纲中涉及但原文缺失的信息 → 重点搜索
- 需要验证的数据 → 使用 web_search 确认
- 需要详细内容 → 使用 web_search_with_content

完成搜索后，输出结构化的 Fact Sheet。
"""


# ============================================================================
# 创建 Research Agent
# ============================================================================

def create_research_agent():
    """创建 Research Agent。

    Returns:
        LangGraph ReAct Agent
    """
    tools = [web_search, web_search_with_content, search_and_extract]

    try:
        # 使用项目的 LLMClient（封装了 DashScope）
        llm_client = LLMClient()
        llm = llm_client.llm  # 获取内部的 ChatOpenAI 对象

        agent = create_react_agent(
            model=llm,
            tools=tools,
            state_modifier=RESEARCH_SYSTEM_PROMPT,
        )

        logger.info("[ResearchAgent] Agent created successfully")
        return agent

    except Exception as e:
        logger.error(f"[ResearchAgent] Failed to create agent: {e}")
        return None


# ============================================================================
# 运行 Research Agent
# ============================================================================

async def run_research_agent(
    outline: str,
    raw_content: Dict[str, Any],
    user_input: str,
    rag_context: str = "",
) -> str:
    """运行 Research Agent 收集素材。

    Args:
        outline: 大纲内容
        raw_content: 原文章内容 {title, text, ...}
        user_input: 用户改写需求
        rag_context: RAG 知识库素材（作为补充）

    Returns:
        Fact Sheet（事实清单）
    """
    logger.info("[ResearchAgent] Starting research...")
    logger.info(f"[ResearchAgent] Outline: {len(outline)} chars")

    agent = create_research_agent()

    if agent is None:
        # Fallback: 使用 RAG 知识库作为素材
        logger.warning("[ResearchAgent] Agent creation failed, using RAG context")
        return _build_fallback_fact_sheet(outline, raw_content, rag_context)

    # 构建初始消息
    initial_message = f"""请根据以下信息收集素材，生成 Fact Sheet：

## 大纲
{outline}

## 原文章
标题：{raw_content.get('title', '')}
内容摘要：{raw_content.get('text', '')[:500]}...

## 用户改写需求
{user_input}

## 已有知识库素材
{rag_context if rag_context else "无"}

请分析大纲，识别需要搜索的信息点，收集素材并输出 Fact Sheet。
"""

    try:
        result = await agent.ainvoke({
            "messages": [HumanMessage(content=initial_message)],
        })

        # 提取最终响应
        messages = result.get("messages", [])
        if messages:
            last_msg = messages[-1]
            fact_sheet = last_msg.content
            logger.info(f"[ResearchAgent] Fact sheet generated: {len(fact_sheet)} chars")
            return fact_sheet

        return _build_fallback_fact_sheet(outline, raw_content, rag_context)

    except Exception as e:
        logger.error(f"[ResearchAgent] Execution failed: {e}")
        return _build_fallback_fact_sheet(outline, raw_content, rag_context)


def _build_fallback_fact_sheet(
    outline: str,
    raw_content: Dict[str, Any],
    rag_context: str,
) -> str:
    """构建 fallback 的 Fact Sheet（Agent 失败时使用）。

    直接从大纲、原文和 RAG 素材整理出事实清单。
    """
    logger.info("[ResearchAgent] Building fallback fact sheet")

    fact_sheet = f"""## 核心论点
（从大纲提取）
- 按大纲结构组织核心观点

## 原文章关键信息
标题：{raw_content.get('title', '')}
主要内容：{raw_content.get('text', '')[:1000]}...

## 知识库补充素材
{rag_context if rag_context else "无额外素材"}

## 备注
Research Agent 未成功运行，此 Fact Sheet 由大纲和原文直接整理。
请基于此素材撰写文章。
"""
    return fact_sheet


# ============================================================================
# 导出
# ============================================================================

__all__ = [
    "create_research_agent",
    "run_research_agent",
    "RESEARCH_SYSTEM_PROMPT",
]