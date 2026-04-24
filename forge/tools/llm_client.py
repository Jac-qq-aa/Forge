"""Async and Sync LLM client using LangChain ChatOpenAI for LangSmith tracing."""

import asyncio
import logging
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from forge.config import (
    QWEN_API_URL, QWEN_API_KEY, QWEN_MODEL, LLM_TIMEOUT,
    LANGCHAIN_API_KEY, LANGCHAIN_TRACING_V2, LANGCHAIN_PROJECT
)

logger = logging.getLogger(__name__)


# Ensure LangSmith tracing is enabled
if LANGCHAIN_API_KEY:
    import os
    os.environ["LANGCHAIN_API_KEY"] = LANGCHAIN_API_KEY
    os.environ["LANGCHAIN_TRACING_V2"] = LANGCHAIN_TRACING_V2
    os.environ["LANGCHAIN_PROJECT"] = LANGCHAIN_PROJECT
    logger.info(f"[LLM] LangSmith tracing enabled: {LANGCHAIN_PROJECT}")


class LLMClientError(Exception):
    """Raised when LLM API call fails after all retries."""
    pass


class LLMClient:
    """Async client for Qwen LLM using LangChain ChatOpenAI.

    Using ChatOpenAI enables automatic LangSmith tracing for all calls.
    """

    def __init__(self, model: str = None):
        if not QWEN_API_KEY:
            raise ValueError("QWEN_API_KEY not set in environment")

        self.llm = ChatOpenAI(
            base_url=QWEN_API_URL,
            api_key=QWEN_API_KEY,
            model=model or QWEN_MODEL,
            timeout=LLM_TIMEOUT,
        )
        logger.info(f"[LLM] ChatOpenAI initialized with model: {model or QWEN_MODEL}")

    async def chat(self, prompt: str, system_prompt: str = None) -> str:
        """Send a chat request to Qwen.

        Args:
            prompt: User message.
            system_prompt: Optional system message.

        Returns:
            LLM response text.
        """
        messages = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=prompt))

        logger.info(f"[LLM] Sending request with {len(messages)} messages")

        try:
            response = await self.llm.ainvoke(messages)
            content = response.content
            if not content:
                raise LLMClientError("Empty response from LLM")
            logger.info(f"[LLM] Response received: {len(content)} chars")
            return content
        except Exception as e:
            logger.error(f"[LLM] Chat failed: {e}")
            raise LLMClientError(f"LLM调用失败: {e}") from e

    async def chat_with_retry(self, prompt: str, system_prompt: str = None, max_retries: int = 3) -> str:
        """Send chat request with exponential backoff retry.

        Args:
            prompt: User message.
            system_prompt: Optional system message.
            max_retries: Maximum retry attempts.

        Returns:
            LLM response text.

        Raises:
            LLMClientError: If all retry attempts fail.
        """
        for attempt in range(max_retries):
            try:
                return await self.chat(prompt, system_prompt)
            except LLMClientError as e:
                logger.warning(f"[LLM] Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    logger.error(f"[LLM] All retries exhausted")
                    raise
                await asyncio.sleep(2 ** attempt)


class SyncLLMClient:
    """Sync client for Qwen LLM - used in @tool decorated functions.

    Using ChatOpenAI enables automatic LangSmith tracing.
    """

    def __init__(self, model: str = None):
        if not QWEN_API_KEY:
            raise ValueError("QWEN_API_KEY not set in environment")

        self.llm = ChatOpenAI(
            base_url=QWEN_API_URL,
            api_key=QWEN_API_KEY,
            model=model or QWEN_MODEL,
            timeout=LLM_TIMEOUT,
        )
        logger.info(f"[LLM-Sync] ChatOpenAI initialized with model: {model or QWEN_MODEL}")

    def chat(self, prompt: str, system_prompt: str = None) -> str:
        """Send a synchronous chat request to Qwen."""
        messages = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=prompt))

        logger.info(f"[LLM-Sync] Sending request with {len(messages)} messages")

        try:
            response = self.llm.invoke(messages)
            content = response.content
            if not content:
                raise LLMClientError("Empty response from LLM")
            logger.info(f"[LLM-Sync] Response received: {len(content)} chars")
            return content
        except Exception as e:
            logger.error(f"[LLM-Sync] Chat failed: {e}")
            raise LLMClientError(f"LLM调用失败: {e}") from e

    def chat_with_retry(self, prompt: str, system_prompt: str = None, max_retries: int = 3) -> str:
        """Send chat request with exponential backoff retry (sync version)."""
        import time
        for attempt in range(max_retries):
            try:
                return self.chat(prompt, system_prompt)
            except LLMClientError as e:
                logger.warning(f"[LLM-Sync] Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    logger.error(f"[LLM-Sync] All retries exhausted")
                    raise
                time.sleep(2 ** attempt)


def get_sync_llm() -> SyncLLMClient:
    """Get a synchronous LLM client for use in @tool functions."""
    return SyncLLMClient()