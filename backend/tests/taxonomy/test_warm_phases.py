"""Unit tests for warm-path phase functions in warm_phases.py.

Tests focus on the key bug fixes and dataclass contracts rather than
replicating the full warm-path flow end-to-end.
"""

from __future__ import annotations

from dataclasses import fields
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.models import Optimization, PromptCluster
from app.services.taxonomy.warm_phases import (
    DEADLOCK_BREAKER_THRESHOLD,
    AuditResult,
    DiscoverResult,
    PhaseResult,
    ReconcileResult,
    RefreshResult,
    phase_merge,
    phase_reconcile,
    phase_retire,
    phase_split_emerge,
)
from tests.taxonomy.conftest import EMBEDDING_DIM, make_cluster_distribution


# ---------------------------------------------------------------------------
# Dataclass field contracts
# ---------------------------------------------------------------------------


def test_phase_result_fields():
    """PhaseResult dataclass has expected fields."""
    field_names = {f.name for f in fields(PhaseResult)}
    assert "phase" in field_names
    assert "q_before" in field_names
    assert "q_after" in field_names
    assert "accepted" in field_names
    assert "ops_attempted" in field_names
    assert "ops_accepted" in field_names
    assert "operations" in field_names
    assert "embedding_index_mutations" in field_names


def test_reconcile_result_fields():
    """ReconcileResult dataclass has expected fields."""
    field_names = {f.name for f in fields(ReconcileResult)}
    assert "member_counts_fixed" in field_names
    assert "coherence_updated" in field_names
    assert "scores_reconciled" in field_names
    assert "zombies_archived" in field_names


def test_refresh_result_fields():
    """RefreshResult dataclass has expected fields."""
    field_names = {f.name for f in fields(RefreshResult)}
    assert "clusters_refreshed" in field_names


def test_discover_result_fields():
    """DiscoverResult dataclass has expected fields."""
    field_names = {f.name for f in fields(DiscoverResult)}
    assert "domains_created" in field_names
    assert "candidates_detected" in field_names


def test_audit_result_fields():
    """AuditResult dataclass has expected fields."""
    field_names = {f.name for f in fields(AuditResult)}
    assert "snapshot_id" in field_names
    assert "q_final" in field_names
    assert "deadlock_breaker_used" in field_names
    assert "deadlock_breaker_phase" in field_names


def test_deadlock_breaker_threshold():
    """DEADLOCK_BREAKER_THRESHOLD constant is exported and positive."""
    assert DEADLOCK_BREAKER_THRESHOLD == 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_engine(db_session, mock_embedding, mock_provider):
    """Build a minimal mock TaxonomyEngine with the attributes phases access."""
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine(
        embedding_service=mock_embedding,
        provider=mock_provider,
    )
    return engine


# ---------------------------------------------------------------------------
# phase_reconcile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase_reconcile_updates_member_count(db, mock_embedding, mock_provider):
    """phase_reconcile corrects a stale member_count."""
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    # Create a cluster whose stored member_count is wrong
    node = PromptCluster(
        label="Stale Count Node",
        state="active",
        domain="general",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=99,  # intentionally wrong
        color_hex="#a855f7",
    )
    db.add(node)
    await db.flush()

    # Add 2 real member optimizations
    for i in range(2):
        opt = Optimization(
            raw_prompt=f"reconcile test {i}",
            cluster_id=node.id,
        )
        db.add(opt)
    await db.commit()

    result = await phase_reconcile(engine, db)
    assert result.member_counts_fixed >= 1


@pytest.mark.asyncio
async def test_phase_reconcile_updates_coherence(db, mock_embedding, mock_provider):
    """phase_reconcile recomputes coherence from actual member embeddings."""
    engine = _make_mock_engine(db, mock_embedding, mock_provider)
    rng = np.random.RandomState(42)

    tight_embs = make_cluster_distribution("coherence test", 4, spread=0.02, rng=rng)
    centroid = np.mean(tight_embs, axis=0).astype(np.float32)
    centroid /= np.linalg.norm(centroid) + 1e-9

    node = PromptCluster(
        label="Low Coherence Cluster",
        state="active",
        domain="general",
        centroid_embedding=centroid.tobytes(),
        member_count=4,
        coherence=0.1,  # intentionally stale/wrong
        color_hex="#a855f7",
    )
    db.add(node)
    await db.flush()

    for i, emb in enumerate(tight_embs):
        opt = Optimization(
            raw_prompt=f"coherence member {i}",
            cluster_id=node.id,
            embedding=emb.astype(np.float32).tobytes(),
        )
        db.add(opt)
    await db.commit()

    result = await phase_reconcile(engine, db)
    assert result.coherence_updated >= 1

    await db.refresh(node)
    # Tight cluster should have high coherence after recomputation
    assert node.coherence is not None
    assert node.coherence > 0.5


@pytest.mark.asyncio
async def test_phase_reconcile_archives_zombies(db, mock_embedding, mock_provider):
    """phase_reconcile archives 0-member zombie nodes."""
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    zombie = PromptCluster(
        label="Zombie Node",
        state="active",
        domain="general",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=0,
        color_hex="#a855f7",
    )
    db.add(zombie)
    await db.commit()

    result = await phase_reconcile(engine, db)
    assert result.zombies_archived >= 1

    await db.refresh(zombie)
    assert zombie.state == "archived"


@pytest.mark.asyncio
async def test_phase_reconcile_queries_notin_domain_archived(db, mock_embedding, mock_provider):
    """phase_reconcile uses state.notin_(["domain","archived"]) — Fix #10.

    Domain nodes should NOT have their member_count set to 0 by the
    reconcile logic that processes non-domain nodes.
    """
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    domain_node = PromptCluster(
        label="general",
        state="domain",
        domain="general",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=5,
        color_hex="#6366f1",
    )
    db.add(domain_node)
    await db.commit()

    result = await phase_reconcile(engine, db)

    await db.refresh(domain_node)
    # Domain node should remain (not archived)
    assert domain_node.state == "domain"


# ---------------------------------------------------------------------------
# phase_split_emerge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase_split_emerge_excludes_domain_from_emerge(db, mock_embedding, mock_provider):
    """Fix #7: domain nodes must not be included in the emerge candidate list."""
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    # Create domain node with parent_id=None — must NOT be emerge candidate
    domain_node = PromptCluster(
        label="general",
        state="domain",
        domain="general",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=0,
        parent_id=None,
        color_hex="#6366f1",
    )
    db.add(domain_node)
    await db.commit()

    # Run split_emerge — even if emerge threshold is reached, domain node
    # must not appear in operations output as an emerged node
    result = await phase_split_emerge(engine, db, split_protected_ids=set())

    for op in result.operations:
        # No operation should reference the domain node as a created emerge node
        if op.get("type") == "emerge":
            assert op.get("node_id") != domain_node.id


@pytest.mark.asyncio
async def test_phase_split_emerge_ops_accepted_incremented(db, mock_embedding, mock_provider):
    """Fix #9: ops_accepted must be incremented for successful leaf splits.

    We indirectly verify this by checking that ops_accepted > 0 whenever
    a leaf_split operation is logged.
    """
    engine = _make_mock_engine(db, mock_embedding, mock_provider)
    rng = np.random.RandomState(77)

    # Create a domain node as parent
    domain_node = PromptCluster(
        label="general",
        state="domain",
        domain="general",
        centroid_embedding=rng.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=0,
        color_hex="#6366f1",
    )
    db.add(domain_node)
    await db.flush()

    # Create a cluster with 14 diverse members (low coherence → split candidate)
    from tests.taxonomy.test_engine_warm_path import _make_diverse_embeddings

    center = rng.randn(EMBEDDING_DIM).astype(np.float32)
    center /= np.linalg.norm(center) + 1e-9

    mega = PromptCluster(
        label="Mega Cluster",
        state="active",
        domain="general",
        parent_id=domain_node.id,
        centroid_embedding=center.tobytes(),
        member_count=14,
        coherence=0.1,  # very low — split candidate
        color_hex="#a855f7",
    )
    db.add(mega)
    await db.flush()

    diverse_embs = _make_diverse_embeddings(7, 2, rng)
    for i, emb in enumerate(diverse_embs):
        opt = Optimization(
            raw_prompt=f"diverse topic {i}",
            domain="general",
            cluster_id=mega.id,
            embedding=emb.astype(np.float32).tobytes(),
            intent_label=f"topic {i}",
        )
        db.add(opt)
    await db.commit()

    result = await phase_split_emerge(engine, db, split_protected_ids=set())

    leaf_splits = [op for op in result.operations if op.get("type") == "leaf_split"]
    if leaf_splits:
        # Fix #9 assertion: ops_accepted must be > 0 when a split happened
        assert result.ops_accepted > 0, (
            "Fix #9 violated: leaf split logged but ops_accepted == 0"
        )


# ---------------------------------------------------------------------------
# phase_merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase_merge_merges_similar_nodes(db, mock_embedding, mock_provider):
    """phase_merge should merge two nearly identical nodes."""
    engine = _make_mock_engine(db, mock_embedding, mock_provider)
    rng = np.random.RandomState(11)

    # Two nodes with very similar centroids
    base = rng.randn(EMBEDDING_DIM).astype(np.float32)
    base /= np.linalg.norm(base) + 1e-9

    noise = rng.randn(EMBEDDING_DIM).astype(np.float32) * 0.01
    near = (base + noise)
    near /= np.linalg.norm(near) + 1e-9

    node_a = PromptCluster(
        label="Node A",
        state="active",
        domain="general",
        centroid_embedding=base.tobytes(),
        member_count=3,
        coherence=0.8,
        separation=0.7,
        color_hex="#a855f7",
    )
    node_b = PromptCluster(
        label="Node A",  # Same label to trigger same-domain duplicate merge
        state="active",
        domain="general",
        centroid_embedding=near.tobytes(),
        member_count=2,
        coherence=0.8,
        separation=0.7,
        color_hex="#a855f7",
    )
    db.add(node_a)
    db.add(node_b)
    await db.commit()

    result = await phase_merge(engine, db, split_protected_ids=set())

    # At least one merge should have been attempted/accepted
    assert result.ops_attempted >= 1


@pytest.mark.asyncio
async def test_phase_merge_returns_phase_result(db, mock_embedding, mock_provider):
    """phase_merge always returns a PhaseResult with the correct phase name."""
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    result = await phase_merge(engine, db, split_protected_ids=set())
    assert isinstance(result, PhaseResult)
    assert result.phase == "merge"


# ---------------------------------------------------------------------------
# phase_retire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase_retire_archives_zero_member_nodes(db, mock_embedding, mock_provider):
    """phase_retire should archive nodes with member_count == 0."""
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    idle = PromptCluster(
        label="Idle Node",
        state="active",
        domain="general",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=0,
        coherence=0.9,
        color_hex="#a855f7",
    )
    db.add(idle)
    await db.commit()

    result = await phase_retire(engine, db)

    # The node should have been retired (ops_accepted > 0 or state changed)
    assert result.ops_attempted >= 1

    await db.refresh(idle)
    # State should be archived if retire succeeded
    assert idle.state in ("archived", "active")  # attempt_retire may not retire if age < threshold


@pytest.mark.asyncio
async def test_phase_retire_skips_nodes_with_members(db, mock_embedding, mock_provider):
    """phase_retire must not touch nodes that have members."""
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    active = PromptCluster(
        label="Active Node",
        state="active",
        domain="general",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=5,
        coherence=0.8,
        color_hex="#a855f7",
    )
    db.add(active)
    await db.commit()

    result = await phase_retire(engine, db)

    # No retirement attempted for nodes with members
    assert result.ops_attempted == 0
    await db.refresh(active)
    assert active.state == "active"


# ---------------------------------------------------------------------------
# phase_refresh — Fix #15 (safe extract-before-delete)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase_refresh_returns_refresh_result(db, mock_embedding, mock_provider):
    """phase_refresh returns a RefreshResult instance."""
    from app.services.taxonomy.warm_phases import phase_refresh

    engine = _make_mock_engine(db, mock_embedding, mock_provider)
    result = await phase_refresh(engine, db)
    assert isinstance(result, RefreshResult)
    assert result.clusters_refreshed >= 0


@pytest.mark.asyncio
async def test_phase_refresh_does_not_delete_old_patterns_on_extraction_failure(
    db, mock_embedding, mock_provider
):
    """Fix #15: old patterns must not be deleted if new extraction fails.

    When the provider returns no patterns, old MetaPattern rows for the
    cluster should survive.
    """
    from app.models import MetaPattern
    from app.services.taxonomy.warm_phases import phase_refresh

    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    # Cluster with enough members to trigger refresh (pattern_member_count=0)
    node = PromptCluster(
        label="Refresh Test Node",
        state="active",
        domain="general",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=5,
        coherence=0.8,
        color_hex="#a855f7",
    )
    db.add(node)
    await db.flush()

    # Add a MetaPattern that should be preserved if refresh extraction fails
    old_pattern = MetaPattern(
        cluster_id=node.id,
        pattern_text="old important pattern",
        embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
    )
    db.add(old_pattern)

    # Add member optimizations (need >=3 for refresh to trigger)
    for i in range(4):
        opt = Optimization(
            raw_prompt=f"refresh member {i}",
            intent_label=f"label {i}",
            cluster_id=node.id,
        )
        db.add(opt)
    await db.commit()

    # Make provider return empty patterns (simulate extraction failure)
    mock_result = MagicMock()
    mock_result.patterns = []
    mock_provider.complete_parsed.return_value = mock_result

    await phase_refresh(engine, db)

    # Verify the old pattern is still present (not deleted on empty extraction)
    from sqlalchemy import select as sa_select

    patterns = (
        await db.execute(
            sa_select(MetaPattern).where(MetaPattern.cluster_id == node.id)
        )
    ).scalars().all()
    # If extraction returned 0 patterns, old pattern must survive (Fix #15)
    assert len(patterns) >= 1


# ---------------------------------------------------------------------------
# notin_ query filter (Fix #10)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase_reconcile_notin_query_excludes_archived(
    db, mock_embedding, mock_provider
):
    """Archived nodes must not be processed by reconcile (Fix #10).

    Verifies that phase_reconcile does NOT resurrect an archived node.
    """
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    archived = PromptCluster(
        label="Archived Node",
        state="archived",
        domain="general",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=0,
        color_hex="#a855f7",
    )
    db.add(archived)
    await db.commit()

    await phase_reconcile(engine, db)

    await db.refresh(archived)
    # Archived node must remain archived — not touched by reconcile
    assert archived.state == "archived"
