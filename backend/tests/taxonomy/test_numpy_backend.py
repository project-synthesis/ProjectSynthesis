"""Tests for _NumpyBackend extracted from EmbeddingIndex."""

import numpy as np
import pytest

from app.services.taxonomy.embedding_index import _NumpyBackend


@pytest.fixture
def backend():
    return _NumpyBackend(dim=4)


def _norm(v):
    v = np.array(v, dtype=np.float32)
    return v / np.linalg.norm(v)


class TestBuild:
    def test_build_sets_matrix(self, backend):
        matrix = np.eye(3, 4, dtype=np.float32)
        backend.build(matrix, 3)
        assert backend._matrix.shape == (3, 4)
        np.testing.assert_array_equal(backend._matrix, matrix)

    def test_build_copies_matrix(self, backend):
        matrix = np.eye(2, 4, dtype=np.float32)
        backend.build(matrix, 2)
        matrix[:] = 0.0
        # Build should have made a copy
        assert not np.allclose(backend._matrix, 0.0)

    def test_build_empty(self, backend):
        matrix = np.empty((0, 4), dtype=np.float32)
        backend.build(matrix, 0)
        assert backend._matrix.shape == (0, 4)


class TestAdd:
    def test_add_within_bounds(self, backend):
        matrix = np.zeros((3, 4), dtype=np.float32)
        backend.build(matrix, 3)
        emb = _norm([1, 0, 0, 0])
        backend.add(1, emb)
        np.testing.assert_array_almost_equal(backend._matrix[1], emb)

    def test_add_beyond_bounds_extends(self, backend):
        backend.build(np.empty((0, 4), dtype=np.float32), 0)
        emb = _norm([0, 1, 0, 0])
        backend.add(5, emb)
        assert backend._matrix.shape[0] >= 6
        np.testing.assert_array_almost_equal(backend._matrix[5], emb)

    def test_add_at_exact_boundary(self, backend):
        matrix = np.zeros((2, 4), dtype=np.float32)
        backend.build(matrix, 2)
        emb = _norm([0, 0, 1, 0])
        backend.add(2, emb)
        assert backend._matrix.shape[0] >= 3
        np.testing.assert_array_almost_equal(backend._matrix[2], emb)


class TestRemoveLabel:
    def test_remove_zeros_row(self, backend):
        matrix = np.eye(3, 4, dtype=np.float32)
        backend.build(matrix, 3)
        backend.remove_label(1)
        np.testing.assert_array_equal(backend._matrix[1], np.zeros(4))

    def test_remove_out_of_bounds_is_noop(self, backend):
        backend.build(np.eye(2, 4, dtype=np.float32), 2)
        backend.remove_label(10)  # should not raise


class TestSearch:
    def test_search_empty(self, backend):
        backend.build(np.empty((0, 4), dtype=np.float32), 0)
        query = _norm([1, 0, 0, 0])
        results = backend.search(query, k=5, threshold=0.0, filter_fn=None)
        assert results == []

    def test_search_returns_top_k(self, backend):
        # 3 vectors: one aligned with query, two orthogonal
        v1 = _norm([1, 0, 0, 0])
        v2 = _norm([0, 1, 0, 0])
        v3 = _norm([0, 0, 1, 0])
        matrix = np.vstack([v1, v2, v3])
        backend.build(matrix, 3)

        query = _norm([1, 0, 0, 0])
        results = backend.search(query, k=1, threshold=0.0, filter_fn=None)
        assert len(results) == 1
        assert results[0][0] == 0  # label 0 is v1
        assert results[0][1] > 0.99

    def test_search_threshold_filter(self, backend):
        v1 = _norm([1, 0, 0, 0])
        v2 = _norm([0, 1, 0, 0])
        matrix = np.vstack([v1, v2])
        backend.build(matrix, 2)

        query = _norm([1, 0, 0, 0])
        results = backend.search(query, k=5, threshold=0.9, filter_fn=None)
        assert len(results) == 1
        assert results[0][0] == 0

    def test_search_with_filter_fn(self, backend):
        v1 = _norm([1, 0, 0, 0])
        v2 = _norm([0.99, 0.01, 0, 0])
        matrix = np.vstack([v1, v2])
        backend.build(matrix, 2)

        query = _norm([1, 0, 0, 0])
        # Filter out label 0
        results = backend.search(
            query, k=5, threshold=0.0,
            filter_fn=lambda label: label != 0,
        )
        assert len(results) >= 1
        assert all(label != 0 for label, _ in results)

    def test_search_sorted_descending(self, backend):
        v1 = _norm([1, 0, 0, 0])
        v2 = _norm([0.9, 0.1, 0, 0])
        v3 = _norm([0.7, 0.3, 0, 0])
        matrix = np.vstack([v1, v2, v3])
        backend.build(matrix, 3)

        query = _norm([1, 0, 0, 0])
        results = backend.search(query, k=5, threshold=0.0, filter_fn=None)
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)
