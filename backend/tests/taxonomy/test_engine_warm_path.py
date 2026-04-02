"""Tests for TaxonomyEngine warm path — periodic re-clustering with lifecycle."""


import numpy as np
import pytest

from app.models import Optimization, PromptCluster
from app.services.taxonomy.engine import TaxonomyEngine
from app.services.taxonomy.warm_path import WarmPathResult
from tests.taxonomy.conftest import EMBEDDING_DIM, make_cluster_distribution


@pytest.mark.asyncio
async def test_warm_path_creates_snapshot(session_factory, mock_embedding, mock_provider):
    """Warm path should always create a snapshot."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    result = await engine.run_warm_path(session_factory)
    assert result is not None
    assert result.snapshot_id is not None


@pytest.mark.asyncio
async def test_warm_path_lock_deduplication(session_factory, mock_embedding, mock_provider):
    """Concurrent warm-path invocations should be deduplicated."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Acquire lock to simulate running warm path
    async with engine._warm_path_lock:
        assert engine._warm_path_lock.locked()
        # Second invocation should skip
        result = await engine.run_warm_path(session_factory)
        assert result is None  # skipped due to lock


@pytest.mark.asyncio
async def test_warm_path_q_system_non_regressive(session_factory, mock_embedding, mock_provider):
    """Q_system should not decrease across warm-path cycles (within epsilon)."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create some families and nodes to give the warm path something to work with
    from contextlib import asynccontextmanager
    rng = np.random.RandomState(42)

    async with session_factory() as db:
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
        result = await engine.run_warm_path(session_factory)
        if result and result.q_system is not None:
            q_values.append(result.q_system)

    # Q_system should be non-decreasing (within epsilon tolerance).
    # Exception: Q=0.0 is valid when the active set is too small
    # (< 3 nodes with separation data), so skip that comparison.
    for i in range(1, len(q_values)):
        if q_values[i] == 0.0 or q_values[i - 1] == 0.0:
            continue  # Q=0 means insufficient data, not regression
        assert q_values[i] >= q_values[i - 1] - 0.02  # epsilon tolerance


@pytest.mark.asyncio
async def test_warm_path_returns_operation_counts(session_factory, mock_embedding, mock_provider):
    """WarmPathResult should report operations_attempted and operations_accepted."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    result = await engine.run_warm_path(session_factory)
    assert result is not None
    assert result.operations_attempted >= 0
    assert result.operations_accepted >= 0
    assert result.operations_accepted <= result.operations_attempted


@pytest.mark.asyncio
async def test_warm_path_deadlock_breaker_field(session_factory, mock_embedding, mock_provider):
    """WarmPathResult should include deadlock_breaker_used field."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    result = await engine.run_warm_path(session_factory)
    assert result is not None
    assert isinstance(result.deadlock_breaker_used, bool)


@pytest.mark.asyncio
async def test_warm_path_lock_released_after_completion(session_factory, mock_embedding, mock_provider):
    """Warm path should release lock after completing."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    await engine.run_warm_path(session_factory)
    # Lock should be released after completion
    assert not engine._warm_path_lock.locked()


@pytest.mark.asyncio
async def test_warm_path_deadlock_breaker_triggers_at_cycle_5(
    mock_embedding, mock_provider
):
    """Per-phase deadlock breaker should activate after 5 consecutive rejections.

    The ``_update_phase_rejection_counters`` helper in warm_path.py increments
    per-phase counters whenever a phase's Q gate rejects the phase (accepted=False).
    When any counter reaches DEADLOCK_BREAKER_THRESHOLD (5), it sets
    engine._cold_path_needed and the WarmPathResult carries deadlock_breaker_used=True.

    This test exercises the logic directly by calling the helper function with
    mocked PhaseResult objects.
    """
    from app.services.taxonomy.warm_path import _update_phase_rejection_counters
    from app.services.taxonomy.warm_phases import PhaseResult

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Simulate 4 prior consecutive rejections for the retire phase
    engine._phase_rejection_counters["retire"] = 4

    # Build a PhaseResult that represents a rejected retire phase
    rejected_retire = PhaseResult(
        phase="retire",
        q_before=0.7,
        q_after=0.3,  # regressed — gate rejected
        accepted=False,
        ops_attempted=1,
        ops_accepted=0,
        operations=[],
        embedding_index_mutations=0,
    )

    speculative = [
        ("split_emerge", PhaseResult(
            phase="split_emerge", q_before=0.7, q_after=0.7,
            accepted=True, ops_attempted=0, ops_accepted=0,
            operations=[], embedding_index_mutations=0,
        )),
        ("merge", PhaseResult(
            phase="merge", q_before=0.7, q_after=0.7,
            accepted=True, ops_attempted=0, ops_accepted=0,
            operations=[], embedding_index_mutations=0,
        )),
        ("retire", rejected_retire),
    ]

    deadlock_used, deadlock_phase = _update_phase_rejection_counters(engine, speculative)

    # Counter was 4 → now 5 → threshold reached
    assert deadlock_used is True
    assert deadlock_phase == "retire"
    assert engine._cold_path_needed is True
    # Counter should have been reset after breaker triggers… wait, the helper does NOT
    # reset the counter — that's done in the audit phase.  Verify the counter IS at 5.
    assert engine._phase_rejection_counters["retire"] == 5


@pytest.mark.asyncio
async def test_warm_path_lock_released_on_error(session_factory, mock_embedding, mock_provider):
    """Warm path should release lock even if an error occurs mid-execution."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Create a node with corrupt centroid to trigger error during Q computation
    async with session_factory() as db:
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
    await engine.run_warm_path(session_factory)
    assert not engine._warm_path_lock.locked()


@pytest.mark.asyncio
async def test_warm_path_result_has_q_baseline_and_q_final(session_factory, mock_embedding, mock_provider):
    """WarmPathResult should expose q_baseline and q_final fields."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    result = await engine.run_warm_path(session_factory)
    assert result is not None
    # q_baseline and q_final can be None (no active nodes) or float
    if result.q_baseline is not None:
        assert 0.0 <= result.q_baseline <= 1.0
    if result.q_final is not None:
        assert 0.0 <= result.q_final <= 1.0


@pytest.mark.asyncio
async def test_warm_path_q_system_backward_compat(session_factory, mock_embedding, mock_provider):
    """WarmPathResult.q_system should be auto-set from q_final for backward compat."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    result = await engine.run_warm_path(session_factory)
    assert result is not None
    # q_system must equal q_final (set via __post_init__)
    assert result.q_system == result.q_final
    # Confirm it can be accessed just like the old q_system field
    if result.q_system is not None:
        assert 0.0 <= result.q_system <= 1.0


@pytest.mark.asyncio
async def test_warm_path_result_direct_construction():
    """WarmPathResult.__post_init__ sets q_system from q_final when not provided."""
    result = WarmPathResult(
        snapshot_id="snap-test",
        q_baseline=0.5,
        q_final=0.75,
        phase_results=[],
        operations_attempted=3,
        operations_accepted=2,
        deadlock_breaker_used=False,
        deadlock_breaker_phase=None,
    )
    # q_system should be auto-populated from q_final
    assert result.q_system == 0.75


@pytest.mark.asyncio
async def test_warm_path_result_explicit_q_system_preserved():
    """WarmPathResult.__post_init__ does not overwrite explicit q_system."""
    result = WarmPathResult(
        snapshot_id="snap-test",
        q_baseline=0.5,
        q_final=0.75,
        phase_results=[],
        operations_attempted=3,
        operations_accepted=2,
        deadlock_breaker_used=False,
        deadlock_breaker_phase=None,
        q_system=0.99,  # explicitly provided
    )
    # Should NOT be overwritten by q_final
    assert result.q_system == 0.99


@pytest.mark.asyncio
async def test_warm_path_has_phase_results(session_factory, mock_embedding, mock_provider):
    """WarmPathResult should expose a list of phase_results."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    result = await engine.run_warm_path(session_factory)
    assert result is not None
    assert isinstance(result.phase_results, list)


@pytest.mark.asyncio
async def test_warm_path_deadlock_breaker_phase_field(session_factory, mock_embedding, mock_provider):
    """WarmPathResult should have deadlock_breaker_phase field (None when no deadlock)."""
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    result = await engine.run_warm_path(session_factory)
    assert result is not None
    # deadlock_breaker_phase is None unless a deadlock was triggered
    assert result.deadlock_breaker_phase is None or isinstance(result.deadlock_breaker_phase, str)


# ---------------------------------------------------------------------------
# Stale coherence tests
# ---------------------------------------------------------------------------


def _make_diverse_embeddings(n_topics: int, per_topic: int, rng: np.random.RandomState) -> list[np.ndarray]:
    """Generate embeddings for n_topics distinct clusters.

    Each topic gets a random center with tight samples (spread=0.02).
    Inter-topic similarity is low because random 384-dim vectors are
    nearly orthogonal.
    """
    all_embs: list[np.ndarray] = []
    for _ in range(n_topics):
        center = rng.randn(EMBEDDING_DIM).astype(np.float32)
        center /= np.linalg.norm(center) + 1e-9
        for _ in range(per_topic):
            noise = rng.randn(EMBEDDING_DIM).astype(np.float32) * 0.02
            vec = center + noise
            vec /= np.linalg.norm(vec) + 1e-9
            all_embs.append(vec)
    return all_embs


@pytest.mark.asyncio
async def test_warm_path_recomputes_stale_coherence(session_factory, mock_embedding, mock_provider):
    """Clusters with stale high coherence should be corrected by warm path.

    The hot path never updates coherence, so a cluster that grew from 2 to 10
    members can still show coherence=0.95 when actual pairwise mean is ~0.4.
    The reconciliation phase must always recompute, not just when NULL/0.0.
    """
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    rng = np.random.RandomState(42)

    async with session_factory() as db:
        # Create a cluster with falsely high coherence
        center = rng.randn(EMBEDDING_DIM).astype(np.float32)
        center /= np.linalg.norm(center) + 1e-9

        cluster = PromptCluster(
            label="Stale Coherence Cluster",
            state="active",
            domain="general",
            centroid_embedding=center.tobytes(),
            member_count=10,
            coherence=0.95,  # stale — will not match actual pairwise
            color_hex="#a855f7",
        )
        db.add(cluster)
        await db.flush()

        # Add 10 diverse optimizations (5 topics × 2) — actual coherence will be low
        diverse_embs = _make_diverse_embeddings(5, 2, rng)
        for i, emb in enumerate(diverse_embs):
            opt = Optimization(
                raw_prompt=f"diverse prompt topic {i}",
                cluster_id=cluster.id,
                embedding=emb.astype(np.float32).tobytes(),
            )
            db.add(opt)
        await db.commit()
        cluster_id = cluster.id

    await engine.run_warm_path(session_factory)

    # Refresh from DB using a new session
    async with session_factory() as db:
        from sqlalchemy import select
        refreshed = (await db.execute(
            select(PromptCluster).where(PromptCluster.id == cluster_id)
        )).scalar_one_or_none()
        assert refreshed is not None
        # Coherence should now reflect actual pairwise similarity, not the stale 0.95
        assert refreshed.coherence is not None
        assert refreshed.coherence < 0.6, (
            f"Expected coherence to drop from stale 0.95 to actual pairwise (~0.4), "
            f"got {refreshed.coherence:.3f}"
        )


@pytest.mark.asyncio
async def test_warm_path_recomputes_nonzero_coherence(session_factory, mock_embedding, mock_provider):
    """Reconciliation must recompute coherence even when it's nonzero and non-null.

    Previously, the guard `node.coherence is None or node.coherence == 0.0`
    skipped clusters with any positive coherence value.
    """
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    rng = np.random.RandomState(99)

    # Create a tight 5-member cluster — actual coherence should be high
    tight_embs = make_cluster_distribution("tight cluster test", 5, spread=0.03, rng=rng)

    center = np.mean(tight_embs, axis=0).astype(np.float32)
    center /= np.linalg.norm(center) + 1e-9

    async with session_factory() as db:
        cluster = PromptCluster(
            label="Nonzero Coherence Cluster",
            state="active",
            domain="general",
            centroid_embedding=center.tobytes(),
            member_count=5,
            coherence=0.42,  # intentionally wrong — should be corrected upward
            color_hex="#a855f7",
        )
        db.add(cluster)
        await db.flush()

        for i, emb in enumerate(tight_embs):
            opt = Optimization(
                raw_prompt=f"tight prompt {i}",
                cluster_id=cluster.id,
                embedding=emb.astype(np.float32).tobytes(),
            )
            db.add(opt)
        await db.commit()
        cluster_id = cluster.id

    await engine.run_warm_path(session_factory)

    async with session_factory() as db:
        from sqlalchemy import select
        refreshed = (await db.execute(
            select(PromptCluster).where(PromptCluster.id == cluster_id)
        )).scalar_one_or_none()
        assert refreshed is not None
        # Coherence should be recomputed to the tight cluster's actual pairwise value.
        # With spread=0.03 this is ~0.73.  The key assertion: it was recomputed
        # from the stale 0.42 to something significantly higher.
        assert refreshed.coherence is not None
        assert refreshed.coherence > 0.65, (
            f"Expected tight cluster coherence >0.65, got {refreshed.coherence:.3f} "
            f"(old guard would have left it at 0.42)"
        )


@pytest.mark.asyncio
async def test_split_triggers_on_stale_coherence_cluster(session_factory, mock_embedding, mock_provider):
    """A 14-member mega-cluster with stale coherence should be split.

    With actual pairwise coherence well below the dynamic split floor,
    inline recomputation in split detection should trigger the split
    in a single warm cycle — not require two cycles.
    """
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    rng = np.random.RandomState(77)

    async with session_factory() as db:
        # Create a domain node for the cluster to be parented under
        domain_node = PromptCluster(
            label="general",
            state="domain",
            domain="general",
            centroid_embedding=rng.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
            member_count=14,
            color_hex="#6366f1",
        )
        db.add(domain_node)
        await db.flush()

        # Create a mega-cluster with stale high coherence
        center = rng.randn(EMBEDDING_DIM).astype(np.float32)
        center /= np.linalg.norm(center) + 1e-9

        mega = PromptCluster(
            label="Mega Cluster",
            state="active",
            domain="general",
            parent_id=domain_node.id,
            centroid_embedding=center.tobytes(),
            member_count=14,
            coherence=0.95,  # stale — actual will be ~0.2
            color_hex="#a855f7",
        )
        db.add(mega)
        await db.flush()

        # Add 14 diverse optimizations (7 topics × 2) — low actual coherence
        diverse_embs = _make_diverse_embeddings(7, 2, rng)
        for i, emb in enumerate(diverse_embs):
            opt = Optimization(
                raw_prompt=f"mega topic {i}",
                domain="general",
                cluster_id=mega.id,
                embedding=emb.astype(np.float32).tobytes(),
            )
            db.add(opt)
        await db.commit()

    result = await engine.run_warm_path(session_factory)
    assert result is not None

    # The split should have been attempted
    assert result.operations_attempted >= 1, (
        "Expected at least 1 operation attempted (split), "
        f"got {result.operations_attempted}"
    )
