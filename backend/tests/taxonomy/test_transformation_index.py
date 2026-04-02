"""Unit tests for TransformationIndex.

Covers:
1. upsert and search — insert vector, find it by cosine similarity
2. remove — delete from index, verify size=0
3. snapshot/restore round-trip
4. get_vector returns the stored vector
5. get_vector returns None for missing cluster
6. rebuild from dict
"""

import asyncio
import inspect

import numpy as np
import pytest

from app.services.taxonomy.transformation_index import (
    TransformationIndex,
    TransformationSnapshot,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rand_emb(dim: int = 384, seed: int | None = None) -> np.ndarray:
    rng = np.random.RandomState(seed)
    v = rng.randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def index() -> TransformationIndex:
    return TransformationIndex(dim=384)


# ---------------------------------------------------------------------------
# Structural: async interface and dataclass
# ---------------------------------------------------------------------------


def test_upsert_is_coroutine(index: TransformationIndex):
    assert inspect.iscoroutinefunction(index.upsert)


def test_remove_is_coroutine(index: TransformationIndex):
    assert inspect.iscoroutinefunction(index.remove)


def test_rebuild_is_coroutine(index: TransformationIndex):
    assert inspect.iscoroutinefunction(index.rebuild)


def test_snapshot_is_coroutine(index: TransformationIndex):
    assert inspect.iscoroutinefunction(index.snapshot)


def test_restore_is_coroutine(index: TransformationIndex):
    assert inspect.iscoroutinefunction(index.restore)


def test_transformation_snapshot_is_dataclass():
    import dataclasses
    assert dataclasses.is_dataclass(TransformationSnapshot)


def test_transformation_snapshot_fields():
    mat = np.eye(2, dtype=np.float32)
    ids = ["a", "b"]
    snap = TransformationSnapshot(matrix=mat, ids=ids)
    assert np.array_equal(snap.matrix, mat)
    assert snap.ids == ids


# ---------------------------------------------------------------------------
# 1. upsert and search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_and_search_basic(index: TransformationIndex):
    """Insert a vector, then search with a similar query and find it."""
    emb = _rand_emb(seed=1)
    await index.upsert("cluster-1", emb)

    assert index.size == 1

    results = index.search(emb, k=5, threshold=0.50)
    assert len(results) == 1
    cluster_id, score = results[0]
    assert cluster_id == "cluster-1"
    assert score > 0.99  # near-identical query should yield near-1.0


@pytest.mark.asyncio
async def test_upsert_multiple_search_returns_top_k(index: TransformationIndex):
    """Multiple inserts; search returns at most k results above threshold."""
    embeddings = {f"c{i}": _rand_emb(seed=i) for i in range(10)}
    for cid, emb in embeddings.items():
        await index.upsert(cid, emb)

    assert index.size == 10

    query = _rand_emb(seed=0)  # same direction as "c0"
    results = index.search(query, k=3, threshold=0.0)
    assert len(results) <= 3
    # Results must be sorted descending
    scores = [s for _, s in results]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_search_below_threshold_returns_empty(index: TransformationIndex):
    """search() returns [] when no vector meets the threshold."""
    emb = _rand_emb(seed=5)
    await index.upsert("c1", emb)

    # Use a threshold higher than any possible match
    results = index.search(emb, k=5, threshold=1.01)
    assert results == []


@pytest.mark.asyncio
async def test_search_empty_index_returns_empty(index: TransformationIndex):
    """search() on an empty index returns []."""
    query = _rand_emb(seed=99)
    results = index.search(query)
    assert results == []


@pytest.mark.asyncio
async def test_upsert_update_existing(index: TransformationIndex):
    """Upserting an existing cluster_id updates its vector in place."""
    emb_old = _rand_emb(seed=10)
    emb_new = _rand_emb(seed=11)

    await index.upsert("cluster-x", emb_old)
    assert index.size == 1

    await index.upsert("cluster-x", emb_new)
    assert index.size == 1  # count unchanged

    # The stored vector should now match emb_new
    stored = index.get_vector("cluster-x")
    assert stored is not None
    assert np.allclose(stored, emb_new / np.linalg.norm(emb_new), atol=1e-6)


# ---------------------------------------------------------------------------
# 2. remove
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_reduces_size(index: TransformationIndex):
    """Removing a cluster_id decreases size by 1."""
    await index.upsert("a", _rand_emb(seed=1))
    await index.upsert("b", _rand_emb(seed=2))
    assert index.size == 2

    await index.remove("a")
    assert index.size == 1


@pytest.mark.asyncio
async def test_remove_last_entry_gives_empty_index(index: TransformationIndex):
    """Removing the only entry leaves an empty index."""
    await index.upsert("solo", _rand_emb(seed=42))
    await index.remove("solo")

    assert index.size == 0
    results = index.search(_rand_emb(seed=42), threshold=0.0)
    assert results == []


@pytest.mark.asyncio
async def test_remove_nonexistent_is_noop(index: TransformationIndex):
    """Removing a cluster_id that doesn't exist is a no-op."""
    await index.upsert("a", _rand_emb(seed=1))
    await index.remove("does-not-exist")
    assert index.size == 1


@pytest.mark.asyncio
async def test_remove_makes_cluster_unsearchable(index: TransformationIndex):
    """After removal the deleted cluster no longer appears in search results."""
    emb = _rand_emb(seed=7)
    await index.upsert("target", emb)
    await index.upsert("other", _rand_emb(seed=8))

    await index.remove("target")

    results = index.search(emb, k=10, threshold=0.0)
    assert not any(cid == "target" for cid, _ in results)


# ---------------------------------------------------------------------------
# 3. snapshot / restore round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_empty_index(index: TransformationIndex):
    """Snapshot of empty index has empty matrix and ids."""
    snap = await index.snapshot()
    assert isinstance(snap, TransformationSnapshot)
    assert snap.matrix.shape == (0, 384)
    assert snap.ids == []


@pytest.mark.asyncio
async def test_snapshot_is_deep_copy(index: TransformationIndex):
    """Mutations after snapshot do not affect the snapshot."""
    await index.upsert("a", _rand_emb(seed=1))
    snap = await index.snapshot()

    await index.upsert("b", _rand_emb(seed=2))

    assert snap.matrix.shape == (1, 384)
    assert snap.ids == ["a"]


@pytest.mark.asyncio
async def test_restore_recovers_state(index: TransformationIndex):
    """After mutating the index, restore brings it back to the snapshotted state."""
    emb_a = _rand_emb(seed=100)
    emb_b = _rand_emb(seed=101)
    await index.upsert("a", emb_a)
    await index.upsert("b", emb_b)

    snap = await index.snapshot()
    assert snap.matrix.shape == (2, 384)

    # Mutate
    await index.upsert("c", _rand_emb(seed=102))
    await index.remove("a")
    assert index.size == 2

    # Restore
    await index.restore(snap)

    assert index.size == 2
    assert set(index._ids) == {"a", "b"}
    assert index._matrix.shape == (2, 384)


@pytest.mark.asyncio
async def test_restore_snapshot_independence(index: TransformationIndex):
    """Corrupting snapshot matrix after restore does not affect live index."""
    await index.upsert("a", _rand_emb(seed=5))
    snap = await index.snapshot()
    await index.restore(snap)

    snap.matrix[:] = 0.0

    assert not np.allclose(index._matrix, 0.0)


@pytest.mark.asyncio
async def test_roundtrip_search_consistency(index: TransformationIndex):
    """Search results after restore match the original snapshotted state."""
    emb_a = _rand_emb(seed=200)
    await index.upsert("a", emb_a)
    snap = await index.snapshot()

    # Mutate: remove original, add unrelated
    await index.remove("a")
    await index.upsert("noise", _rand_emb(seed=201))

    # Restore
    await index.restore(snap)

    results = index.search(emb_a, k=5, threshold=0.5)
    assert len(results) == 1
    assert results[0][0] == "a"
    assert results[0][1] > 0.99


# ---------------------------------------------------------------------------
# 4. get_vector returns stored vector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_vector_returns_normalized_vector(index: TransformationIndex):
    """get_vector returns the L2-normalized form of the inserted vector."""
    raw = np.array([3.0, 4.0] + [0.0] * 382, dtype=np.float32)
    await index.upsert("cluster-norm", raw)

    stored = index.get_vector("cluster-norm")
    assert stored is not None
    # Should be unit-normalized
    assert abs(np.linalg.norm(stored) - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_get_vector_returns_copy(index: TransformationIndex):
    """get_vector returns a copy; mutating it does not corrupt the index."""
    emb = _rand_emb(seed=300)
    await index.upsert("c", emb)

    stored = index.get_vector("c")
    stored[:] = 0.0  # corrupt the copy

    # Verify index is unaffected
    stored2 = index.get_vector("c")
    assert not np.allclose(stored2, 0.0)


@pytest.mark.asyncio
async def test_get_vector_after_update(index: TransformationIndex):
    """get_vector reflects the latest upserted vector, not the original."""
    emb_v1 = _rand_emb(seed=400)
    emb_v2 = _rand_emb(seed=401)

    await index.upsert("c", emb_v1)
    await index.upsert("c", emb_v2)

    stored = index.get_vector("c")
    expected = emb_v2 / np.linalg.norm(emb_v2)
    assert np.allclose(stored, expected, atol=1e-6)


# ---------------------------------------------------------------------------
# 5. get_vector returns None for missing cluster
# ---------------------------------------------------------------------------


def test_get_vector_missing_returns_none(index: TransformationIndex):
    """get_vector on an empty index returns None."""
    assert index.get_vector("nonexistent") is None


@pytest.mark.asyncio
async def test_get_vector_after_remove_returns_none(index: TransformationIndex):
    """get_vector returns None after the cluster is removed."""
    await index.upsert("c", _rand_emb(seed=500))
    await index.remove("c")

    assert index.get_vector("c") is None


# ---------------------------------------------------------------------------
# 6. rebuild from dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebuild_from_dict(index: TransformationIndex):
    """rebuild() populates the index from a dict of vectors."""
    vectors = {f"c{i}": _rand_emb(seed=i) for i in range(5)}
    await index.rebuild(vectors)

    assert index.size == 5
    for cid in vectors:
        v = index.get_vector(cid)
        assert v is not None
        assert abs(np.linalg.norm(v) - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_rebuild_empty_dict_clears_index(index: TransformationIndex):
    """rebuild() with empty dict clears all entries."""
    await index.upsert("a", _rand_emb(seed=1))
    await index.rebuild({})

    assert index.size == 0
    assert index._ids == []
    assert index._matrix.shape == (0, 384)


@pytest.mark.asyncio
async def test_rebuild_replaces_existing_content(index: TransformationIndex):
    """rebuild() discards previous entries and starts fresh."""
    await index.upsert("old-1", _rand_emb(seed=10))
    await index.upsert("old-2", _rand_emb(seed=11))

    new_vectors = {"new-a": _rand_emb(seed=20), "new-b": _rand_emb(seed=21)}
    await index.rebuild(new_vectors)

    assert index.size == 2
    assert set(index._ids) == {"new-a", "new-b"}
    assert index.get_vector("old-1") is None
    assert index.get_vector("old-2") is None


@pytest.mark.asyncio
async def test_rebuild_zero_norm_vector_is_stored_as_zeros(index: TransformationIndex):
    """rebuild() handles zero-norm vectors without crashing (stores zero row)."""
    zero_vec = np.zeros(384, dtype=np.float32)
    normal_vec = _rand_emb(seed=99)

    await index.rebuild({"zero": zero_vec, "normal": normal_vec})

    assert index.size == 2
    # Zero-norm entry stored as zero row; normal entry is normalized
    stored_normal = index.get_vector("normal")
    assert abs(np.linalg.norm(stored_normal) - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# Concurrent correctness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_upserts_do_not_corrupt():
    """Concurrent upserts do not corrupt the index state."""
    index = TransformationIndex(dim=384)

    async def do_upsert(i: int):
        await index.upsert(f"c{i}", _rand_emb(seed=i))

    await asyncio.gather(*[do_upsert(i) for i in range(20)])

    assert index.size == 20
    # Matrix rows must equal ids length
    assert index._matrix.shape == (20, 384)


# ---------------------------------------------------------------------------
# 7. save_cache / load_cache
# ---------------------------------------------------------------------------

import time
from pathlib import Path


@pytest.mark.asyncio
async def test_save_and_load_cache_round_trip(index: TransformationIndex, tmp_path: Path):
    """Save/load round-trip preserves vectors."""
    v1 = _rand_emb(seed=10)
    v2 = _rand_emb(seed=20)
    await index.upsert("c1", v1)
    await index.upsert("c2", v2)

    cache_path = tmp_path / "transformation_index.pkl"
    await index.save_cache(cache_path)
    assert cache_path.exists()

    fresh = TransformationIndex(dim=384)
    loaded = await fresh.load_cache(cache_path)
    assert loaded is True
    assert fresh.size == 2

    restored_v1 = fresh.get_vector("c1")
    assert restored_v1 is not None
    assert np.dot(restored_v1, v1 / np.linalg.norm(v1)) > 0.99


@pytest.mark.asyncio
async def test_load_cache_rejects_stale(index: TransformationIndex, tmp_path: Path):
    """Cache older than max_age_seconds is rejected."""
    await index.upsert("c1", _rand_emb(seed=1))
    cache_path = tmp_path / "transformation_index.pkl"
    await index.save_cache(cache_path)

    fresh = TransformationIndex(dim=384)
    loaded = await fresh.load_cache(cache_path, max_age_seconds=0)
    assert loaded is False
    assert fresh.size == 0


@pytest.mark.asyncio
async def test_load_cache_missing_file(index: TransformationIndex, tmp_path: Path):
    """Missing file returns False without error."""
    loaded = await index.load_cache(tmp_path / "nonexistent.pkl")
    assert loaded is False
