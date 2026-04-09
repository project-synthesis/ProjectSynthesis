"""Benchmark latency assertions for EmbeddingIndex backends.

Lightweight performance tests — not a full benchmark suite. Validates
that search and build operations complete within reasonable time bounds.

Covers:
- numpy search at 100 clusters: < 10ms
- numpy search at 500 clusters: < 20ms
- HNSW build at 1000 clusters completes without error
- HNSW search at 1000 clusters: < 10ms
"""

import time

import numpy as np
import pytest

from app.services.taxonomy.embedding_index import (
    EmbeddingIndex,
    _HnswBackend,
    _NumpyBackend,
)

DIM = 384  # realistic dimension for MiniLM embeddings


def _make_matrix(n: int, seed: int = 42) -> np.ndarray:
    """Generate n normalized random vectors of dimension DIM."""
    rng = np.random.RandomState(seed)
    matrix = rng.randn(n, DIM).astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms < 1e-9] = 1.0
    return matrix / norms


def _make_query(seed: int = 99) -> np.ndarray:
    rng = np.random.RandomState(seed)
    v = rng.randn(DIM).astype(np.float32)
    return v / np.linalg.norm(v)


class TestNumpyBenchmark:
    def test_numpy_search_100_clusters(self):
        """numpy search at 100 clusters completes in < 10ms."""
        backend = _NumpyBackend(dim=DIM)
        matrix = _make_matrix(100)
        backend.build(matrix, 100)
        query = _make_query()

        # Warm up
        backend.search(query, k=5, threshold=0.0, filter_fn=None)

        start = time.perf_counter()
        for _ in range(10):
            backend.search(query, k=5, threshold=0.0, filter_fn=None)
        elapsed_ms = (time.perf_counter() - start) / 10 * 1000

        assert elapsed_ms < 10, f"numpy search at 100 clusters took {elapsed_ms:.2f}ms"

    def test_numpy_search_500_clusters(self):
        """numpy search at 500 clusters completes in < 20ms."""
        backend = _NumpyBackend(dim=DIM)
        matrix = _make_matrix(500)
        backend.build(matrix, 500)
        query = _make_query()

        # Warm up
        backend.search(query, k=5, threshold=0.0, filter_fn=None)

        start = time.perf_counter()
        for _ in range(10):
            backend.search(query, k=5, threshold=0.0, filter_fn=None)
        elapsed_ms = (time.perf_counter() - start) / 10 * 1000

        assert elapsed_ms < 20, f"numpy search at 500 clusters took {elapsed_ms:.2f}ms"


class TestHnswBenchmark:
    def test_hnsw_build_1000_clusters(self):
        """HNSW build at 1000 clusters completes without error."""
        backend = _HnswBackend(dim=DIM)
        matrix = _make_matrix(1000)

        start = time.perf_counter()
        backend.build(matrix, 1000)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert backend._index is not None
        assert backend._index.get_current_count() == 1000
        # Build should complete in reasonable time (< 5s even on slow CI)
        assert elapsed_ms < 5000, f"HNSW build at 1000 took {elapsed_ms:.2f}ms"

    def test_hnsw_search_1000_clusters(self):
        """HNSW search at 1000 clusters completes in < 10ms."""
        backend = _HnswBackend(dim=DIM)
        matrix = _make_matrix(1000)
        backend.build(matrix, 1000)
        query = _make_query()

        # Warm up
        backend.search(query, k=5, threshold=0.0, filter_fn=None)

        start = time.perf_counter()
        for _ in range(10):
            backend.search(query, k=5, threshold=0.0, filter_fn=None)
        elapsed_ms = (time.perf_counter() - start) / 10 * 1000

        assert elapsed_ms < 10, f"HNSW search at 1000 clusters took {elapsed_ms:.2f}ms"


class TestEndToEndBenchmark:
    @pytest.mark.asyncio
    async def test_rebuild_and_search_at_scale(self):
        """Full EmbeddingIndex rebuild + search at 1000 clusters."""
        index = EmbeddingIndex(dim=DIM)
        rng = np.random.RandomState(42)
        centroids = {}
        for i in range(1000):
            v = rng.randn(DIM).astype(np.float32)
            v /= np.linalg.norm(v)
            centroids[f"c-{i}"] = v

        await index.rebuild(centroids)
        assert index.size == 1000

        query = _make_query()
        results = index.search(query, k=5, threshold=0.0)
        assert len(results) <= 5
        assert all(isinstance(cid, str) and isinstance(score, float) for cid, score in results)
