"""Tests for EmbeddingIndex cache round-trip and compatibility.

Covers:
- save_cache + load_cache preserves all data (ids, project_ids, matrix)
- Legacy cache (no project_ids key, no backend key) loads correctly
- Phase 1 cache (has project_ids but no backend key) loads correctly
- Cache loads via rebuild() (clean label mapping)
- Tombstoned entries excluded from saved cache (compaction)
"""

import pickle

import numpy as np
import pytest

from app.services.taxonomy.embedding_index import EmbeddingIndex

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


class TestCacheRoundTrip:
    @pytest.mark.asyncio
    async def test_save_load_preserves_ids(self, index, tmp_path):
        """Saved and loaded cache preserves cluster IDs."""
        await index.upsert("c1", _random_emb(1))
        await index.upsert("c2", _random_emb(2))
        await index.upsert("c3", _random_emb(3))

        cache_path = tmp_path / "index.pkl"
        await index.save_cache(cache_path)

        new_index = EmbeddingIndex(dim=DIM)
        loaded = await new_index.load_cache(cache_path, max_age_seconds=9999)
        assert loaded
        assert new_index.size == 3
        assert set(new_index._ids) == {"c1", "c2", "c3"}

    @pytest.mark.asyncio
    async def test_save_load_preserves_project_ids(self, index, tmp_path):
        """Saved and loaded cache preserves project IDs."""
        await index.upsert("c1", _random_emb(1), project_id="proj-A")
        await index.upsert("c2", _random_emb(2), project_id="proj-B")

        cache_path = tmp_path / "index.pkl"
        await index.save_cache(cache_path)

        new_index = EmbeddingIndex(dim=DIM)
        loaded = await new_index.load_cache(cache_path, max_age_seconds=9999)
        assert loaded
        # Project IDs should match the saved entries (order may differ
        # since rebuild reassigns labels, but the mapping must be consistent)
        pid_map = {
            new_index._ids[i]: new_index._project_ids[i]
            for i in range(new_index.size)
        }
        assert pid_map["c1"] == "proj-A"
        assert pid_map["c2"] == "proj-B"

    @pytest.mark.asyncio
    async def test_save_load_preserves_matrix(self, index, tmp_path):
        """Saved and loaded cache preserves embedding vectors."""
        emb1 = _norm([1, 0, 0, 0])
        emb2 = _norm([0, 1, 0, 0])
        await index.upsert("c1", emb1)
        await index.upsert("c2", emb2)

        cache_path = tmp_path / "index.pkl"
        await index.save_cache(cache_path)

        new_index = EmbeddingIndex(dim=DIM)
        loaded = await new_index.load_cache(cache_path, max_age_seconds=9999)
        assert loaded

        # Search should still find vectors with high similarity
        results = new_index.search(emb1, k=1, threshold=0.9)
        assert len(results) == 1
        assert results[0][0] == "c1"
        assert results[0][1] > 0.99

    @pytest.mark.asyncio
    async def test_load_cache_rebuilds_clean_label_mapping(self, index, tmp_path):
        """load_cache() calls rebuild() which creates clean sequential labels."""
        await index.upsert("a", _random_emb(10))
        await index.upsert("b", _random_emb(11))
        await index.upsert("c", _random_emb(12))

        cache_path = tmp_path / "index.pkl"
        await index.save_cache(cache_path)

        new_index = EmbeddingIndex(dim=DIM)
        await new_index.load_cache(cache_path, max_age_seconds=9999)

        # Labels should be sequential starting from 0
        labels = sorted(new_index._id_to_label.values())
        assert labels == [0, 1, 2]
        assert new_index._next_label == 3
        assert new_index._tombstones == set()


class TestLegacyCacheCompat:
    @pytest.mark.asyncio
    async def test_legacy_cache_no_project_ids(self, index, tmp_path):
        """Legacy cache without project_ids key loads correctly."""
        rng = np.random.RandomState(42)
        matrix = np.vstack([
            rng.randn(DIM).astype(np.float32),
            rng.randn(DIM).astype(np.float32),
        ])
        # Normalize rows
        for i in range(matrix.shape[0]):
            matrix[i] /= np.linalg.norm(matrix[i])

        legacy_data = {
            "matrix": matrix,
            "ids": ["old1", "old2"],
            # No "project_ids" key — legacy format
        }
        cache_path = tmp_path / "legacy.pkl"
        with open(cache_path, "wb") as f:
            pickle.dump(legacy_data, f)

        loaded = await index.load_cache(cache_path, max_age_seconds=9999)
        assert loaded
        assert index.size == 2
        assert set(index._ids) == {"old1", "old2"}
        # Project IDs should default to None
        assert index._project_ids == [None, None]

    @pytest.mark.asyncio
    async def test_phase1_cache_with_project_ids_no_backend(self, index, tmp_path):
        """Phase 1 cache (has project_ids but no backend key) loads correctly."""
        rng = np.random.RandomState(43)
        matrix = np.vstack([
            rng.randn(DIM).astype(np.float32),
            rng.randn(DIM).astype(np.float32),
        ])
        for i in range(matrix.shape[0]):
            matrix[i] /= np.linalg.norm(matrix[i])

        phase1_data = {
            "matrix": matrix,
            "ids": ["p1", "p2"],
            "project_ids": ["proj-X", "proj-Y"],
            # No "backend" key — Phase 1 format
        }
        cache_path = tmp_path / "phase1.pkl"
        with open(cache_path, "wb") as f:
            pickle.dump(phase1_data, f)

        loaded = await index.load_cache(cache_path, max_age_seconds=9999)
        assert loaded
        assert index.size == 2
        pid_map = {
            index._ids[i]: index._project_ids[i]
            for i in range(index.size)
        }
        assert pid_map["p1"] == "proj-X"
        assert pid_map["p2"] == "proj-Y"


class TestTombstoneCompaction:
    @pytest.mark.asyncio
    async def test_tombstoned_entries_excluded_from_cache(self, index, tmp_path):
        """save_cache() compacts — tombstoned entries are not saved."""
        await index.upsert("keep", _random_emb(1))
        await index.upsert("remove_me", _random_emb(2))
        await index.upsert("also_keep", _random_emb(3))
        await index.remove("remove_me")

        cache_path = tmp_path / "compacted.pkl"
        await index.save_cache(cache_path)

        # Read raw pickle to verify compaction
        with open(cache_path, "rb") as f:
            data = pickle.load(f)  # noqa: S301

        assert "remove_me" not in data["ids"]
        assert len(data["ids"]) == 2
        assert data["matrix"].shape[0] == 2

    @pytest.mark.asyncio
    async def test_compacted_cache_loads_correctly(self, index, tmp_path):
        """Cache saved after remove loads with correct entries."""
        emb_keep = _norm([1, 0, 0, 0])
        emb_remove = _norm([0, 1, 0, 0])
        emb_also = _norm([0, 0, 1, 0])
        await index.upsert("keep", emb_keep)
        await index.upsert("remove_me", emb_remove)
        await index.upsert("also_keep", emb_also)
        await index.remove("remove_me")

        cache_path = tmp_path / "compacted.pkl"
        await index.save_cache(cache_path)

        new_index = EmbeddingIndex(dim=DIM)
        loaded = await new_index.load_cache(cache_path, max_age_seconds=9999)
        assert loaded
        assert new_index.size == 2
        assert "remove_me" not in new_index._ids
        assert "keep" in new_index._ids
        assert "also_keep" in new_index._ids

        # Verify search still works
        results = new_index.search(emb_keep, k=1, threshold=0.9)
        assert len(results) == 1
        assert results[0][0] == "keep"


class TestCacheEdgeCases:
    @pytest.mark.asyncio
    async def test_stale_cache_not_loaded(self, index, tmp_path):
        """Cache older than max_age_seconds is not loaded."""
        import os
        import time

        await index.upsert("c1", _random_emb(1))
        cache_path = tmp_path / "stale.pkl"
        await index.save_cache(cache_path)

        # Backdate the file modification time
        old_time = time.time() - 7200  # 2 hours ago
        os.utime(cache_path, (old_time, old_time))

        new_index = EmbeddingIndex(dim=DIM)
        loaded = await new_index.load_cache(cache_path, max_age_seconds=3600)
        assert not loaded
        assert new_index.size == 0

    @pytest.mark.asyncio
    async def test_missing_cache_returns_false(self, index, tmp_path):
        """load_cache() with non-existent path returns False."""
        loaded = await index.load_cache(tmp_path / "does_not_exist.pkl")
        assert not loaded

    @pytest.mark.asyncio
    async def test_empty_index_save_load(self, index, tmp_path):
        """Saving and loading an empty index works."""
        cache_path = tmp_path / "empty.pkl"
        await index.save_cache(cache_path)

        new_index = EmbeddingIndex(dim=DIM)
        loaded = await new_index.load_cache(cache_path, max_age_seconds=9999)
        assert loaded
        assert new_index.size == 0
