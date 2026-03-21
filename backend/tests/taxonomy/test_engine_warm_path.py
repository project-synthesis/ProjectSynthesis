"""Tests for TaxonomyEngine warm path — periodic re-clustering with lifecycle."""


import numpy as np
import pytest

from app.models import PromptCluster
from app.services.taxonomy.engine import TaxonomyEngine
from tests.taxonomy.conftest import EMBEDDING_DIM, make_cluster_distribution


@pytest.mark.asyncio
async def test_warm_path_creates_snapshot(db, mock_embedding, mock_provider):
    """Warm path should always create a snapshot."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    result = await engine.run_warm_path(db)
    assert result is not None
    assert result.snapshot_id is not None


@pytest.mark.asyncio
async def test_warm_path_lock_deduplication(db, mock_embedding, mock_provider):
    """Concurrent warm-path invocations should be deduplicated."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Acquire lock to simulate running warm path
    async with engine._warm_path_lock:
        assert engine._warm_path_lock.locked()
        # Second invocation should skip
        result = await engine.run_warm_path(db)
        assert result is None  # skipped due to lock


@pytest.mark.asyncio
async def test_warm_path_q_system_non_regressive(db, mock_embedding, mock_provider):
    """Q_system should not decrease across warm-path cycles (within epsilon)."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create some families and nodes to give the warm path something to work with
    rng = np.random.RandomState(42)
    for text in ["REST API", "SQL queries", "React components"]:
        cluster = make_cluster_distribution(text, 5, spread=0.05, rng=rng)
        for i, emb in enumerate(cluster):
            f = PromptCluster(
                label=f"{text}-{i}",
                domain="general",
                centroid_embedding=emb.astype(np.float32).tobytes(),
            )
            db.add(f)
    await db.commit()

    # Run multiple warm paths
    q_values = []
    for _ in range(3):
        result = await engine.run_warm_path(db)
        if result and result.q_system is not None:
            q_values.append(result.q_system)

    # Q_system should be non-decreasing (within epsilon tolerance)
    for i in range(1, len(q_values)):
        assert q_values[i] >= q_values[i - 1] - 0.02  # epsilon tolerance


@pytest.mark.asyncio
async def test_warm_path_returns_operation_counts(db, mock_embedding, mock_provider):
    """WarmPathResult should report operations_attempted and operations_accepted."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    result = await engine.run_warm_path(db)
    assert result is not None
    assert result.operations_attempted >= 0
    assert result.operations_accepted >= 0
    assert result.operations_accepted <= result.operations_attempted


@pytest.mark.asyncio
async def test_warm_path_deadlock_breaker_field(db, mock_embedding, mock_provider):
    """WarmPathResult should include deadlock_breaker_used field."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    result = await engine.run_warm_path(db)
    assert result is not None
    assert isinstance(result.deadlock_breaker_used, bool)


@pytest.mark.asyncio
async def test_warm_path_lock_released_after_completion(db, mock_embedding, mock_provider):
    """Warm path should release lock after completing."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    await engine.run_warm_path(db)
    # Lock should be released after completion
    assert not engine._warm_path_lock.locked()


@pytest.mark.asyncio
async def test_warm_path_deadlock_breaker_triggers_at_cycle_5(
    db, mock_embedding, mock_provider
):
    """Deadlock breaker should activate after 5 consecutive rejected cycles.

    We set the counter to 4 and run one cycle where ops are attempted but
    ALL are rejected (ops_accepted == 0), pushing the counter to 5.
    """
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    engine._consecutive_rejected_cycles = 4

    # Create exactly ONE confirmed node with member_count=0 so retire is
    # attempted.  After retire succeeds, Q drops from ~0.7 to 0.0 (no
    # confirmed nodes left), which fails the non-regression check.  The
    # rollback makes ops_accepted=0, pushing the counter from 4 to 5.
    node = PromptCluster(
        label="Idle node",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        state="active",
        member_count=0,
        coherence=0.9,
        color_hex="#a855f7",
    )
    db.add(node)
    await db.commit()

    result = await engine.run_warm_path(db)
    assert result is not None
    # The counter should have hit 5, triggering the breaker
    assert result.deadlock_breaker_used is True
    # Counter should be reset after breaker triggers
    assert engine._consecutive_rejected_cycles == 0
    # _cold_path_needed flag should be set
    assert engine._cold_path_needed is True


@pytest.mark.asyncio
async def test_warm_path_lock_released_on_error(db, mock_embedding, mock_provider):
    """Warm path should release lock even if an error occurs mid-execution."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create a node with corrupt centroid to trigger error during Q computation
    node = PromptCluster(
        label="Corrupt",
        centroid_embedding=b"not_valid_floats",
        state="active",
        member_count=5,
        color_hex="#a855f7",
    )
    db.add(node)
    await db.commit()

    # Should not raise, and lock should be released
    await engine.run_warm_path(db)
    assert not engine._warm_path_lock.locked()
