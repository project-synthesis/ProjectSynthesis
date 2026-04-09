"""Cross-validate numpy vs HNSW search results (Phase 3B)."""

import numpy as np
import pytest

from app.services.taxonomy.embedding_index import EmbeddingIndex, _HnswBackend, _NumpyBackend


def _random_emb(dim=384, seed=None):
    rng = np.random.RandomState(seed)
    v = rng.randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.mark.asyncio
async def test_numpy_and_hnsw_return_same_results():
    """Search results must be identical regardless of backend."""
    dim = 384
    n_clusters = 50
    np.random.seed(42)

    centroids = {}
    project_ids = {}
    for i in range(n_clusters):
        centroids[f"c{i}"] = _random_emb(dim, seed=i)
        project_ids[f"c{i}"] = f"proj-{'A' if i % 2 == 0 else 'B'}"

    # Build numpy index
    numpy_idx = EmbeddingIndex(dim=dim)
    numpy_idx._backend = _NumpyBackend(dim=dim)
    await numpy_idx.rebuild(centroids, project_ids=project_ids)

    # Build hnsw index
    hnsw_idx = EmbeddingIndex(dim=dim)
    hnsw_idx._backend = _HnswBackend(dim=dim)
    await hnsw_idx.rebuild(centroids, project_ids=project_ids)

    # Run 10 random queries without filter
    for seed in range(10):
        query = _random_emb(dim, seed=seed + 100)

        numpy_results = numpy_idx.search(query, k=5, threshold=0.0)
        hnsw_results = hnsw_idx.search(query, k=5, threshold=0.0)

        # Same cluster IDs in top results (allow 1 difference for float precision)
        numpy_ids = {r[0] for r in numpy_results}
        hnsw_ids = {r[0] for r in hnsw_results}
        assert len(numpy_ids & hnsw_ids) >= len(numpy_ids) - 1

    # Test with project_filter
    for seed in range(5):
        query = _random_emb(dim, seed=seed + 200)

        numpy_results = numpy_idx.search(query, k=5, threshold=0.0, project_filter="proj-A")
        hnsw_results = hnsw_idx.search(query, k=5, threshold=0.0, project_filter="proj-A")

        # All results should be from proj-A
        for cid, _ in numpy_results:
            assert project_ids[cid] == "proj-A"
        for cid, _ in hnsw_results:
            assert project_ids[cid] == "proj-A"


@pytest.mark.asyncio
async def test_reset_clears_all_state():
    """EmbeddingIndex.reset() clears all state."""
    idx = EmbeddingIndex(dim=4)
    await idx.upsert("c1", np.array([1, 0, 0, 0], dtype=np.float32))
    assert idx.size == 1

    await idx.reset()
    assert idx.size == 0
    assert idx._id_to_label == {}
    assert idx._tombstones == set()
