"""独立的判断模型客户端，用于 AI 检测节点。

使用不同的模型进行判断，与改写模型隔离，
避免"自己评判自己"的问题。

模型职责分离（使用同一 API Key，不同模型）：
- 改写模型: qwen-plus (Editor, Humanizer_Editor)
- 判断模型: qwen-max (AI_Detector) - 更强的模型

使用 LangChain ChatOpenAI 以启用 LangSmith tracing。
"""

import asyncio
import logging
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from forge.config import (
    JUDGE_API_KEY, JUDGE_API_URL, JUDGE_MODEL,
    LANGCHAIN_API_KEY, LANGCHAIN_TRACING_V2, LANGCHAIN_PROJECT
)

logger = logging.getLogger(__name__)


# Ensure LangSmith tracing is enabled
if LANGCHAIN_API_KEY:
    import os
    os.environ["LANGCHAIN_API_KEY"] = LANGCHAIN_API_KEY
    os.environ["LANGCHAIN_TRACING_V2"] = LANGCHAIN_TRACING_V2
    os.environ["LANGCHAIN_PROJECT"] = LANGCHAIN_PROJECT


class JudgeLLMClientError(Exception):
    """判断模型调用失败。"""
    pass


class JudgeLLMClient:
    """独立的判断模型客户端。

    使用 LangChain ChatOpenAI，自动启用 LangSmith tracing。
    使用不同的 Qwen 模型进行判断，确保与改写模型隔离。
    """

    def __init__(self):
        if not JUDGE_API_KEY:
            raise ValueError("JUDGE_API_KEY (或 QWEN_API_KEY) not set in environment. "
                           "Please configure API key for AI detection.")

        self.llm = ChatOpenAI(
            base_url=JUDGE_API_URL,
            api_key=JUDGE_API_KEY,
            model=JUDGE_MODEL,
            timeout=60.0,
        )
        logger.info(f"[JudgeLLM] ChatOpenAI initialized with model: {JUDGE_MODEL}")
        logger.info(f"[JudgeLLM] Model isolation: judge={JUDGE_MODEL} vs rewrite=qwen-plus")

    async def judge(self, prompt: str, system_prompt: str = None) -> str:
        """使用判断模型进行分析。

        Args:
            prompt: 用户消息。
            system_prompt: 系统提示（可选）。

        Returns:
            判断结果文本。
        """
        logger.info("[JudgeLLM] Sending request for judgment")

        messages = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=prompt))

        try:
            response = await self.llm.ainvoke(messages)
            content = response.content

            if not content:
                raise JudgeLLMClientError("Empty response from Judge model")

            logger.info(f"[JudgeLLM] Response received: {len(content)} chars")
            return content
        except Exception as e:
            logger.error(f"[JudgeLLM] Judge failed: {e}")
            raise JudgeLLMClientError(f"判断模型调用失败: {e}") from e

    async def judge_with_retry(self, prompt: str, system_prompt: str = None, max_retries: int = 3) -> str:
        """带重试的判断调用。

        Args:
            prompt: 用户消息。
            system_prompt: 系统提示。
            max_retries: 最大重试次数。

        Returns:
            判断结果文本。

        Raises:
            JudgeLLMClientError: 所有重试失败后抛出。
        """
        for attempt in range(max_retries):
            try:
                return await self.judge(prompt, system_prompt)
            except JudgeLLMClientError as e:
                logger.warning(f"[JudgeLLM] Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    logger.error("[JudgeLLM] All retries exhausted")
                    raise
                await asyncio.sleep(2 ** attempt)


def has_judge_client() -> bool:
    """检查是否配置了判断模型（会自动使用 QWEN_API_KEY）。"""
    return bool(JUDGE_API_KEY)  # JUDGE_API_KEY 默认等于 QWEN_API_KEY