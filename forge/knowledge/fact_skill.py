from __future__ import annotations

from pathlib import Path

from forge.infrastructure.telemetry import emit, fingerprint


class ArticleFactsSkill:
    """Read-only policy shared by article planning, research, and critic."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._path = (self._root / "SKILL.md").resolve()
        if self._root not in self._path.parents:
            raise ValueError("文章事实 skill 路径越界")

    async def load(self) -> str:
        import asyncio

        content = await asyncio.to_thread(self._path.read_text, encoding="utf-8")
        await emit("skill.loaded", article_skill="article_facts", resource_version=fingerprint(content))
        return content
