"""Tests for _HnswBackend in isolation.

Covers:
- build() creates index with correct params (allow_replace_deleted=True)
- add() inserts vectors and auto-resizes
- remove_label() marks deleted (verify search excludes it)
- search() returns correct (label, score) pairs
- search() with filter_fn filters correctly
- search() converts cosine distance to similarity (1.0 - dist)
- Empty index returns empty results
"""

import numpy as np
import pytest

from app.services.taxonomy.embedding_index import _HnswBackend

DIM = 4


@pytest.fixture
def backend():
    return _HnswBackend(dim=DIM)


def _norm(v):
    v = np.array(v, dtype=np.float32)
    return v / np.linalg.norm(v)


class TestBuild:
    def test_build_creates_index(self, backend):
        matrix = np.eye(3, DIM, dtype=np.float32)
        backend.build(matrix, 3)
        assert backend._index is not None
        assert backend._index.get_current_count() == 3

    def test_build_with_allow_replace_deleted(self, backend):
        """build() must pass allow_replace_deleted=True."""
        matrix = np.eye(3, DIM, dtype=np.float32)
        backend.build(matrix, 3)
        # Verify by adding an item with replace_deleted=True (would fail
        # if the index wasn't created with allow_replace_deleted=True).
        backend._index.mark_deleted(0)
        backend._index.add_items(
            _norm([1, 0, 0, 0]).reshape(1, -1),
            ids=np.array([0]),
            replace_deleted=True,
        )
        assert backend._index.get_current_count() == 3

    def test_build_empty(self, backend):
        matrix = np.empty((0, DIM), dtype=np.float32)
        backend.build(matrix, 0)
        assert backend._index is not None
        assert backend._index.get_current_count() == 0

    def test_build_max_elements_at_least_1000(self, backend):
        """max_elements should be max(count * 2, 1000)."""
        matrix = np.eye(3, DIM, dtype=np.float32)
        backend.build(matrix, 3)
        assert backend._index.get_max_elements() >= 1000


class TestAdd:
    def test_add_inserts_vector(self, backend):
        backend.build(np.empty((0, DIM), dtype=np.float32), 0)
        emb = _norm([1, 0, 0, 0])
        backend.add(0, emb)
        assert backend._index.get_current_count() == 1

    def test_add_auto_resizes(self, backend):
        matrix = np.eye(2, DIM, dtype=np.float32)
        backend.build(matrix, 2)
        original_max = backend._index.get_max_elements()
        # Add a label far beyond current max_elements to trigger resize
        emb = _norm([0, 1, 0, 0])
        backend.add(original_max + 10, emb)
        assert backend._index.get_max_elements() > original_max

    def test_add_replace_deleted(self, backend):
        """add() uses replace_deleted=True so tombstoned slots can be reused."""
        matrix = np.vstack([_norm([1, 0, 0, 0]), _norm([0, 1, 0, 0])])
        backend.build(matrix, 2)
        backend.remove_label(0)
        # Re-add at same label should succeed (replace_deleted)
        backend.add(0, _norm([0, 0, 1, 0]))
        # Search should find the new vector at label 0
        results = backend.search(_norm([0, 0, 1, 0]), k=5, threshold=0.0, filter_fn=None)
        labels = [r[0] for r in results]
        assert 0 in labels

    def test_add_no_index_is_noop(self, backend):
        """add() when index is None should not raise."""
        backend.add(0, _norm([1, 0, 0, 0]))  # no build() called


class TestRemoveLabel:
    def test_remove_marks_deleted(self, backend):
        matrix = np.vstack([_norm([1, 0, 0, 0]), _norm([0, 1, 0, 0])])
        backend.build(matrix, 2)
        backend.remove_label(0)
        # Searching for the removed vector should not return label 0
        results = backend.search(_norm([1, 0, 0, 0]), k=5, threshold=0.0, filter_fn=None)
        labels = [r[0] for r in results]
        assert 0 not in labels

    def test_remove_nonexistent_is_noop(self, backend):
        matrix = np.eye(2, DIM, dtype=np.float32)
        backend.build(matrix, 2)
        backend.remove_label(999)  # should not raise

    def test_remove_no_index_is_noop(self, backend):
        backend.remove_label(0)  # no build() called, should not raise


class TestSearch:
    def test_search_empty_index(self, backend):
        backend.build(np.empty((0, DIM), dtype=np.float32), 0)
        results = backend.search(_norm([1, 0, 0, 0]), k=5, threshold=0.0, filter_fn=None)
        assert results == []

    def test_search_no_index(self, backend):
        """search() when index is None returns empty."""
        results = backend.search(_norm([1, 0, 0, 0]), k=5, threshold=0.0, filter_fn=None)
        assert results == []

    def test_search_returns_correct_labels(self, backend):
        v1 = _norm([1, 0, 0, 0])
        v2 = _norm([0, 1, 0, 0])
        v3 = _norm([0, 0, 1, 0])
        matrix = np.vstack([v1, v2, v3])
        backend.build(matrix, 3)

        results = backend.search(v1, k=1, threshold=0.0, filter_fn=None)
        assert len(results) >= 1
        assert results[0][0] == 0  # label 0 matches v1

    def test_search_cosine_distance_to_similarity(self, backend):
        """Scores should be cosine similarity (1.0 - distance), not raw distance."""
        v1 = _norm([1, 0, 0, 0])
        matrix = np.vstack([v1])
        backend.build(matrix, 1)

        results = backend.search(v1, k=1, threshold=0.0, filter_fn=None)
        assert len(results) == 1
        label, sim = results[0]
        assert label == 0
        # Identical vector: cosine distance ~0, similarity ~1.0
        assert sim > 0.99

    def test_search_threshold_filters(self, backend):
        v1 = _norm([1, 0, 0, 0])
        v2 = _norm([0, 1, 0, 0])
        matrix = np.vstack([v1, v2])
        backend.build(matrix, 2)

        # High threshold should only return the exact match
        results = backend.search(v1, k=5, threshold=0.9, filter_fn=None)
        assert all(sim >= 0.9 for _, sim in results)

    def test_search_sorted_descending(self, backend):
        v1 = _norm([1, 0, 0, 0])
        v2 = _norm([0.9, 0.1, 0, 0])
        v3 = _norm([0.7, 0.3, 0, 0])
        matrix = np.vstack([v1, v2, v3])
        backend.build(matrix, 3)

        results = backend.search(v1, k=5, threshold=0.0, filter_fn=None)
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_with_filter_fn(self):
        # Use dim=384 and enough vectors so the HNSW graph has
        # sufficient connectivity for filtered knn_query to succeed.
        dim = 384
        hnsw = _HnswBackend(dim=dim)
        rng = np.random.RandomState(42)
        n = 50
        vecs = []
        for _ in range(n):
            v = rng.randn(dim).astype(np.float32)
            v /= np.linalg.norm(v)
            vecs.append(v)
        matrix = np.vstack(vecs)
        hnsw.build(matrix, n)

        # Filter out label 0
        def filter_fn(label):
            return label != 0

        results = hnsw.search(vecs[0], k=5, threshold=0.0, filter_fn=filter_fn)
        labels = [r[0] for r in results]
        assert 0 not in labels
        assert len(results) >= 1

    def test_search_respects_k(self, backend):
        """search() returns at most k results."""
        rng = np.random.RandomState(42)
        n = 10
        vecs = []
        for _ in range(n):
            v = rng.randn(DIM).astype(np.float32)
            v /= np.linalg.norm(v)
            vecs.append(v)
        matrix = np.vstack(vecs)
        backend.build(matrix, n)

        results = backend.search(vecs[0], k=3, threshold=0.0, filter_fn=None)
        assert len(results) <= 3
