"""Tests for TaxonomyEngine read API (get_tree, get_node, get_stats)."""

import numpy as np
import pytest

from app.models import TaxonomyNode
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

    for i, state in enumerate(["confirmed", "candidate", "retired"]):
        centroid = rng.randn(EMBEDDING_DIM).astype(np.float32)
        node = TaxonomyNode(
            label=f"node-{state}",
            centroid_embedding=centroid.tobytes(),
            member_count=5,
            state=state,
        )
        db.add(node)
    await db.commit()

    tree = await engine.get_tree(db)
    labels = [n["label"] for n in tree]
    assert "node-confirmed" in labels
    assert "node-candidate" in labels
    assert "node-retired" not in labels


@pytest.mark.asyncio
async def test_get_node_returns_detail(db, mock_embedding, mock_provider):
    """get_node returns a single node with its fields."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    rng = np.random.RandomState(42)

    centroid = rng.randn(EMBEDDING_DIM).astype(np.float32)
    node = TaxonomyNode(
        label="API Architecture",
        centroid_embedding=centroid.tobytes(),
        member_count=10,
        coherence=0.85,
        state="confirmed",
        color_hex="#a855f7",
    )
    db.add(node)
    await db.commit()

    detail = await engine.get_node(node.id, db)
    assert detail is not None
    assert detail["label"] == "API Architecture"
    assert detail["member_count"] == 10
    assert detail["state"] == "confirmed"


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
    assert stats["confirmed_nodes"] == 0
    assert stats["candidate_nodes"] == 0
    assert stats["total_families"] == 0


@pytest.mark.asyncio
async def test_get_stats_counts(db, mock_embedding, mock_provider):
    """get_stats returns correct counts of nodes and families."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    rng = np.random.RandomState(42)

    for state in ["confirmed", "confirmed", "candidate"]:
        centroid = rng.randn(EMBEDDING_DIM).astype(np.float32)
        node = TaxonomyNode(
            label=f"node-{state}",
            centroid_embedding=centroid.tobytes(),
            member_count=5,
            state=state,
        )
        db.add(node)
    await db.commit()

    stats = await engine.get_stats(db)
    assert stats["confirmed_nodes"] == 2
    assert stats["candidate_nodes"] == 1
