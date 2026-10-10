from __future__ import annotations

import json
import re
from typing import Any

from openai import AsyncOpenAI
from langsmith.wrappers import wrap_openai
from forge.infrastructure.telemetry import fingerprint, span, traced
from forge.infrastructure.model_limits import SharedModelLimiter, estimate_tokens


class OpenAICompatibleModel:
    def __init__(self, *, api_key: str, base_url: str, model: str,
                 limiter: SharedModelLimiter, timeout: int = 180, max_output_tokens: int = 4096) -> None:
        if not api_key:
            raise RuntimeError("LLM_API_KEY 未配置")
        self._client = wrap_openai(AsyncOpenAI(api_key=api_key, base_url=base_url,
                                                timeout=timeout, max_retries=0))
        self._model = model
        self._limiter = limiter
        self._max_output_tokens = max_output_tokens

    async def text(self, *, system: str, prompt: str) -> str:
        async with span("model.completion", model=self._model,
                        prompt_version=fingerprint(system), input_fingerprint=fingerprint(prompt)) as details:
            response = await self._limiter.call(lambda: self._client.chat.completions.create(
                model=self._model,
                max_tokens=self._max_output_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            ), estimated_tokens=estimate_tokens(system, prompt, output_tokens=self._max_output_tokens))
            if response.usage:
                details["tokens"] = {"input": response.usage.prompt_tokens,
                                     "output": response.usage.completion_tokens,
                                     "total": response.usage.total_tokens}
            content = response.choices[0].message.content
            if not content:
                raise RuntimeError("模型返回空内容")
            details["output_fingerprint"] = fingerprint(content)
            return content

    @traced("model.structured")
    async def structured(
        self, *, system: str, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        raw = await self.text(
            system=f"{system}\n只输出符合以下 JSON Schema 的 JSON：\n{json.dumps(schema, ensure_ascii=False)}",
            prompt=prompt,
        )
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
        start, end = raw.find("{"), raw.rfind("}")
        if not fenced and (start < 0 or end < start):
            raise ValueError("模型结构化输出中没有 JSON 对象")
        candidate = fenced.group(1) if fenced else raw[start : end + 1]
        value = json.loads(candidate)
        if not isinstance(value, dict):
            raise ValueError("模型结构化输出不是 JSON 对象")
        return value


class EmbeddingGateway:
    def __init__(
        self, *, api_key: str, base_url: str, model: str, limiter: SharedModelLimiter,
        dimensions: int | None = None, timeout: int = 180
    ) -> None:
        self._client = wrap_openai(AsyncOpenAI(api_key=api_key, base_url=base_url,
                                                timeout=timeout, max_retries=0))
        self._model = model
        self._dimensions = dimensions
        self._limiter = limiter

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return int(self._dimensions or 0)

    async def embed(self, text: str) -> list[float]:
        async with span("model.embedding", model=self._model, dimensions=self._dimensions,
                        input_fingerprint=fingerprint(text)) as details:
            kwargs: dict[str, Any] = {"model": self._model, "input": text}
            if self._dimensions:
                kwargs["dimensions"] = self._dimensions
            response = await self._limiter.call(lambda: self._client.embeddings.create(**kwargs),
                                                estimated_tokens=estimate_tokens(text))
            if response.usage:
                details["tokens"] = {"input": response.usage.prompt_tokens, "total": response.usage.total_tokens}
            details["result_dimensions"] = len(response.data[0].embedding)
            return response.data[0].embedding
