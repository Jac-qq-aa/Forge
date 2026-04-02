"""Async LLM client for Qwen API via OpenAI-compatible interface."""

import asyncio
import logging
from openai import AsyncOpenAI, APIError, APIConnectionError, RateLimitError

from forge.config import QWEN_API_URL, QWEN_API_KEY, QWEN_MODEL, LLM_TIMEOUT

logger = logging.getLogger(__name__)


class LLMClientError(Exception):
    """Raised when LLM API call fails after all retries."""
    pass


class LLMClient:
    """Async client for Qwen LLM with retry logic."""

    def __init__(self):
        if not QWEN_API_KEY:
            raise ValueError("QWEN_API_KEY not set in environment")
        self.client = AsyncOpenAI(
            base_url=QWEN_API_URL,
            api_key=QWEN_API_KEY,
            timeout=LLM_TIMEOUT,
        )

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
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        logger.info(f"[LLM] Sending request with {len(messages)} messages")

        response = await self.client.chat.completions.create(
            model=QWEN_MODEL,
            messages=messages,
        )
        if not response.choices or response.choices[0].message.content is None:
            raise LLMClientError("Empty response from LLM")
        content = response.choices[0].message.content
        logger.info(f"[LLM] Response received: {len(content)} chars")
        return content

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
            except (APIError, APIConnectionError, RateLimitError) as e:
                logger.warning(f"[LLM] Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    logger.error(f"[LLM] All retries exhausted")
                    raise LLMClientError(f"LLM调用失败: {e}") from e
                await asyncio.sleep(2 ** attempt)
