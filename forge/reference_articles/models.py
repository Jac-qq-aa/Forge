"""参考文章库的稳定领域接口。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

CompositionType = Literal["company", "policy", "mixed"]
ArticleArchetype = Literal[
    "company_profile",
    "policy_quick_read",
    "risk_warning",
    "industry_analysis",
    "practical_guide",
    "trend_brand_mix",
]


@dataclass(frozen=True)
class StyleProfile:
    average_sentence_chars: float
    average_paragraph_chars: float
    question_ratio: float
    exclamation_ratio: float
    list_marker_ratio: float
    number_density: float
    first_person_density: float
    transition_density: float
    tone: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReferenceSection:
    role: str
    title: str
    summary: str
    order_index: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnalyzedReferenceArticle:
    article_id: str
    title: str
    content: str
    source_url: str
    platform: str
    composition_type: CompositionType
    archetype: ArticleArchetype
    topic_text: str
    style_text: str
    content_hash: str
    dedup_fingerprint: str
    style_profile: StyleProfile
    sections: tuple[ReferenceSection, ...]
    publish_date: str = ""
    author: str = ""


@dataclass(frozen=True)
class ReferenceRequest:
    topic: str
    target_platform: str
    content_type: str = "general"
    desired_archetype: str = ""
    source_title: str = ""
    source_excerpt: str = ""
    limit: int = 3

    def query_text(self) -> str:
        return "\n".join(
            part for part in (
                f"主题：{self.topic}",
                f"标题：{self.source_title}",
                f"内容类型：{self.content_type}",
                f"文章原型：{self.desired_archetype}",
                f"素材摘要：{self.source_excerpt[:800]}",
            ) if part.split("：", 1)[-1].strip()
        )


@dataclass(frozen=True)
class ReferenceExample:
    article_id: str
    title: str
    archetype: str
    composition_type: str
    score: float
    structure_roles: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReferencePack:
    archetype: str
    structure_roles: tuple[str, ...]
    style_guidance: tuple[str, ...]
    prohibited_phrases: tuple[str, ...]
    examples: tuple[ReferenceExample, ...] = field(default_factory=tuple)

    @classmethod
    def empty(cls) -> "ReferencePack":
        return cls("", (), (), ())

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "ReferencePack":
        if not value:
            return cls.empty()
        return cls(
            archetype=str(value.get("archetype") or ""),
            structure_roles=tuple(str(item) for item in value.get("structure_roles", [])),
            style_guidance=tuple(str(item) for item in value.get("style_guidance", [])),
            prohibited_phrases=tuple(str(item) for item in value.get("prohibited_phrases", [])),
            examples=tuple(
                ReferenceExample(
                    article_id=str(item.get("article_id") or ""),
                    title=str(item.get("title") or ""),
                    archetype=str(item.get("archetype") or ""),
                    composition_type=str(item.get("composition_type") or ""),
                    score=float(item.get("score") or 0.0),
                    structure_roles=tuple(str(role) for role in item.get("structure_roles", [])),
                )
                for item in value.get("examples", []) if isinstance(item, dict)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "archetype": self.archetype,
            "structure_roles": list(self.structure_roles),
            "style_guidance": list(self.style_guidance),
            "prohibited_phrases": list(self.prohibited_phrases),
            "examples": [example.to_dict() for example in self.examples],
        }

    def prompt_context(self) -> str:
        if not self.examples:
            return "未检索到可用参考文章；按平台通用规范写作。"
        return "\n".join((
            f"文章原型：{self.archetype}",
            "结构角色：" + " → ".join(self.structure_roles),
            "风格指引：" + "；".join(self.style_guidance),
            "禁止复用：" + "；".join(self.prohibited_phrases),
            "参考样本：" + "；".join(
                f"{item.title}（仅参考结构与风格，匹配度 {item.score:.2f}）"
                for item in self.examples
            ),
        ))
