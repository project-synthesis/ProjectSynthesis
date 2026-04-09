"""Tests for EmbeddingIndex snapshot/restore with label mapping.

Covers:
- Snapshot captures id_to_label, next_label, tombstones
- Restore recovers label mapping correctly
- Restore after remove (with tombstones) works
- Legacy snapshot (no id_to_label fields) loads with fallback
- Restore rebuilds backend from matrix
- Multiple snapshot/restore cycles don't corrupt state
"""

import numpy as np
import pytest

from app.services.taxonomy.embedding_index import (
    EmbeddingIndex,
    IndexSnapshot,
    _NumpyBackend,
)

DIM = 4


def _norm(v):
    v = np.array(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def _random_emb(seed: int, dim: int = DIM) -> np.ndarray:
    rng = np.random.RandomState(seed)
    v = rng.randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def index():
    return EmbeddingIndex(dim=DIM)


class TestSnapshotCapturesLabelMapping:
    @pytest.mark.asyncio
    async def test_snapshot_has_id_to_label(self, index):
        await index.upsert("a", _random_emb(1))
        await index.upsert("b", _random_emb(2))
        snap = await index.snapshot()
        assert snap.id_to_label == {"a": 0, "b": 1}

    @pytest.mark.asyncio
    async def test_snapshot_has_next_label(self, index):
        await index.upsert("a", _random_emb(1))
        await index.upsert("b", _random_emb(2))
        snap = await index.snapshot()
        assert snap.next_label == 2

    @pytest.mark.asyncio
    async def test_snapshot_has_tombstones(self, index):
        await index.upsert("a", _random_emb(1))
        await index.upsert("b", _random_emb(2))
        await index.remove("a")
        snap = await index.snapshot()
        assert snap.tombstones == {0}
        assert "a" not in snap.id_to_label

    @pytest.mark.asyncio
    async def test_snapshot_is_deep_copy(self, index):
        """Mutating snapshot's id_to_label doesn't affect the index."""
        await index.upsert("x", _random_emb(10))
        snap = await index.snapshot()
        snap.id_to_label["injected"] = 999
        assert "injected" not in index._id_to_label


class TestRestoreRecovery:
    @pytest.mark.asyncio
    async def test_restore_recovers_label_mapping(self, index):
        await index.upsert("a", _random_emb(1))
        await index.upsert("b", _random_emb(2))
        snap = await index.snapshot()

        # Mutate
        await index.upsert("c", _random_emb(3))
        await index.remove("a")

        # Restore
        await index.restore(snap)
        assert index._id_to_label == {"a": 0, "b": 1}
        assert index._next_label == 2
        assert index._tombstones == set()
        assert index.size == 2

    @pytest.mark.asyncio
    async def test_restore_after_remove_with_tombstones(self, index):
        """Snapshot taken after remove preserves tombstones on restore."""
        await index.upsert("a", _random_emb(1))
        await index.upsert("b", _random_emb(2))
        await index.remove("a")
        snap = await index.snapshot()

        # Mutate further
        await index.upsert("c", _random_emb(3))
        assert index.size == 2  # b + c

        # Restore to post-remove state
        await index.restore(snap)
        assert index.size == 1  # only b
        assert index._tombstones == {0}
        assert "a" not in index._id_to_label
        assert index._id_to_label == {"b": 1}

    @pytest.mark.asyncio
    async def test_restore_rebuilds_backend(self, index):
        """restore() rebuilds backend from snapshot matrix."""
        emb_a = _norm([1, 0, 0, 0])
        emb_b = _norm([0, 1, 0, 0])
        await index.upsert("a", emb_a)
        await index.upsert("b", emb_b)
        snap = await index.snapshot()

        # Clear everything
        await index.reset()
        assert index.size == 0

        # Restore
        await index.restore(snap)
        assert isinstance(index._backend, _NumpyBackend)
        # Verify search works on the restored backend
        results = index.search(emb_a, k=1, threshold=0.9)
        assert len(results) == 1
        assert results[0][0] == "a"

    @pytest.mark.asyncio
    async def test_restore_always_uses_numpy_backend(self, index):
        """restore() always restores to numpy backend (even if HNSW was active)."""
        # The implementation explicitly restores to numpy for simplicity
        await index.upsert("a", _random_emb(1))
        snap = await index.snapshot()
        await index.restore(snap)
        assert isinstance(index._backend, _NumpyBackend)


class TestLegacySnapshot:
    @pytest.mark.asyncio
    async def test_legacy_snapshot_no_id_to_label(self, index):
        """Snapshot without id_to_label fields uses fallback reconstruction."""
        matrix = np.vstack([_norm([1, 0, 0, 0]), _norm([0, 1, 0, 0])])
        legacy_snap = IndexSnapshot(
            matrix=matrix,
            ids=["a", "b"],
            project_ids=["proj-X", None],
            # id_to_label defaults to empty dict, next_label to 0,
            # tombstones to empty set — triggers legacy fallback path
        )

        await index.restore(legacy_snap)
        # Fallback should reconstruct from ids
        assert index._id_to_label == {"a": 0, "b": 1}
        assert index._next_label == 2
        assert index._tombstones == set()

    @pytest.mark.asyncio
    async def test_legacy_snapshot_with_none_ids(self, index):
        """Legacy snapshot with None entries in ids creates tombstones."""
        matrix = np.vstack([
            _norm([1, 0, 0, 0]),
            _norm([0, 1, 0, 0]),
            _norm([0, 0, 1, 0]),
        ])
        legacy_snap = IndexSnapshot(
            matrix=matrix,
            ids=["a", None, "c"],
            project_ids=[None, None, None],
            # Empty id_to_label → triggers fallback
        )

        await index.restore(legacy_snap)
        assert index._id_to_label == {"a": 0, "c": 2}
        assert index._next_label == 3
        assert 1 in index._tombstones

    @pytest.mark.asyncio
    async def test_legacy_snapshot_no_project_ids(self, index):
        """Legacy snapshot without project_ids defaults to None list."""
        matrix = np.vstack([_norm([1, 0, 0, 0])])
        legacy_snap = IndexSnapshot(
            matrix=matrix,
            ids=["a"],
            # project_ids defaults to empty list (falsy) → triggers fallback
        )

        await index.restore(legacy_snap)
        assert index._project_ids == [None]


class TestMultipleSnapshotRestoreCycles:
    @pytest.mark.asyncio
    async def test_multiple_cycles(self, index):
        """Multiple snapshot/restore cycles don't corrupt state."""
        emb_a = _norm([1, 0, 0, 0])
        emb_b = _norm([0, 1, 0, 0])
        emb_c = _norm([0, 0, 1, 0])

        # Cycle 1: upsert a, b; snapshot; mutate; restore
        await index.upsert("a", emb_a)
        await index.upsert("b", emb_b)
        snap1 = await index.snapshot()

        await index.upsert("c", emb_c)
        await index.restore(snap1)
        assert index.size == 2
        assert set(index._ids[:2]) == {"a", "b"}

        # Cycle 2: snapshot again; remove a; restore
        snap2 = await index.snapshot()
        await index.remove("a")
        assert index.size == 1
        await index.restore(snap2)
        assert index.size == 2

        # Cycle 3: add c; snapshot; rebuild; restore
        await index.upsert("c", emb_c)
        snap3 = await index.snapshot()
        assert index.size == 3

        await index.rebuild({"x": _random_emb(99)})
        assert index.size == 1

        await index.restore(snap3)
        assert index.size == 3
        assert "a" in index._id_to_label
        assert "b" in index._id_to_label
        assert "c" in index._id_to_label

    @pytest.mark.asyncio
    async def test_search_consistent_after_multiple_cycles(self, index):
        """Search results are correct after multiple snapshot/restore cycles."""
        emb = _norm([1, 0, 0, 0])
        await index.upsert("target", emb)

        for i in range(5):
            snap = await index.snapshot()
            await index.upsert(f"noise-{i}", _random_emb(i + 100))
            await index.restore(snap)

        results = index.search(emb, k=1, threshold=0.9)
        assert len(results) == 1
        assert results[0][0] == "target"
        assert results[0][1] > 0.99
        assert index.size == 1
