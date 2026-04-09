"""Tests for per-project dirty-set tracking (Phase 3A)."""

from unittest.mock import MagicMock

import pytest

from app.services.taxonomy.engine import TaxonomyEngine


@pytest.fixture
def engine():
    mock_embedding = MagicMock()
    mock_provider = MagicMock()
    return TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)


class TestDirtySetDict:
    def test_mark_dirty_with_project(self, engine):
        engine.mark_dirty("c1", project_id="proj-A")
        assert "c1" in engine._dirty_set
        assert engine._dirty_set["c1"] == "proj-A"

    def test_mark_dirty_without_project_defaults_none(self, engine):
        engine.mark_dirty("c1")
        assert engine._dirty_set["c1"] is None

    def test_snapshot_with_projects(self, engine):
        engine.mark_dirty("c1", project_id="proj-A")
        engine.mark_dirty("c2", project_id="proj-B")
        engine.mark_dirty("c3", project_id="proj-A")

        all_ids, by_project = engine.snapshot_dirty_set_with_projects()
        assert all_ids == {"c1", "c2", "c3"}
        assert by_project["proj-A"] == {"c1", "c3"}
        assert by_project["proj-B"] == {"c2"}
        assert len(engine._dirty_set) == 0  # cleared

    def test_snapshot_none_project_grouped_as_legacy(self, engine):
        engine.mark_dirty("c1")  # no project
        _, by_project = engine.snapshot_dirty_set_with_projects()
        assert "legacy" in by_project
        assert "c1" in by_project["legacy"]

    def test_backward_compat_snapshot(self, engine):
        """Old snapshot_dirty_set() still returns set[str]."""
        engine.mark_dirty("c1", project_id="proj-A")
        engine.mark_dirty("c2", project_id="proj-B")
        result = engine.snapshot_dirty_set()
        assert isinstance(result, set)
        assert result == {"c1", "c2"}

    def test_empty_snapshot_with_projects(self, engine):
        all_ids, by_project = engine.snapshot_dirty_set_with_projects()
        assert all_ids == set()
        assert by_project == {}
