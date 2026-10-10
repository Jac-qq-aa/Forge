from __future__ import annotations

from dataclasses import dataclass
import asyncio
from pathlib import Path
from typing import Any

from forge.application.ports import ModelGateway
from forge.knowledge.text2sql import SkillLoader
from forge.infrastructure.telemetry import emit, fingerprint, error_fields, traced


ARTICLE_TYPES = {
    "company_profile": "公司介绍",
    "business_introduction": "业务介绍",
    "industry_practices": "行业解读与行业行规",
    "compliance_explanation": "法律合规说明",
    "case_study": "案例分析",
}


@dataclass(frozen=True)
class ArticleTypeSelection:
    code: str
    name: str
    skill: str
    warning: str = ""


class ArticleTypeSkill:
    """识别原文文章类型，并加载供全流程共享的写作规范。"""

    def __init__(self, *, model: ModelGateway, root: Path) -> None:
        self._model = model
        self._loader = SkillLoader(root)

    @traced("skill.article_type")
    async def resolve(
        self, *, source_text: str, direction: str, user_request: str
    ) -> ArticleTypeSelection:
        try:
            result: dict[str, Any] = await self._model.structured(
                system=(
                    "识别原文的主要文章类型。优先沿用原文类型；只有用户明确要求改成另一类型时才转换。"
                    "必须从给出的类型代码中选择，不得创造新类型。"
                ),
                prompt=(
                    f"文章方向：{direction}\n用户要求：{user_request}\n"
                    f"原文：\n{source_text[:12000]}"
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "article_type": {"type": "string", "enum": list(ARTICLE_TYPES)},
                        "reason": {"type": "string"},
                    },
                    "required": ["article_type", "reason"],
                    "additionalProperties": False,
                },
            )
        except Exception as exc:
            skill = await asyncio.to_thread(self._loader.load, ("SKILL",))
            await emit("fallback.used", status="degraded", reason="article_type_model_failed", strategy="source_default", **error_fields(exc))
            return ArticleTypeSelection(
                code="source_default",
                name="沿用原文类型",
                skill=skill,
                warning=f"文章类型识别失败，已使用通用原文规范：{type(exc).__name__}",
            )
        code = result.get("article_type")
        if code not in ARTICLE_TYPES:
            skill = await asyncio.to_thread(self._loader.load, ("SKILL",))
            await emit("fallback.used", status="degraded", reason="article_type_invalid", strategy="source_default")
            return ArticleTypeSelection(
                code="source_default",
                name="沿用原文类型",
                skill=skill,
                warning="文章类型识别结果超出允许范围，已使用通用原文规范",
            )
        skill = await asyncio.to_thread(self._loader.load, ("SKILL", code))
        await emit("skill.loaded", article_type=code, resources=["SKILL", code], resource_version=fingerprint(skill))
        return ArticleTypeSelection(code=code, name=ARTICLE_TYPES[code], skill=skill)
