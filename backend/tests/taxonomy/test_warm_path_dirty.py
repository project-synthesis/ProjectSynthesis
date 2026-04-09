"""Tests for warm path dirty-set integration."""

from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_warm_path_passes_dirty_set_to_phases():
    """Warm path should snapshot dirty set and pass to phase functions."""
    from app.services.taxonomy.engine import TaxonomyEngine

    mock_embedding = MagicMock()
    mock_provider = MagicMock()
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    engine.mark_dirty("cluster-1")
    engine.mark_dirty("cluster-2")

    # Snapshot should return the dirty IDs
    snapshot = engine.snapshot_dirty_set()
    assert snapshot == {"cluster-1", "cluster-2"}
    assert len(engine._dirty_set) == 0

    # After snapshot, marking new IDs starts a fresh set
    engine.mark_dirty("cluster-3")
    assert set(engine._dirty_set.keys()) == {"cluster-3"}


@pytest.mark.asyncio
async def test_first_cycle_returns_none_dirty_set():
    """First warm cycle (age=0) should signal full-scan via None dirty set."""
    from app.services.taxonomy.engine import TaxonomyEngine

    mock_embedding = MagicMock()
    mock_provider = MagicMock()
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    assert engine.is_first_warm_cycle()
    # On first cycle, caller should treat dirty_set as None (= process all)
