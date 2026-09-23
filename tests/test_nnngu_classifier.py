"""Tests for deterministic Ruibo website classification."""

from datetime import date

from forge.knowledge.nnngu_classifier import classify_nnngu_article


def _article(title: str, content: str, category: str = "recruitment") -> dict:
    return {
        "id": "1",
        "title": title,
        "content": content,
        "category": category,
        "date": "2026-07-01",
    }


def test_uses_stage_priority_and_preserves_all_stage_tags():
    result = classify_nnngu_article(
        _article("消防员笔试成绩及面试通知（更正）", "天津市招聘信息"),
        reference_date=date(2026, 7, 21),
    )

    assert result["secondary_category"] == "correction_supplement"
    assert result["stage_tags"].split(",") == [
        "correction_supplement",
        "results_and_list",
        "process_notice",
    ]


def test_extracts_multiple_topics_and_region_fields():
    result = classify_nnngu_article(
        _article(
            "天津市北辰区消防救援支队拟录用人员公示",
            "本次人员采用劳务派遣方式，为院内服务岗位提供支持。",
        ),
        reference_date=date(2026, 7, 21),
    )

    assert result["secondary_category"] == "admission_publicity"
    assert set(result["topic_tags"].split(",")) == {
        "fire_emergency",
        "medical_on_site",
        "labor_dispatch",
    }
    assert result["province"] == "天津"
    assert result["city"] == "天津"
    assert result["district"] == "北辰区"


def test_extracts_registration_range_and_active_status():
    result = classify_nnngu_article(
        _article(
            "云南锐博招聘简章",
            "报名时间：2026年7月1日至7月30日，报名结束后组织面试。",
        ),
        reference_date=date(2026, 7, 21),
    )

    assert result["registration_start"] == "2026-07-01"
    assert result["registration_end"] == "2026-07-30"
    assert result["validity_status"] == "active"
    assert result["province"] == "云南"


def test_marks_static_content_as_long_term():
    result = classify_nnngu_article(
        _article("集团概况", "锐博集团介绍", category="company_intro"),
        reference_date=date(2026, 7, 21),
    )

    assert result["secondary_category"] == ""
    assert result["validity_status"] == "long_term"
    assert result["province"] == ""


def test_prefers_title_district_and_marks_multiple_content_regions():
    district_result = classify_nnngu_article(
        _article("静海区消防员体检公告", "咨询地址可能提及昆明办事处。"),
        reference_date=date(2026, 7, 21),
    )
    multiple_result = classify_nnngu_article(
        _article("锐博集团招聘", "工作地点包括广州、武汉和昆明。"),
        reference_date=date(2026, 7, 21),
    )

    assert district_result["province"] == "天津"
    assert district_result["district"] == "静海区"
    assert multiple_result["province"] == "多地区"
    assert set(multiple_result["region_raw"].split(",")) == {"广州", "武汉", "昆明"}


def test_medical_topic_ignores_recruitment_physical_exam_context():
    result = classify_nnngu_article(
        _article(
            "天津市消防救援支队专职消防员体检公告",
            "应聘人员须到指定医院参加体检，并提交医疗机构出具的报告。",
        ),
        reference_date=date(2026, 7, 21),
    )

    assert result["topic_tags"] == "fire_emergency"


def test_medical_topic_accepts_explicit_business_context_in_content():
    result = classify_nnngu_article(
        _article("项目人员招聘公告", "本次招聘人员负责医院院内服务及药品服务。"),
        reference_date=date(2026, 7, 21),
    )

    assert result["topic_tags"] == "medical_on_site"
