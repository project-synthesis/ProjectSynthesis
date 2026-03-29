"""Tests for TaxonomyEngine read API (get_tree, get_node, get_stats)."""

import numpy as np
import pytest

from app.models import PromptCluster
from app.services.taxonomy.engine import TaxonomyEngine
from tests.taxonomy.conftest import EMBEDDING_DIM


@pytest.mark.asyncio
async def test_get_tree_empty(db, mock_embedding, mock_provider):
    """get_tree on empty DB returns empty list."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    tree = await engine.get_tree(db)
    assert tree == []


@pytest.mark.asyncio
async def test_get_tree_returns_confirmed_and_candidate(db, mock_embedding, mock_provider):
    """get_tree returns confirmed and candidate nodes, not retired."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    rng = np.random.RandomState(42)

    for i, state in enumerate(["active", "candidate", "archived"]):
        centroid = rng.randn(EMBEDDING_DIM).astype(np.float32)
        node = PromptCluster(
            label=f"node-{state}",
            centroid_embedding=centroid.tobytes(),
            member_count=5,
            state=state,
        )
        db.add(node)
    await db.commit()

    tree = await engine.get_tree(db)
    labels = [n["label"] for n in tree]
    assert "node-active" in labels
    assert "node-candidate" in labels
    assert "node-archived" in labels  # archived included for navigator filter tab


@pytest.mark.asyncio
async def test_get_node_returns_detail(db, mock_embedding, mock_provider):
    """get_node returns a single node with its fields."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    rng = np.random.RandomState(42)

    centroid = rng.randn(EMBEDDING_DIM).astype(np.float32)
    node = PromptCluster(
        label="API Architecture",
        centroid_embedding=centroid.tobytes(),
        member_count=10,
        coherence=0.85,
        state="active",
        color_hex="#a855f7",
    )
    db.add(node)
    await db.commit()

    detail = await engine.get_node(node.id, db)
    assert detail is not None
    assert detail["label"] == "API Architecture"
    assert detail["member_count"] == 10
    assert detail["state"] == "active"


@pytest.mark.asyncio
async def test_get_node_not_found(db, mock_embedding, mock_provider):
    """get_node returns None for a nonexistent node."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    detail = await engine.get_node("nonexistent-id", db)
    assert detail is None


@pytest.mark.asyncio
async def test_get_stats_empty(db, mock_embedding, mock_provider):
    """get_stats on empty DB returns zero counts."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    stats = await engine.get_stats(db)
    assert stats["nodes"]["active"] == 0
    assert stats["nodes"]["candidate"] == 0
    assert stats["total_families"] == 0
    assert stats["q_system"] is None
    # q_sparkline must be a flat list of floats, not a SparklineData object
    assert isinstance(stats["q_sparkline"], list)
    assert all(isinstance(v, (int, float)) for v in stats["q_sparkline"])
    # SparklineData enrichment fields
    assert stats["q_trend"] == 0.0
    assert stats["q_current"] is None
    assert stats["q_min"] is None
    assert stats["q_max"] is None
    assert stats["q_point_count"] == 0


@pytest.mark.asyncio
async def test_get_stats_counts(db, mock_embedding, mock_provider):
    """get_stats returns correct counts of nodes and families."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    rng = np.random.RandomState(42)

    for state in ["active", "active", "candidate"]:
        centroid = rng.randn(EMBEDDING_DIM).astype(np.float32)
        node = PromptCluster(
            label=f"node-{state}",
            centroid_embedding=centroid.tobytes(),
            member_count=5,
            state=state,
        )
        db.add(node)
    await db.commit()

    stats = await engine.get_stats(db)
    assert stats["nodes"]["active"] == 2
    assert stats["nodes"]["candidate"] == 1
    assert stats["total_families"] == 0  # no families created in this test


@pytest.mark.asyncio
async def test_get_stats_sparkline_with_snapshots(db, mock_embedding, mock_provider):
    """get_stats returns sparkline data when snapshots exist."""
    from app.models import TaxonomySnapshot

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create 5 snapshots with ascending q_system values
    for i in range(5):
        snap = TaxonomySnapshot(
            trigger="warm_path",
            q_system=0.5 + i * 0.1,  # 0.5, 0.6, 0.7, 0.8, 0.9
            q_coherence=0.6 + i * 0.05,
            q_separation=0.5 + i * 0.05,
            q_coverage=0.8,
            q_dbcv=0.0,
        )
        db.add(snap)
    await db.commit()

    stats = await engine.get_stats(db)

    # Sparkline should have data
    assert len(stats["q_sparkline"]) == 5
    assert all(isinstance(v, float) for v in stats["q_sparkline"])

    # Trend should be positive (ascending values)
    assert stats["q_trend"] > 0

    # Current should be the last value (0.9)
    assert stats["q_current"] == pytest.approx(0.9, abs=0.01)

    # Min/max
    assert stats["q_min"] == pytest.approx(0.5, abs=0.01)
    assert stats["q_max"] == pytest.approx(0.9, abs=0.01)

    # Point count
    assert stats["q_point_count"] == 5


@pytest.mark.asyncio
async def test_get_stats_cache_hit(db, mock_embedding, mock_provider):
    """get_stats returns cached result on second call within TTL."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    result1 = await engine.get_stats(db)
    result2 = await engine.get_stats(db)

    # Same object returned (cache hit)
    assert result1 is result2


@pytest.mark.asyncio
async def test_get_stats_cache_invalidation(db, mock_embedding, mock_provider):
    """_invalidate_stats_cache clears the cache."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    result1 = await engine.get_stats(db)
    engine._invalidate_stats_cache()
    result2 = await engine.get_stats(db)

    # Different objects (cache was cleared)
    assert result1 is not result2
