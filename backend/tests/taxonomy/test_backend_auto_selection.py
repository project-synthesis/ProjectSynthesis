"""Tests for EmbeddingIndex auto-selection between numpy and HNSW backends.

Covers:
- Below HNSW_CLUSTER_THRESHOLD (1000): numpy backend stays active
- At/above threshold: HNSW backend selected on rebuild
- Switching from numpy to HNSW on rebuild
- Switching from HNSW back to numpy when cluster count drops below threshold
- upsert() does NOT trigger backend switch (only rebuild)
"""

import numpy as np
import pytest

from app.services.taxonomy._constants import HNSW_CLUSTER_THRESHOLD
from app.services.taxonomy.embedding_index import (
    EmbeddingIndex,
    _HnswBackend,
    _NumpyBackend,
)

DIM = 4


def _make_centroids(n: int, seed: int = 42) -> dict[str, np.ndarray]:
    """Generate n random centroids keyed by UUID-like strings."""
    rng = np.random.RandomState(seed)
    centroids = {}
    for i in range(n):
        v = rng.randn(DIM).astype(np.float32)
        v /= np.linalg.norm(v)
        centroids[f"c-{i}"] = v
    return centroids


@pytest.fixture
def index():
    return EmbeddingIndex(dim=DIM)


class TestBelowThreshold:
    @pytest.mark.asyncio
    async def test_small_rebuild_uses_numpy(self, index):
        """Below HNSW_CLUSTER_THRESHOLD, numpy backend is used."""
        centroids = _make_centroids(10)
        await index.rebuild(centroids)
        assert isinstance(index._backend, _NumpyBackend)

    @pytest.mark.asyncio
    async def test_just_below_threshold_uses_numpy(self, index):
        """At threshold - 1, numpy backend is still used."""
        centroids = _make_centroids(HNSW_CLUSTER_THRESHOLD - 1)
        await index.rebuild(centroids)
        assert isinstance(index._backend, _NumpyBackend)


class TestAtOrAboveThreshold:
    @pytest.mark.asyncio
    async def test_at_threshold_uses_hnsw(self, index):
        """At exactly HNSW_CLUSTER_THRESHOLD, HNSW backend is selected."""
        centroids = _make_centroids(HNSW_CLUSTER_THRESHOLD)
        await index.rebuild(centroids)
        assert isinstance(index._backend, _HnswBackend)

    @pytest.mark.asyncio
    async def test_above_threshold_uses_hnsw(self, index):
        """Above HNSW_CLUSTER_THRESHOLD, HNSW backend is selected."""
        centroids = _make_centroids(HNSW_CLUSTER_THRESHOLD + 100)
        await index.rebuild(centroids)
        assert isinstance(index._backend, _HnswBackend)


class TestBackendSwitching:
    @pytest.mark.asyncio
    async def test_numpy_to_hnsw_on_rebuild(self, index):
        """Rebuild from small to large switches numpy -> HNSW."""
        small = _make_centroids(10)
        await index.rebuild(small)
        assert isinstance(index._backend, _NumpyBackend)

        large = _make_centroids(HNSW_CLUSTER_THRESHOLD)
        await index.rebuild(large)
        assert isinstance(index._backend, _HnswBackend)

    @pytest.mark.asyncio
    async def test_hnsw_to_numpy_on_rebuild(self, index):
        """Rebuild from large to small switches HNSW -> numpy."""
        large = _make_centroids(HNSW_CLUSTER_THRESHOLD)
        await index.rebuild(large)
        assert isinstance(index._backend, _HnswBackend)

        small = _make_centroids(10)
        await index.rebuild(small)
        assert isinstance(index._backend, _NumpyBackend)

    @pytest.mark.asyncio
    async def test_rebuild_empty_resets_to_numpy(self, index):
        """Rebuilding with empty centroids resets to numpy."""
        large = _make_centroids(HNSW_CLUSTER_THRESHOLD)
        await index.rebuild(large)
        assert isinstance(index._backend, _HnswBackend)

        await index.rebuild({})
        assert isinstance(index._backend, _NumpyBackend)


class TestUpsertDoesNotSwitchBackend:
    @pytest.mark.asyncio
    async def test_upsert_keeps_numpy_even_at_threshold(self, index):
        """upsert() does NOT trigger backend switch, only rebuild does."""
        # Start with numpy (default)
        assert isinstance(index._backend, _NumpyBackend)

        # Upsert enough items to exceed the threshold
        rng = np.random.RandomState(42)
        for i in range(HNSW_CLUSTER_THRESHOLD + 10):
            v = rng.randn(DIM).astype(np.float32)
            v /= np.linalg.norm(v)
            await index.upsert(f"c-{i}", v)

        # Backend should still be numpy — upsert doesn't trigger switch
        assert isinstance(index._backend, _NumpyBackend)
        assert index.size == HNSW_CLUSTER_THRESHOLD + 10

    @pytest.mark.asyncio
    async def test_upsert_keeps_hnsw(self, index):
        """After rebuild selects HNSW, upserts continue on HNSW."""
        large = _make_centroids(HNSW_CLUSTER_THRESHOLD)
        await index.rebuild(large)
        assert isinstance(index._backend, _HnswBackend)

        # Upsert a new item — backend should stay HNSW
        rng = np.random.RandomState(99)
        v = rng.randn(DIM).astype(np.float32)
        v /= np.linalg.norm(v)
        await index.upsert("extra", v)
        assert isinstance(index._backend, _HnswBackend)
        assert index.size == HNSW_CLUSTER_THRESHOLD + 1


class TestSearchConsistencyAcrossBackends:
    @pytest.mark.asyncio
    async def test_search_works_after_switch_to_hnsw(self, index):
        """After switching to HNSW, search still returns correct results."""
        rng = np.random.RandomState(42)
        centroids = {}
        # Create a distinctive vector we can search for
        target = np.array([1, 0, 0, 0], dtype=np.float32)
        target /= np.linalg.norm(target)
        centroids["target"] = target

        for i in range(HNSW_CLUSTER_THRESHOLD - 1):
            v = rng.randn(DIM).astype(np.float32)
            v /= np.linalg.norm(v)
            centroids[f"c-{i}"] = v

        await index.rebuild(centroids)
        assert isinstance(index._backend, _HnswBackend)

        results = index.search(target, k=1, threshold=0.5)
        assert len(results) >= 1
        assert results[0][0] == "target"
        assert results[0][1] > 0.99
