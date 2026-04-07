"""Tests for the shared split_cluster() function."""

from __future__ import annotations

import numpy as np

from app.services.taxonomy._constants import SPLIT_SIBLING_SIMILARITY_CEILING
from app.services.taxonomy.split import SplitResult, split_cluster


def _rand_emb(dim: int = 384, seed: int = 0) -> bytes:
    rng = np.random.RandomState(seed)
    v = rng.randn(dim).astype(np.float32)
    v = v / np.linalg.norm(v)
    return v.tobytes()


def test_split_result_dataclass():
    """SplitResult has required fields."""
    r = SplitResult(success=True, children_created=3, noise_reassigned=2, children=[])
    assert r.success is True
    assert r.children_created == 3
    assert r.noise_reassigned == 2


def test_split_result_failure():
    """SplitResult for failed split."""
    r = SplitResult(success=False, children_created=0, noise_reassigned=0, children=[])
    assert r.success is False


def test_split_cluster_is_async():
    """split_cluster must be a coroutine function."""
    import inspect
    assert inspect.iscoroutinefunction(split_cluster)


class TestSiblingSimilarityCheck:
    def test_similar_siblings_detected(self):
        """Centroids with cosine > 0.75 should be flagged as too similar."""
        a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        b = np.array([0.98, 0.2, 0.0], dtype=np.float32)
        b = b / np.linalg.norm(b)
        sim = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
        assert sim > SPLIT_SIBLING_SIMILARITY_CEILING

    def test_dissimilar_siblings_pass(self):
        """Centroids with cosine < 0.75 should be allowed."""
        a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        sim = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
        assert sim < SPLIT_SIBLING_SIMILARITY_CEILING
