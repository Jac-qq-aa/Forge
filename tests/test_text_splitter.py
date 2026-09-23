"""Tests for lossless Chinese knowledge-base chunking."""

import pytest

from forge.knowledge.text_splitter import ChineseTextSplitter


def _bodies(chunks: list[dict], title: str = "") -> list[str]:
    prefix = f"[{title}] " if title else ""
    return [
        chunk["content"][len(prefix):]
        if prefix and chunk["content"].startswith(prefix)
        else chunk["content"]
        for chunk in chunks
    ]


def test_preserves_short_sentences_in_long_article():
    sentences = [f"这是第{i}条简短但重要的信息。" for i in range(60)]
    text = "".join(sentences)
    splitter = ChineseTextSplitter(chunk_size=120, chunk_overlap=20)

    bodies = _bodies(splitter.split_text(text))

    for sentence in sentences:
        assert any(sentence in body for body in bodies)


def test_preserves_paragraph_boundaries_and_tail():
    text = ("第一段内容。" * 30) + "\n\n" + ("第二段内容。" * 30) + "唯一结尾标记"
    splitter = ChineseTextSplitter(chunk_size=160, chunk_overlap=20)

    bodies = _bodies(splitter.split_text(text))

    assert any("\n\n" in body for body in bodies)
    assert any("唯一结尾标记" in body for body in bodies)


def test_overlap_does_not_discard_current_chunk_tail():
    text = ("甲" * 500) + "最终答案"
    splitter = ChineseTextSplitter(
        chunk_size=100,
        chunk_overlap=20,
        min_chunk_size=20,
    )

    bodies = _bodies(splitter.split_text(text))

    assert any("最终答案" in body for body in bodies)
    assert all(len(body) <= 120 for body in bodies)


def test_adds_title_prefix_to_every_chunk():
    splitter = ChineseTextSplitter(chunk_size=100, chunk_overlap=10)

    chunks = splitter.split_text("业务介绍。" * 50, {"title": "锐博集团"})

    assert chunks
    assert all(chunk["content"].startswith("[锐博集团] ") for chunk in chunks)


@pytest.mark.parametrize(
    ("chunk_size", "chunk_overlap", "min_chunk_size"),
    [(0, 0, 1), (100, 100, 10), (100, -1, 10), (100, 10, 101)],
)
def test_rejects_invalid_chunk_configuration(
    chunk_size: int,
    chunk_overlap: int,
    min_chunk_size: int,
):
    with pytest.raises(ValueError):
        ChineseTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            min_chunk_size=min_chunk_size,
        )
