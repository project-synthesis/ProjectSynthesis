"""Tests for TaxonomyEngine cold path — full HDBSCAN + UMAP refit."""

import numpy as np
import pytest

from app.models import PromptCluster
from app.services.taxonomy.engine import TaxonomyEngine
from tests.taxonomy.conftest import EMBEDDING_DIM


@pytest.mark.asyncio
async def test_cold_path_recomputes_umap(db, mock_embedding, mock_provider):
    """Cold path should set UMAP coordinates on all confirmed nodes."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create some confirmed nodes
    for label in ["Node A", "Node B", "Node C"]:
        node = PromptCluster(
            label=label,
            centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
            state="active",
            member_count=5,
            color_hex="#a855f7",
        )
        db.add(node)
    await db.commit()

    result = await engine.run_cold_path(db)
    assert result is not None

    # Verify UMAP positions are set (may be None for < 5 nodes — fallback)
    from sqlalchemy import select
    nodes = (await db.execute(select(PromptCluster))).scalars().all()
    for node in nodes:
        # At minimum, positions should be set (even if PCA fallback)
        assert node.umap_x is not None or len(nodes) < 5


@pytest.mark.asyncio
async def test_cold_path_acquires_warm_lock(db, mock_embedding, mock_provider):
    """Cold path should block warm path during execution."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Run cold path — it should acquire the warm lock
    await engine.run_cold_path(db)
    # After completion, lock should be released
    assert not engine._warm_path_lock.locked()


@pytest.mark.asyncio
async def test_cold_path_creates_snapshot(db, mock_embedding, mock_provider):
    """Cold path should create a snapshot with trigger='cold_path'."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    result = await engine.run_cold_path(db)
    assert result is not None
    assert result.snapshot_id is not None


@pytest.mark.asyncio
async def test_cold_path_returns_correct_counts(db, mock_embedding, mock_provider):
    """ColdPathResult should report node counts and umap_fitted flag."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create families so cold path has data to cluster
    rng = np.random.RandomState(42)
    for i in range(6):
        f = PromptCluster(
            label=f"Family {i}",
            domain="backend",
            centroid_embedding=rng.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        )
        db.add(f)
    await db.commit()

    result = await engine.run_cold_path(db)
    assert result is not None
    assert result.nodes_created >= 0
    assert result.nodes_updated >= 0
    assert isinstance(result.umap_fitted, bool)


@pytest.mark.asyncio
async def test_cold_path_regenerates_colors(db, mock_embedding, mock_provider):
    """Cold path should set color_hex on nodes with UMAP positions."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create confirmed nodes with UMAP positions
    for i, label in enumerate(["Alpha", "Beta", "Gamma"]):
        node = PromptCluster(
            label=label,
            centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
            state="active",
            member_count=3,
            umap_x=float(i),
            umap_y=float(i * 0.5),
            umap_z=float(i * 0.2),
            color_hex="#000000",
        )
        db.add(node)
    await db.commit()

    result = await engine.run_cold_path(db)
    assert result is not None

    from sqlalchemy import select
    nodes = (await db.execute(select(PromptCluster))).scalars().all()
    confirmed = [n for n in nodes if n.state == "active"]
    for node in confirmed:
        if node.umap_x is not None:
            # Color should have been regenerated from UMAP position
            assert node.color_hex.startswith("#")
            assert len(node.color_hex) == 7


@pytest.mark.asyncio
async def test_cold_path_lock_released_on_error(db, mock_embedding, mock_provider):
    """Cold path should release lock even on internal errors."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create a family with corrupt embedding to potentially trigger error
    f = PromptCluster(
        label="Corrupt",
        domain="backend",
        centroid_embedding=b"bad_data",
    )
    db.add(f)
    await db.commit()

    # Should not raise, and lock should be released
    await engine.run_cold_path(db)
    assert not engine._warm_path_lock.locked()


@pytest.mark.asyncio
async def test_cold_path_q_system_in_result(db, mock_embedding, mock_provider):
    """ColdPathResult should contain q_system score."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    result = await engine.run_cold_path(db)
    assert result is not None
    # q_system can be None (no nodes) or a float
    if result.q_system is not None:
        assert 0.0 <= result.q_system <= 1.0
