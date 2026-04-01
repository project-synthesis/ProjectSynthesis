"""Tests for the in-memory numpy embedding index."""
import numpy as np
import pytest

from app.services.taxonomy.embedding_index import EmbeddingIndex


@pytest.fixture
def index():
    return EmbeddingIndex(dim=384)


def _rand_emb(dim=384):
    v = np.random.randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def test_empty_search(index):
    results = index.search(_rand_emb(), k=5)
    assert results == []


@pytest.mark.asyncio
async def test_upsert_and_search(index):
    emb = _rand_emb()
    await index.upsert("a", emb)
    results = index.search(emb, k=1, threshold=0.5)
    assert len(results) == 1
    assert results[0][0] == "a"
    assert results[0][1] > 0.99  # near-identical


@pytest.mark.asyncio
async def test_remove(index):
    emb = _rand_emb()
    await index.upsert("a", emb)
    await index.remove("a")
    results = index.search(emb, k=1, threshold=0.5)
    assert results == []


@pytest.mark.asyncio
async def test_threshold_filtering(index):
    e1 = _rand_emb()
    e2 = _rand_emb()  # random = ~0 cosine to e1
    await index.upsert("a", e1)
    await index.upsert("b", e2)
    results = index.search(e1, k=5, threshold=0.8)
    assert len(results) == 1
    assert results[0][0] == "a"


@pytest.mark.asyncio
async def test_rebuild(index):
    e1, e2 = _rand_emb(), _rand_emb()
    await index.upsert("old", _rand_emb())
    await index.rebuild({"a": e1, "b": e2})
    results = index.search(e1, k=5, threshold=0.5)
    ids = [r[0] for r in results]
    assert "a" in ids
    assert "old" not in ids


@pytest.mark.asyncio
async def test_scale_500_clusters(index):
    """Search over 500 clusters completes in <10ms."""
    import time
    for i in range(500):
        await index.upsert(f"c{i}", _rand_emb())
    query = _rand_emb()
    start = time.perf_counter()
    results = index.search(query, k=5, threshold=0.3)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 10, f"Search took {elapsed_ms:.1f}ms, expected <10ms"
    assert len(results) <= 5


# ---------------------------------------------------------------------------
# pairwise_similarities tests
# ---------------------------------------------------------------------------


def test_pairwise_empty(index):
    """Empty index returns no edges."""
    assert index.pairwise_similarities(threshold=0.5, k=10) == []


@pytest.mark.asyncio
async def test_pairwise_single_item(index):
    """Single item returns no edges (need at least 2)."""
    await index.upsert("a", _rand_emb())
    assert index.pairwise_similarities(threshold=0.0, k=10) == []


@pytest.mark.asyncio
async def test_pairwise_identical_vectors(index):
    """Two identical vectors should have similarity ~1.0."""
    emb = _rand_emb()
    await index.upsert("a", emb)
    await index.upsert("b", emb)
    pairs = index.pairwise_similarities(threshold=0.9, k=10)
    assert len(pairs) == 1
    assert pairs[0][0] == "a"
    assert pairs[0][1] == "b"
    assert pairs[0][2] > 0.99


@pytest.mark.asyncio
async def test_pairwise_threshold_filtering(index):
    """Random vectors should mostly be below high threshold."""
    for i in range(10):
        await index.upsert(f"c{i}", _rand_emb())
    # Very high threshold should yield few or no edges (random 384-dim vectors)
    pairs = index.pairwise_similarities(threshold=0.95, k=100)
    for _, _, score in pairs:
        assert score >= 0.95


@pytest.mark.asyncio
async def test_pairwise_k_truncation(index):
    """Result list is truncated to k."""
    emb = _rand_emb()
    # Insert 5 near-identical vectors
    for i in range(5):
        noise = np.random.randn(384).astype(np.float32) * 0.01
        await index.upsert(f"c{i}", emb + noise)
    # 5 items = 10 upper-triangle pairs; request k=3
    pairs = index.pairwise_similarities(threshold=0.0, k=3)
    assert len(pairs) <= 3


@pytest.mark.asyncio
async def test_pairwise_sorted_descending(index):
    """Edges are sorted by similarity descending."""
    emb = _rand_emb()
    await index.upsert("a", emb)
    await index.upsert("b", emb + np.random.randn(384).astype(np.float32) * 0.01)
    await index.upsert("c", emb + np.random.randn(384).astype(np.float32) * 0.5)
    pairs = index.pairwise_similarities(threshold=0.0, k=100)
    scores = [s for _, _, s in pairs]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_pairwise_no_duplicates(index):
    """Each pair appears only once (upper triangle)."""
    for i in range(5):
        await index.upsert(f"c{i}", _rand_emb())
    pairs = index.pairwise_similarities(threshold=0.0, k=100)
    pair_set = set()
    for a, b, _ in pairs:
        key = tuple(sorted([a, b]))
        assert key not in pair_set, f"Duplicate pair: {key}"
        pair_set.add(key)


@pytest.mark.asyncio
async def test_pairwise_no_self_edges(index):
    """Diagonal is zeroed — no self-similarity edges."""
    for i in range(5):
        await index.upsert(f"c{i}", _rand_emb())
    pairs = index.pairwise_similarities(threshold=0.0, k=100)
    for a, b, _ in pairs:
        assert a != b
