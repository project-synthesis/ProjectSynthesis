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
