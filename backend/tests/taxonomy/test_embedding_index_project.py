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
        assert index._project_ids == ["proj-B"]

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
    async def test_search_filter_none_entries_excluded(self, index):
        await index.upsert("c1", np.array([1, 0, 0, 0], dtype=np.float32), project_id=None)
        await index.upsert("c2", np.array([0.9, 0.1, 0, 0], dtype=np.float32), project_id="proj-A")
        query = np.array([1, 0, 0, 0], dtype=np.float32)
        results = index.search(query, k=5, threshold=0.0, project_filter="proj-A")
        assert len(results) == 1
        assert results[0][0] == "c2"


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
