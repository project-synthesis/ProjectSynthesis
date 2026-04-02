"""Unit tests for OptimizedEmbeddingIndex save_cache / load_cache."""

from pathlib import Path

import numpy as np
import pytest

from app.services.taxonomy.optimized_index import OptimizedEmbeddingIndex


def _rand_emb(dim: int = 384, seed: int | None = None) -> np.ndarray:
    rng = np.random.RandomState(seed)
    v = rng.randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def index() -> OptimizedEmbeddingIndex:
    return OptimizedEmbeddingIndex(dim=384)


@pytest.mark.asyncio
async def test_save_and_load_cache_round_trip(
    index: OptimizedEmbeddingIndex, tmp_path: Path
):
    """Save/load round-trip preserves vectors."""
    v1 = _rand_emb(seed=10)
    v2 = _rand_emb(seed=20)
    await index.upsert("c1", v1)
    await index.upsert("c2", v2)

    cache_path = tmp_path / "optimized_index.pkl"
    await index.save_cache(cache_path)
    assert cache_path.exists()

    fresh = OptimizedEmbeddingIndex(dim=384)
    loaded = await fresh.load_cache(cache_path)
    assert loaded is True
    assert fresh.size == 2

    restored_v1 = fresh.get_vector("c1")
    assert restored_v1 is not None
    assert np.dot(restored_v1, v1 / np.linalg.norm(v1)) > 0.99


@pytest.mark.asyncio
async def test_load_cache_rejects_stale(
    index: OptimizedEmbeddingIndex, tmp_path: Path
):
    """Cache older than max_age_seconds is rejected."""
    await index.upsert("c1", _rand_emb(seed=1))
    cache_path = tmp_path / "optimized_index.pkl"
    await index.save_cache(cache_path)

    fresh = OptimizedEmbeddingIndex(dim=384)
    loaded = await fresh.load_cache(cache_path, max_age_seconds=0)
    assert loaded is False
    assert fresh.size == 0


@pytest.mark.asyncio
async def test_load_cache_missing_file(
    index: OptimizedEmbeddingIndex, tmp_path: Path
):
    """Missing file returns False without error."""
    loaded = await index.load_cache(tmp_path / "nonexistent.pkl")
    assert loaded is False
