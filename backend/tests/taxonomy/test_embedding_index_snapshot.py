"""Unit tests for EmbeddingIndex snapshot/restore functionality.

Covers:
- IndexSnapshot dataclass fields
- snapshot() produces a deep copy (mutations do not affect the snapshot)
- restore() atomically swaps state back to the snapshot
- round-trip: snapshot → mutate → restore → verify original state
- search results are consistent after restore
- snapshot() and restore() are coroutines (acquire the async lock)
- rebuild() already acquires the lock (structural check)
"""

import asyncio
import inspect

import numpy as np
import pytest

from app.services.taxonomy.embedding_index import EmbeddingIndex, IndexSnapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rand_emb(dim: int = 384, seed: int | None = None) -> np.ndarray:
    rng = np.random.RandomState(seed)
    v = rng.randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def index() -> EmbeddingIndex:
    return EmbeddingIndex(dim=384)


# ---------------------------------------------------------------------------
# IndexSnapshot dataclass
# ---------------------------------------------------------------------------


def test_index_snapshot_dataclass_fields():
    """IndexSnapshot must expose matrix and ids fields."""
    mat = np.eye(3, dtype=np.float32)
    ids = ["a", "b", "c"]
    snap = IndexSnapshot(matrix=mat, ids=ids)
    assert np.array_equal(snap.matrix, mat)
    assert snap.ids == ids


def test_index_snapshot_is_dataclass():
    """IndexSnapshot should be a dataclass (not a plain dict or namedtuple)."""
    import dataclasses
    assert dataclasses.is_dataclass(IndexSnapshot)


# ---------------------------------------------------------------------------
# snapshot() — coroutine semantics and lock acquisition
# ---------------------------------------------------------------------------


def test_snapshot_is_coroutine(index: EmbeddingIndex):
    """snapshot() must be an async method (coroutine function)."""
    assert inspect.iscoroutinefunction(index.snapshot)


def test_restore_is_coroutine(index: EmbeddingIndex):
    """restore() must be an async method (coroutine function)."""
    assert inspect.iscoroutinefunction(index.restore)


def test_rebuild_is_coroutine(index: EmbeddingIndex):
    """rebuild() already acquires the lock — verify it is async."""
    assert inspect.iscoroutinefunction(index.rebuild)


# ---------------------------------------------------------------------------
# snapshot() — deep copy semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_of_empty_index(index: EmbeddingIndex):
    """Snapshot of an empty index returns empty matrix and ids."""
    snap = await index.snapshot()
    assert isinstance(snap, IndexSnapshot)
    assert snap.matrix.shape == (0, 384)
    assert snap.ids == []


@pytest.mark.asyncio
async def test_snapshot_copies_matrix(index: EmbeddingIndex):
    """Snapshot matrix is a copy — mutations to the index don't affect it."""
    emb = _rand_emb(seed=1)
    await index.upsert("a", emb)
    snap = await index.snapshot()

    # Mutate index after snapshot
    await index.upsert("b", _rand_emb(seed=2))

    # Snapshot must still reflect the original single-entry state
    assert snap.matrix.shape == (1, 384)
    assert snap.ids == ["a"]


@pytest.mark.asyncio
async def test_snapshot_copies_ids(index: EmbeddingIndex):
    """Snapshot ids list is a copy — modifications don't bleed back."""
    await index.upsert("x", _rand_emb(seed=10))
    snap = await index.snapshot()

    # Modify the snapshot's ids list directly
    snap.ids.append("injected")

    # Index internal list must be unchanged
    assert "injected" not in index._ids


@pytest.mark.asyncio
async def test_snapshot_matrix_independence(index: EmbeddingIndex):
    """Writing into the snapshot matrix does not corrupt the live index."""
    emb = _rand_emb(seed=20)
    await index.upsert("c", emb)
    snap = await index.snapshot()

    # Zero out the snapshot's matrix
    snap.matrix[:] = 0.0

    # The live index matrix must be untouched
    assert not np.allclose(index._matrix, 0.0)


# ---------------------------------------------------------------------------
# restore() — atomic swap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restore_empty_snapshot(index: EmbeddingIndex):
    """Restoring an empty snapshot clears the index."""
    await index.upsert("a", _rand_emb(seed=1))
    empty_snap = IndexSnapshot(matrix=np.empty((0, 384), dtype=np.float32), ids=[])
    await index.restore(empty_snap)

    assert index.size == 0
    assert index._ids == []
    assert index._matrix.shape == (0, 384)


@pytest.mark.asyncio
async def test_restore_matrix_is_independent_copy(index: EmbeddingIndex):
    """After restore, mutating the snapshot's matrix must not corrupt the index."""
    emb = _rand_emb(seed=5)
    await index.upsert("a", emb)
    snap = await index.snapshot()
    await index.upsert("b", _rand_emb(seed=6))   # mutate after snapshot

    # Restore to single-entry state
    await index.restore(snap)

    # Now corrupt the snapshot's matrix
    snap.matrix[:] = 0.0

    # Index must be unaffected
    assert not np.allclose(index._matrix, 0.0)
    assert index.size == 1


# ---------------------------------------------------------------------------
# Round-trip: snapshot → mutate → restore → verify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_roundtrip_upsert(index: EmbeddingIndex):
    """Snapshot before upserts, then restore, original state is recovered."""
    # Populate initial state
    emb_a = _rand_emb(seed=100)
    emb_b = _rand_emb(seed=101)
    await index.upsert("a", emb_a)
    await index.upsert("b", emb_b)

    # Capture snapshot
    snap = await index.snapshot()
    assert snap.matrix.shape == (2, 384)
    assert set(snap.ids) == {"a", "b"}

    # Mutate: add more entries
    await index.upsert("c", _rand_emb(seed=102))
    await index.upsert("d", _rand_emb(seed=103))
    assert index.size == 4

    # Restore
    await index.restore(snap)

    # Verify original shape
    assert index.size == 2
    assert set(index._ids) == {"a", "b"}
    assert index._matrix.shape == (2, 384)


@pytest.mark.asyncio
async def test_roundtrip_remove(index: EmbeddingIndex):
    """Snapshot before removes, then restore, removed entry is recovered."""
    emb_a = _rand_emb(seed=200)
    emb_b = _rand_emb(seed=201)
    await index.upsert("a", emb_a)
    await index.upsert("b", emb_b)

    snap = await index.snapshot()

    # Remove an entry
    await index.remove("a")
    assert index.size == 1

    # Restore
    await index.restore(snap)

    assert index.size == 2
    assert "a" in index._ids
    assert "b" in index._ids


@pytest.mark.asyncio
async def test_roundtrip_rebuild(index: EmbeddingIndex):
    """Snapshot before rebuild, then restore, original entries are recovered."""
    emb_a = _rand_emb(seed=300)
    emb_b = _rand_emb(seed=301)
    await index.upsert("a", emb_a)
    await index.upsert("b", emb_b)

    snap = await index.snapshot()

    # Full rebuild with different contents
    await index.rebuild({"x": _rand_emb(seed=302), "y": _rand_emb(seed=303)})
    assert "x" in index._ids
    assert "a" not in index._ids

    # Restore
    await index.restore(snap)

    assert set(index._ids) == {"a", "b"}
    assert "x" not in index._ids


# ---------------------------------------------------------------------------
# Search consistency after restore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_after_restore(index: EmbeddingIndex):
    """Search results are consistent with original index state after restore."""
    emb_a = _rand_emb(seed=400)
    emb_b = _rand_emb(seed=401)   # unrelated direction
    await index.upsert("a", emb_a)

    snap = await index.snapshot()

    # Add a noise cluster and remove the original
    await index.upsert("b", emb_b)
    await index.remove("a")

    results_mutated = index.search(emb_a, k=5, threshold=0.5)
    # "a" should not appear after mutation
    assert not any(r[0] == "a" for r in results_mutated)

    # Restore and search again
    await index.restore(snap)
    results_restored = index.search(emb_a, k=5, threshold=0.5)
    assert len(results_restored) == 1
    assert results_restored[0][0] == "a"
    assert results_restored[0][1] > 0.99


# ---------------------------------------------------------------------------
# Concurrent correctness: lock prevents interleaving
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_and_upsert_do_not_interleave():
    """snapshot() and upsert() serialize correctly under concurrent calls."""
    index = EmbeddingIndex(dim=384)
    for i in range(10):
        await index.upsert(f"c{i}", _rand_emb(seed=i))

    # Fire snapshot and a batch of upserts concurrently
    snap_task = asyncio.create_task(index.snapshot())
    upsert_tasks = [
        asyncio.create_task(index.upsert(f"extra{i}", _rand_emb(seed=100 + i)))
        for i in range(5)
    ]

    snap = await snap_task
    await asyncio.gather(*upsert_tasks)

    # Snapshot must reflect a consistent point in time (10 or 15 entries, not partial)
    assert snap.matrix.shape[0] == len(snap.ids)
    assert snap.matrix.shape[1] == 384
    # All snapshot ids are consistent
    for idx, cid in enumerate(snap.ids):
        assert snap.matrix[idx].shape == (384,)
