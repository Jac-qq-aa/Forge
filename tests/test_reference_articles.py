from __future__ import annotations

import pytest

from forge.reference_articles.analyzer import (
    analyze_reference_article,
    content_hash,
    dedup_fingerprint,
    discover_boilerplate,
    extract_company_fact_candidates,
    split_sentences,
    strip_boilerplate,
)
from forge.reference_articles.library import ReferenceArticleLibrary
from forge.reference_articles.models import ReferenceRequest


def _article(article_id: str, title: str, content: str) -> dict[str, str]:
    return {"id": article_id, "title": title, "content": content, "platform": "zhihu_article"}


def test_analyzer_separates_policy_from_company_facts() -> None:
    policy = analyze_reference_article(_article(
        "p1", "劳务派遣新规解读：企业如何应对严查",
        ("人社部门发布新的劳务派遣规定。企业需要核对劳动合同、社保和派遣比例，"
         "违规可能受到处罚。如何排查风险？可以按岗位、合同、费用和流程逐项检查。") * 5,
    ))
    company = analyze_reference_article(_article(
        "c1", "锐博集团介绍",
        ("锐博集团成立以来持续服务客户。集团总部建立专业团队，集团业务覆盖人力资源服务。"
         "锐博集团通过标准流程提升交付质量，并以长期服务能力支持客户发展。") * 5,
    ))
    mixed = analyze_reference_article(_article(
        "m1", "新政策下的人力资源服务",
        ("新的劳动合同政策开始施行，监管部门要求企业加强合规。锐博集团结合服务客户经验，"
         "由集团业务团队解释劳务派遣风险，并提出合同、社保和岗位管理建议。") * 5,
    ))

    assert policy.composition_type == "policy"
    assert policy.archetype in {"risk_warning", "practical_guide", "policy_quick_read"}
    assert company.composition_type == "company"
    assert company.archetype == "company_profile"
    assert mixed.composition_type == "mixed"
    assert mixed.archetype == "trend_brand_mix"


def test_dedup_ignores_whitespace_and_punctuation_noise() -> None:
    base = "锐博集团提供人力资源服务。企业需要关注劳动合同与社保合规。" * 20
    noisy = "锐博集团 提供人力资源服务！\n企业需要关注劳动合同与社保合规；" * 20

    assert content_hash(base) != content_hash(noisy)
    assert dedup_fingerprint(base) == dedup_fingerprint(noisy)


@pytest.mark.asyncio
async def test_reference_pack_contains_guidance_not_article_body() -> None:
    rows = [{
        "id": "row-1",
        "article_id": "a1",
        "title": "劳务派遣合规指南",
        "composition_type": "policy",
        "archetype": "practical_guide",
        "style_profile": {
            "average_sentence_chars": 28,
            "average_paragraph_chars": 130,
            "question_ratio": 0.06,
            "tone": "专业、克制",
        },
        "section_blueprint": [
            {"role": "场景导入"}, {"role": "规则说明"}, {"role": "操作步骤"},
        ],
    }]

    class FakeIndex:
        async def search(self, **_: object) -> list[tuple[str, float]]:
            return [("row-1", 0.91)]

    class FakeRepository:
        async def get_many(self, _: list[str]) -> list[dict[str, object]]:
            return rows

    library = ReferenceArticleLibrary(FakeRepository(), FakeIndex())
    pack = await library.select_reference_pack(ReferenceRequest(
        topic="劳务派遣合同", target_platform="zhihu_article"
    ))
    prompt = pack.prompt_context()
    assert pack.structure_roles == ("场景导入", "规则说明", "操作步骤")
    assert "劳务派遣合规指南" in prompt
    assert "文章正文" not in prompt
    assert pack.examples[0].score == 0.91


def test_short_or_incomplete_article_is_rejected() -> None:
    with pytest.raises(ValueError):
        analyze_reference_article({"id": "x", "title": "短文", "content": "太短"})


def test_repeated_boilerplate_is_removed_before_style_analysis() -> None:
    repeated = "锐博集团拥有专业团队并为客户提供一站式人力资源服务。"
    articles = [
        split_sentences(f"第{i}篇文章的政策分析和具体建议。{repeated}其他结论。")
        for i in range(3)
    ]
    boilerplate = discover_boilerplate(articles, min_documents=3)
    cleaned, removed = strip_boilerplate("本篇独有观点。" + repeated, boilerplate)

    assert repeated in removed
    assert repeated not in cleaned
    assert "本篇独有观点" in cleaned


def test_company_facts_enter_pending_review_and_temporal_news_is_flagged() -> None:
    candidates = extract_company_fact_candidates(
        "a1",
        "锐博集团成立于2011年。锐博集团重视人才。当前锐博集团服务客户超过一万家。",
    )

    assert len(candidates) == 2
    assert all(item["review_status"] == "pending" for item in candidates)
    assert all(item["is_temporal"] is True for item in candidates)
