"""Validation tests for knowledge-base embeddings."""

import math

import pytest

from forge.knowledge.config import VECTOR_DIMENSION
from forge.knowledge.manager import KnowledgeBase


def test_accepts_valid_embedding():
    embedding = [0.25] + [0.0] * (VECTOR_DIMENSION - 1)

    assert KnowledgeBase._validate_embedding(embedding) == embedding


@pytest.mark.parametrize(
    "embedding",
    [
        [0.1] * (VECTOR_DIMENSION - 1),
        [0.0] * VECTOR_DIMENSION,
        [math.nan] + [0.0] * (VECTOR_DIMENSION - 1),
        [math.inf] + [0.0] * (VECTOR_DIMENSION - 1),
    ],
)
def test_rejects_invalid_embedding(embedding: list[float]):
    with pytest.raises(ValueError):
        KnowledgeBase._validate_embedding(embedding)
