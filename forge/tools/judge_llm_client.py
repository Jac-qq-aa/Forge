"""独立的判断模型客户端，用于 AI 检测节点。

使用不同的模型进行判断，与改写模型隔离，
避免"自己评判自己"的问题。

模型职责分离（使用同一 API Key，不同模型）：
- 改写模型: qwen-plus (Editor, Humanizer_Editor)
- 判断模型: qwen-max (AI_Detector) - 更强的模型
"""

import asyncio
import logging
from openai import AsyncOpenAI, APIError, APIConnectionError, RateLimitError

from forge.config import JUDGE_API_KEY, JUDGE_API_URL, JUDGE_MODEL

logger = logging.getLogger(__name__)


class JudgeLLMClientError(Exception):
    """判断模型调用失败。"""
    pass


class JudgeLLMClient:
    """独立的判断模型客户端。

    使用不同的 Qwen 模型进行判断，确保与改写模型隔离。
    """

    def __init__(self):
        if not JUDGE_API_KEY:
            raise ValueError("JUDGE_API_KEY (或 QWEN_API_KEY) not set in environment. "
                           "Please configure API key for AI detection.")
        self.client = AsyncOpenAI(
            base_url=JUDGE_API_URL,
            api_key=JUDGE_API_KEY,
            timeout=60.0,
        )
        logger.info(f"[JudgeLLM] Initialized with model: {JUDGE_MODEL}")
        logger.info(f"[JudgeLLM] Model isolation: rewriting={JUDGE_MODEL != 'qwen-plus'}")

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
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = await self.client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=messages,
        )

        if not response.choices or response.choices[0].message.content is None:
            raise JudgeLLMClientError("Empty response from Judge model")

        content = response.choices[0].message.content
        logger.info(f"[JudgeLLM] Response received: {len(content)} chars")
        return content

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
            except (APIError, APIConnectionError, RateLimitError) as e:
                logger.warning(f"[JudgeLLM] Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    logger.error("[JudgeLLM] All retries exhausted")
                    raise JudgeLLMClientError(f"判断模型API调用失败: {e}") from e
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                logger.error(f"[JudgeLLM] Unexpected error: {e}")
                raise JudgeLLMClientError(f"判断模型调用异常: {e}") from e


def has_judge_client() -> bool:
    """检查是否配置了判断模型（会自动使用 QWEN_API_KEY）。"""
    return bool(JUDGE_API_KEY)  # JUDGE_API_KEY 默认等于 QWEN_API_KEY