"""Tests for EmbeddingIndex stable label mapping and tombstones.

Covers:
- upsert assigns sequential labels via _id_to_label
- remove tombstones the label instead of shifting indices
- search excludes tombstoned entries
- rebuild compacts labels and clears tombstones
- size reflects active (non-tombstoned) entries
"""

import numpy as np
import pytest

from app.services.taxonomy.embedding_index import EmbeddingIndex


@pytest.fixture
def index():
    return EmbeddingIndex(dim=4)


def _emb(v):
    """Create a normalized 4-dim embedding from a list."""
    v = np.array(v, dtype=np.float32)
    return v / np.linalg.norm(v)


class TestLabelAssignment:
    @pytest.mark.asyncio
    async def test_upsert_assigns_sequential_labels(self, index):
        await index.upsert("a", _emb([1, 0, 0, 0]))
        await index.upsert("b", _emb([0, 1, 0, 0]))
        await index.upsert("c", _emb([0, 0, 1, 0]))

        assert index._id_to_label == {"a": 0, "b": 1, "c": 2}
        assert index._next_label == 3

    @pytest.mark.asyncio
    async def test_upsert_update_keeps_same_label(self, index):
        await index.upsert("a", _emb([1, 0, 0, 0]))
        label_before = index._id_to_label["a"]
        await index.upsert("a", _emb([0, 1, 0, 0]))  # update
        assert index._id_to_label["a"] == label_before
        assert index.size == 1

    @pytest.mark.asyncio
    async def test_ids_list_tracks_labels(self, index):
        await index.upsert("x", _emb([1, 0, 0, 0]))
        await index.upsert("y", _emb([0, 1, 0, 0]))
        assert index._ids[0] == "x"
        assert index._ids[1] == "y"


class TestTombstones:
    @pytest.mark.asyncio
    async def test_remove_tombstones_label(self, index):
        await index.upsert("a", _emb([1, 0, 0, 0]))
        await index.upsert("b", _emb([0, 1, 0, 0]))
        await index.remove("a")

        assert 0 in index._tombstones
        assert "a" not in index._id_to_label
        assert index._ids[0] is None  # tombstoned slot
        assert index._ids[1] == "b"

    @pytest.mark.asyncio
    async def test_remove_does_not_shift_labels(self, index):
        await index.upsert("a", _emb([1, 0, 0, 0]))
        await index.upsert("b", _emb([0, 1, 0, 0]))
        await index.upsert("c", _emb([0, 0, 1, 0]))
        await index.remove("a")

        # "b" and "c" keep their original labels
        assert index._id_to_label["b"] == 1
        assert index._id_to_label["c"] == 2

    @pytest.mark.asyncio
    async def test_size_excludes_tombstoned(self, index):
        await index.upsert("a", _emb([1, 0, 0, 0]))
        await index.upsert("b", _emb([0, 1, 0, 0]))
        assert index.size == 2
        await index.remove("a")
        assert index.size == 1

    @pytest.mark.asyncio
    async def test_upsert_after_remove_gets_new_label(self, index):
        await index.upsert("a", _emb([1, 0, 0, 0]))
        await index.remove("a")
        await index.upsert("a", _emb([0, 1, 0, 0]))

        # Re-inserted "a" should get a new label (not reuse tombstoned one)
        assert index._id_to_label["a"] == 1  # next_label was 1 after first upsert
        assert 0 not in index._tombstones or index._ids[0] is None
        assert index.size == 1


class TestSearchExcludesTombstoned:
    @pytest.mark.asyncio
    async def test_search_skips_tombstoned(self, index):
        emb_a = _emb([1, 0, 0, 0])
        emb_b = _emb([0.9, 0.1, 0, 0])
        await index.upsert("a", emb_a)
        await index.upsert("b", emb_b)
        await index.remove("a")

        results = index.search(emb_a, k=5, threshold=0.0)
        result_ids = [cid for cid, _ in results]
        assert "a" not in result_ids
        assert "b" in result_ids

    @pytest.mark.asyncio
    async def test_search_after_remove_all_returns_empty(self, index):
        await index.upsert("a", _emb([1, 0, 0, 0]))
        await index.remove("a")
        results = index.search(_emb([1, 0, 0, 0]), k=5, threshold=0.0)
        assert results == []


class TestRebuildCompacts:
    @pytest.mark.asyncio
    async def test_rebuild_clears_tombstones(self, index):
        await index.upsert("a", _emb([1, 0, 0, 0]))
        await index.upsert("b", _emb([0, 1, 0, 0]))
        await index.remove("a")
        assert len(index._tombstones) == 1

        await index.rebuild({"x": _emb([1, 0, 0, 0]), "y": _emb([0, 1, 0, 0])})
        assert len(index._tombstones) == 0
        assert index._next_label == 2
        assert index._id_to_label == {"x": 0, "y": 1}

    @pytest.mark.asyncio
    async def test_rebuild_empty_resets_all(self, index):
        await index.upsert("a", _emb([1, 0, 0, 0]))
        await index.rebuild({})
        assert index.size == 0
        assert index._next_label == 0
        assert index._id_to_label == {}
        assert index._tombstones == set()

    @pytest.mark.asyncio
    async def test_rebuild_resets_ids(self, index):
        await index.upsert("old", _emb([1, 0, 0, 0]))
        await index.rebuild({"new": _emb([0, 1, 0, 0])})
        assert index._ids == ["new"]
        assert "old" not in index._id_to_label


class TestSnapshotLabelMapping:
    @pytest.mark.asyncio
    async def test_snapshot_captures_label_mapping(self, index):
        await index.upsert("a", _emb([1, 0, 0, 0]))
        await index.upsert("b", _emb([0, 1, 0, 0]))
        snap = await index.snapshot()
        assert snap.id_to_label == {"a": 0, "b": 1}
        assert snap.next_label == 2
        assert snap.tombstones == set()

    @pytest.mark.asyncio
    async def test_snapshot_captures_tombstones(self, index):
        await index.upsert("a", _emb([1, 0, 0, 0]))
        await index.upsert("b", _emb([0, 1, 0, 0]))
        await index.remove("a")
        snap = await index.snapshot()
        assert 0 in snap.tombstones
        assert "a" not in snap.id_to_label

    @pytest.mark.asyncio
    async def test_restore_restores_label_mapping(self, index):
        await index.upsert("a", _emb([1, 0, 0, 0]))
        await index.upsert("b", _emb([0, 1, 0, 0]))
        snap = await index.snapshot()

        # Mutate
        await index.remove("a")
        await index.upsert("c", _emb([0, 0, 1, 0]))

        # Restore
        await index.restore(snap)
        assert index._id_to_label == {"a": 0, "b": 1}
        assert index._next_label == 2
        assert index._tombstones == set()
        assert index.size == 2
