from __future__ import annotations

import httpx
from urllib.parse import urlparse

from forge.domain.models import Evidence, Identity
from forge.infrastructure.telemetry import traced


class CompositeSearch:
    def __init__(self, *, tavily_api_key: str, vector_search: object, max_results: int = 5) -> None:
        self._tavily_api_key = tavily_api_key
        self._vector_search = vector_search
        self._max_results = max_results

    @traced("search.tavily")
    async def web(self, query: str, *, limit: int = 5) -> list[Evidence]:
        if not self._tavily_api_key:
            raise RuntimeError("TAVILY_API_KEY 未配置")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self._tavily_api_key,
                    "query": query,
                    "max_results": min(limit, self._max_results),
                    "search_depth": "advanced",
                },
            )
            response.raise_for_status()
        return [
            Evidence(
                source_kind="web",
                title=item.get("title", ""),
                url=item.get("url", ""),
                quote=item.get("content", ""),
                authority=(
                    0.88
                    if urlparse(item.get("url", "")).hostname in {"nnngu.com", "www.nnngu.com"}
                    else 0.5
                ),
                metadata={"provider": "tavily", "score": item.get("score")},
            )
            for item in response.json().get("results", [])
        ]

    @traced("search.pgvector")
    async def semantic(
        self, query: str, *, identity: Identity, limit: int = 8, scope: str = "all"
    ) -> list[Evidence]:
        return await self._vector_search.semantic(query, identity=identity, limit=limit, scope=scope)
