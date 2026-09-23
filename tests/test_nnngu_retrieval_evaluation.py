"""Tests for the reproducible NNGu retrieval comparison."""

from scripts.evaluate_nnngu_retrieval import (
    make_filter,
    paired_comparison,
    split_legacy_content,
)


def test_legacy_splitter_reproduces_dropped_short_tail():
    text = "甲" * 501 + "。" + "短尾"

    chunks = split_legacy_content(text)

    assert chunks == ["甲" * 501]


def test_filter_combines_stage_topic_and_region():
    expression = make_filter({
        "secondary_category": "process_notice",
        "topic": "fire_emergency",
        "province": "天津",
    })

    assert expression == (
        'secondary_category == "process_notice" and '
        'topic_tags like "%fire_emergency%" and province == "天津"'
    )


def test_paired_comparison_counts_improvement_and_regression():
    details = [
        {
            "legacy": {"rank": None},
            "optimized": {"rank": 1},
            "optimized_filtered": {"rank": 1},
        },
        {
            "legacy": {"rank": 1},
            "optimized": {"rank": 2},
            "optimized_filtered": {"rank": 1},
        },
    ]

    comparison = paired_comparison(details)

    assert comparison["pure_vector_rank_improved"] == 1
    assert comparison["pure_vector_rank_regressed"] == 1
    assert comparison["filter_rank_improved"] == 1
    assert comparison["filter_rank_regressed"] == 0
