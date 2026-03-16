"""Tests for EmbeddingService — loads the real sentence-transformers model."""

import numpy as np
import pytest

from app.services.embedding_service import EmbeddingService


@pytest.fixture(scope="module")
def svc() -> EmbeddingService:
    """Single service instance shared across tests in this module."""
    return EmbeddingService()


def test_embed_single(svc: EmbeddingService) -> None:
    vec = svc.embed_single("Hello world")
    assert isinstance(vec, np.ndarray)
    assert vec.shape == (384,)


def test_embed_texts_batch(svc: EmbeddingService) -> None:
    texts = ["Python function", "Database query", "Machine learning model"]
    vecs = svc.embed_texts(texts)
    assert len(vecs) == 3
    for v in vecs:
        assert isinstance(v, np.ndarray)
        assert v.shape == (384,)


def test_cosine_search(svc: EmbeddingService) -> None:
    """'Python code' should be semantically closer to 'Python function' than 'Database query'."""
    query = svc.embed_single("Python code")
    python_vec = svc.embed_single("Python function")
    db_vec = svc.embed_single("Database query")

    results = EmbeddingService.cosine_search(query, [python_vec, db_vec], top_k=2)

    assert len(results) == 2
    # Results are sorted by score descending; first result should be index 0 (Python function)
    top_idx, top_score = results[0]
    assert top_idx == 0, "Python function should be ranked higher than Database query for the query 'Python code'"
    assert top_score > 0.0


def test_cosine_search_returns_scores(svc: EmbeddingService) -> None:
    """An exact-duplicate vector should yield similarity > 0.8."""
    text = "The quick brown fox jumps over the lazy dog"
    vec = svc.embed_single(text)
    # Same text → duplicate vector
    results = EmbeddingService.cosine_search(vec, [vec], top_k=1)
    assert len(results) == 1
    idx, score = results[0]
    assert idx == 0
    assert score > 0.8


def test_embed_empty_list(svc: EmbeddingService) -> None:
    result = svc.embed_texts([])
    assert result == []
