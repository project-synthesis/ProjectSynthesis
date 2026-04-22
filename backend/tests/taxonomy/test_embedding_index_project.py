"""Tests for EmbeddingIndex project_id filtering (ADR-005)."""

import numpy as np
import pytest

from app.services.taxonomy.embedding_index import EmbeddingIndex


@pytest.fixture
def index():
    return EmbeddingIndex(dim=4)


def _random_emb(dim=4):
    v = np.random.randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


class TestProjectIdTracking:
    @pytest.mark.asyncio
    async def test_upsert_with_project_id(self, index):
        await index.upsert("c1", _random_emb(), project_id="proj-A")
        assert index.size == 1
        assert index._project_ids == ["proj-A"]

    @pytest.mark.asyncio
    async def test_upsert_without_project_id_defaults_to_none(self, index):
        await index.upsert("c1", _random_emb())
        assert index._project_ids == [None]

    @pytest.mark.asyncio
    async def test_upsert_update_preserves_project_id(self, index):
        await index.upsert("c1", _random_emb(), project_id="proj-A")
        await index.upsert("c1", _random_emb(), project_id="proj-A")
        assert index.size == 1
        assert index._project_ids == ["proj-A"]

    @pytest.mark.asyncio
    async def test_remove_removes_project_id(self, index):
        await index.upsert("c1", _random_emb(), project_id="proj-A")
        await index.upsert("c2", _random_emb(), project_id="proj-B")
        await index.remove("c1")
        assert index.size == 1
        # Tombstoned entry is None, active entry preserved
        assert index._project_ids[1] == "proj-B"

    @pytest.mark.asyncio
    async def test_rebuild_with_project_ids(self, index):
        centroids = {"c1": _random_emb(), "c2": _random_emb()}
        project_ids = {"c1": "proj-A", "c2": "proj-B"}
        await index.rebuild(centroids, project_ids=project_ids)
        assert index._project_ids == ["proj-A", "proj-B"]

    @pytest.mark.asyncio
    async def test_rebuild_without_project_ids_defaults_to_none(self, index):
        centroids = {"c1": _random_emb(), "c2": _random_emb()}
        await index.rebuild(centroids)
        assert index._project_ids == [None, None]


class TestProjectFilteredSearch:
    @pytest.mark.asyncio
    async def test_search_without_filter_returns_all(self, index):
        emb_a = np.array([1, 0, 0, 0], dtype=np.float32)
        emb_b = np.array([0, 1, 0, 0], dtype=np.float32)
        query = np.array([0.9, 0.1, 0, 0], dtype=np.float32)
        await index.upsert("c1", emb_a, project_id="proj-A")
        await index.upsert("c2", emb_b, project_id="proj-B")
        results = index.search(query, k=5, threshold=0.0)
        assert len(results) == 2
        assert results[0][0] == "c1"

    @pytest.mark.asyncio
    async def test_search_with_project_filter(self, index):
        emb_a = np.array([1, 0, 0, 0], dtype=np.float32)
        emb_b = np.array([0.95, 0.05, 0, 0], dtype=np.float32)
        query = np.array([0.9, 0.1, 0, 0], dtype=np.float32)
        await index.upsert("c1", emb_a, project_id="proj-A")
        await index.upsert("c2", emb_b, project_id="proj-B")
        results = index.search(query, k=5, threshold=0.0, project_filter="proj-B")
        assert len(results) == 1
        assert results[0][0] == "c2"

    @pytest.mark.asyncio
    async def test_search_with_filter_no_matches(self, index):
        await index.upsert("c1", _random_emb(), project_id="proj-A")
        results = index.search(_random_emb(), k=5, threshold=0.0, project_filter="proj-X")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_filter_includes_unscoped_null_clusters(self, index):
        # A10: clusters with dominant_project_id=None (not yet reconciled by warm
        # Phase 0) should be visible when project_filter is set.  They are
        # unreconciled, not cross-project — failing closed here silently drops
        # brand-new cluster patterns from injection until warm Phase 0 runs.
        emb_null = np.array([1, 0, 0, 0], dtype=np.float32)   # unscoped (new cluster)
        emb_proj = np.array([0.9, 0.1, 0, 0], dtype=np.float32)  # tagged proj-A
        emb_other = np.array([0, 1, 0, 0], dtype=np.float32)  # tagged proj-B
        query = np.array([1, 0, 0, 0], dtype=np.float32)
        await index.upsert("unscoped", emb_null, project_id=None)
        await index.upsert("in_proj", emb_proj, project_id="proj-A")
        await index.upsert("other_proj", emb_other, project_id="proj-B")
        results = index.search(query, k=5, threshold=0.0, project_filter="proj-A")
        ids = [r[0] for r in results]
        assert "unscoped" in ids, "Unscoped cluster must be visible under project filter"
        assert "in_proj" in ids, "Same-project cluster must be visible"
        assert "other_proj" not in ids, "Other-project cluster must be excluded"
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_search_filter_all_null_returns_all(self, index):
        # All unscoped — project_filter should still return them all
        emb_a = np.array([1, 0, 0, 0], dtype=np.float32)
        emb_b = np.array([0.9, 0.1, 0, 0], dtype=np.float32)
        query = np.array([1, 0, 0, 0], dtype=np.float32)
        await index.upsert("c1", emb_a, project_id=None)
        await index.upsert("c2", emb_b, project_id=None)
        results = index.search(query, k=5, threshold=0.0, project_filter="proj-A")
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_search_filter_null_never_matches_different_project(self, index):
        # Unscoped clusters must NOT bleed across distinct project filters
        await index.upsert("other", np.array([1, 0, 0, 0], dtype=np.float32), project_id="proj-B")
        query = np.array([1, 0, 0, 0], dtype=np.float32)
        # proj-B tagged cluster should NOT appear when filtering for proj-A
        results = index.search(query, k=5, threshold=0.0, project_filter="proj-A")
        assert results == []


class TestCacheWithProjectIds:
    @pytest.mark.asyncio
    async def test_save_and_load_preserves_project_ids(self, index, tmp_path):
        await index.upsert("c1", _random_emb(), project_id="proj-A")
        await index.upsert("c2", _random_emb(), project_id="proj-B")
        cache_path = tmp_path / "test_index.pkl"
        await index.save_cache(cache_path)
        new_index = EmbeddingIndex(dim=4)
        loaded = await new_index.load_cache(cache_path)
        assert loaded
        assert new_index.size == 2
        assert new_index._project_ids == ["proj-A", "proj-B"]

    @pytest.mark.asyncio
    async def test_load_legacy_cache_without_project_ids(self, index, tmp_path):
        import pickle
        legacy_data = {
            "matrix": np.random.randn(2, 4).astype(np.float32),
            "ids": ["c1", "c2"],
        }
        cache_path = tmp_path / "legacy.pkl"
        with open(cache_path, "wb") as f:
            pickle.dump(legacy_data, f)
        loaded = await index.load_cache(cache_path, max_age_seconds=9999)
        assert loaded
        assert index.size == 2
        assert index._project_ids == [None, None]


class TestSnapshotRestore:
    @pytest.mark.asyncio
    async def test_snapshot_includes_project_ids(self, index):
        await index.upsert("c1", _random_emb(), project_id="proj-A")
        snap = await index.snapshot()
        assert hasattr(snap, "project_ids")
        assert snap.project_ids == ["proj-A"]

    @pytest.mark.asyncio
    async def test_restore_restores_project_ids(self, index):
        await index.upsert("c1", _random_emb(), project_id="proj-A")
        snap = await index.snapshot()
        await index.upsert("c2", _random_emb(), project_id="proj-B")
        assert index.size == 2
        await index.restore(snap)
        assert index.size == 1
        assert index._project_ids == ["proj-A"]
