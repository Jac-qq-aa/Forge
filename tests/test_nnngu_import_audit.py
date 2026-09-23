"""Tests for the auditable Ruibo website import pipeline."""

from import_nnngu import build_documents


def test_build_documents_reports_lossless_coverage_and_metadata():
    article = {
        "id": "42",
        "title": "锐博测试文章",
        "content": "".join(f"第{i}条重要信息。" for i in range(120)),
        "category": "faq",
        "url": "https://www.nnngu.com/article/42.html",
    }

    documents, audit = build_documents([article])

    assert documents
    assert audit["article_count"] == 1
    assert audit["lossless_article_count"] == 1
    assert audit["minimum_coverage_ratio"] == 1.0
    assert audit["duplicate_document_ids"] == 0
    assert all(document["metadata"]["article_id"] == "42" for document in documents)
    assert all(
        document["metadata"]["source_url"] == article["url"]
        for document in documents
    )
    assert audit["classification"]["validity_statuses"] == {"long_term": 1}
    assert all(
        "secondary_category" in document["metadata"]
        and "topic_tags" in document["metadata"]
        and "validity_status" in document["metadata"]
        for document in documents
    )
