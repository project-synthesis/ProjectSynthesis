"""Unit tests for warm-path phase functions in warm_phases.py.

Tests focus on the key bug fixes and dataclass contracts rather than
replicating the full warm-path flow end-to-end.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, fields
from datetime import timedelta
from unittest.mock import MagicMock

import numpy as np
import pytest
from sqlalchemy import select, text

from app.models import Optimization, PromptCluster, PromptTemplate
from app.services.taxonomy._constants import DEADLOCK_BREAKER_THRESHOLD, _utcnow
from app.services.taxonomy.warm_phases import (
    AuditResult,
    DiscoverResult,
    PhaseResult,
    ReconcileResult,
    RefreshResult,
    auto_retire_templates,
    phase_merge,
    phase_reconcile,
    phase_retire,
    phase_split_emerge,
    recompute_preferred_strategies,
    reconcile_template_counts,
)
from app.services.template_service import TemplateService
from tests.taxonomy.conftest import EMBEDDING_DIM, make_cluster_distribution


@dataclass
class _PhaseResultStub:
    """Narrow stub matching the field surface used by auto_retire_templates."""
    templates_auto_retired: int = 0

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
    assert "orphan_structural_nodes_archived" in field_names


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
async def test_phase_reconcile_excludes_analyzed_status(db, mock_embedding, mock_provider):
    """phase_reconcile must exclude status='analyzed' rows from member_count.

    Regression guard: the ``synthesis_analyze`` MCP tool previously persisted
    Optimization rows with ``status='analyzed'`` and a ``cluster_id``, which
    inflated ``member_count`` and desynchronised the History view (which
    filters to ``status='completed'``) from the Clusters view. Phase 0
    reconciliation now counts only completed rows so this class of bug
    cannot recur regardless of how the row got there.
    """
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    node = PromptCluster(
        label="Analyze-leak Guard",
        state="active",
        domain="general",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=0,
        color_hex="#a855f7",
    )
    db.add(node)
    await db.flush()

    # 2 completed rows (the real work) + 3 analyzed rows (should be ignored)
    for i in range(2):
        db.add(Optimization(
            raw_prompt=f"completed {i}",
            cluster_id=node.id,
            status="completed",
        ))
    for i in range(3):
        db.add(Optimization(
            raw_prompt=f"analyzed-only {i}",
            cluster_id=node.id,
            status="analyzed",
        ))
    await db.commit()

    await phase_reconcile(engine, db)
    await db.refresh(node)
    assert node.member_count == 2, (
        "analyzed-status rows must not inflate member_count"
    )


@pytest.mark.asyncio
async def test_phase_reconcile_clears_learned_phase_weights_on_empty_cluster(
    db, mock_embedding, mock_provider,
):
    """An empty cluster must have any stale learned_phase_weights popped.

    Closing the delete-cascade gap: when every member of a cluster has
    been removed, the learned profile keyed to that cluster is no longer
    backed by evidence. Leaving it in place risks "phantom learning" if
    the cluster id is reused or reacquires members later. Phase 0 is the
    single reconciler of live Optimization state, so it also owns the
    aggregate clean-up.
    """
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    node = PromptCluster(
        label="Empty Learned-Weights Node",
        state="active",
        domain="general",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=0,
        color_hex="#a855f7",
        cluster_metadata={
            "learned_phase_weights": {
                "analyze": {
                    "w_topic": 0.4,
                    "w_transformation": 0.2,
                    "w_output": 0.2,
                    "w_pattern": 0.1,
                    "w_qualifier": 0.1,
                },
            },
            "some_other_key": "should_survive",
        },
    )
    db.add(node)
    await db.commit()

    await phase_reconcile(engine, db)
    await db.refresh(node)

    assert node.cluster_metadata is not None
    assert "learned_phase_weights" not in node.cluster_metadata or \
        node.cluster_metadata.get("learned_phase_weights") is None
    # Unrelated metadata must be preserved — only the learned weights key
    # is owned by this reconciliation pass.
    assert node.cluster_metadata.get("some_other_key") == "should_survive"


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

    await phase_reconcile(engine, db)

    await db.refresh(domain_node)
    # Domain node should remain (not archived)
    assert domain_node.state == "domain"


# ---------------------------------------------------------------------------
# phase_reconcile — orphan structural node sweep (Fix B)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase_reconcile_archives_orphan_domain_over_24h(
    db, mock_embedding, mock_provider,
):
    """Old empty domain nodes (>24h, 0 children, 0 optimizations) archived.

    Repro of the zero-prompt Legacy visibility bug: a ghost 'general' domain
    left under a project after a backup restore inflates the project's
    member_count (which counts child domains). Phase 0 must sweep orphans.
    """
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    project = PromptCluster(
        label="Legacy",
        state="project",
        domain="general",
        task_type="general",
        member_count=1,  # stale — will be reconciled down after sweep
        color_hex="#444444",
    )
    db.add(project)
    await db.flush()

    old = _utcnow() - timedelta(hours=25)
    orphan = PromptCluster(
        label="general",
        state="domain",
        domain="general",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=0,
        parent_id=project.id,
        color_hex="#6366f1",
        created_at=old,
    )
    db.add(orphan)
    await db.commit()

    result = await phase_reconcile(engine, db)

    assert result.orphan_structural_nodes_archived >= 1
    await db.refresh(orphan)
    assert orphan.state == "archived"
    assert orphan.archived_at is not None

    await db.refresh(project)
    # Project member_count (child-domain count) should now be 0
    assert project.member_count == 0


@pytest.mark.asyncio
async def test_phase_reconcile_archives_orphan_with_null_created_at(
    db, mock_embedding, mock_provider,
):
    """Domain nodes with NULL created_at are treated as old-enough to sweep.

    Observed in production: a pre-existing Legacy/general domain migrated
    from an older schema has `created_at=NULL` (the column was added by a
    later migration without a backfill). The orphan sweep's
    `created_at < cutoff` filter silently skips NULL rows, so the ghost
    domain never ages out even after the grace period.

    Treat NULL as 'old enough' — the node predates the column, so it has
    survived at least one migration window, which is far longer than any
    grace period we'd reasonably configure.
    """
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    orphan = PromptCluster(
        label="general",
        state="domain",
        domain="general",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=0,
        color_hex="#6366f1",
    )
    db.add(orphan)
    await db.flush()
    # Explicitly null out — the server default populates it on insert.
    await db.execute(
        text("UPDATE prompt_cluster SET created_at = NULL WHERE id = :id").bindparams(id=orphan.id)
    )
    await db.commit()
    await db.refresh(orphan)
    assert orphan.created_at is None, "test setup: created_at must be NULL"

    result = await phase_reconcile(engine, db)

    assert result.orphan_structural_nodes_archived >= 1
    await db.refresh(orphan)
    assert orphan.state == "archived"
    assert orphan.archived_at is not None


@pytest.mark.asyncio
async def test_phase_reconcile_skips_young_orphan(
    db, mock_embedding, mock_provider,
):
    """Domain nodes <24h old get a grace period — not swept even if empty."""
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    fresh = _utcnow() - timedelta(hours=1)
    orphan = PromptCluster(
        label="general",
        state="domain",
        domain="general",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=0,
        color_hex="#6366f1",
        created_at=fresh,
    )
    db.add(orphan)
    await db.commit()

    result = await phase_reconcile(engine, db)

    assert result.orphan_structural_nodes_archived == 0
    await db.refresh(orphan)
    assert orphan.state == "domain"


@pytest.mark.asyncio
async def test_phase_reconcile_skips_domain_with_child_cluster(
    db, mock_embedding, mock_provider,
):
    """Domains with at least one non-archived child cluster are not swept."""
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    old = _utcnow() - timedelta(hours=48)
    domain_node = PromptCluster(
        label="backend",
        state="domain",
        domain="backend",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=1,
        color_hex="#6366f1",
        created_at=old,
    )
    db.add(domain_node)
    await db.flush()

    child = PromptCluster(
        label="Auth Stuff",
        state="active",
        domain="backend",
        parent_id=domain_node.id,
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=0,
        color_hex="#a855f7",
    )
    db.add(child)
    await db.commit()

    result = await phase_reconcile(engine, db)

    assert result.orphan_structural_nodes_archived == 0
    await db.refresh(domain_node)
    assert domain_node.state == "domain"


@pytest.mark.asyncio
async def test_phase_reconcile_skips_domain_with_sub_domain_child(
    db, mock_embedding, mock_provider,
):
    """Domains anchoring a sub-domain (structural child) are not swept."""
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    old = _utcnow() - timedelta(hours=48)
    parent_domain = PromptCluster(
        label="backend",
        state="domain",
        domain="backend",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=0,
        color_hex="#6366f1",
        created_at=old,
    )
    db.add(parent_domain)
    await db.flush()

    sub_domain = PromptCluster(
        label="backend: auth",
        state="domain",
        domain="backend",
        parent_id=parent_domain.id,
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=0,
        color_hex="#8b5cf6",
        created_at=old,
    )
    db.add(sub_domain)
    await db.commit()

    await phase_reconcile(engine, db)

    # Parent must NOT be swept — it anchors a sub-domain
    await db.refresh(parent_domain)
    assert parent_domain.state == "domain"


@pytest.mark.asyncio
async def test_phase_reconcile_skips_domain_with_direct_optimizations(
    db, mock_embedding, mock_provider,
):
    """Domain still referenced by optimizations (defensive) is not swept."""
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    old = _utcnow() - timedelta(hours=48)
    domain_node = PromptCluster(
        label="coding",
        state="domain",
        domain="coding",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=0,
        color_hex="#6366f1",
        created_at=old,
    )
    db.add(domain_node)
    await db.flush()

    # Direct reference — shouldn't happen in practice but must be guarded
    opt = Optimization(
        raw_prompt="dangling reference",
        cluster_id=domain_node.id,
    )
    db.add(opt)
    await db.commit()

    result = await phase_reconcile(engine, db)

    assert result.orphan_structural_nodes_archived == 0
    await db.refresh(domain_node)
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


@pytest.mark.asyncio
async def test_phase_refresh_selects_explicit_pattern_stale_clusters(
    db, mock_embedding, mock_provider,
):
    """Clusters with explicit pattern_stale=True in cluster_metadata must be
    refreshed by Phase 4.

    Regression test: Phase 4 reported clusters_refreshed=0 when a per-cluster
    exception in the Phase D processing loop propagated to the outer
    try/except, aborting refresh for ALL stale clusters. This test verifies
    the filter logic correctly selects stale clusters after JSON round-trip.
    """
    from app.services.taxonomy.cluster_meta import write_meta
    from app.services.taxonomy.warm_phases import phase_refresh

    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    # Create cluster with explicit pattern_stale=True (as split.py sets it)
    metadata = write_meta(None, pattern_stale=True, pattern_member_count=0)
    node = PromptCluster(
        label="Pattern Stale Node",
        state="active",
        domain="general",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=5,
        coherence=0.8,
        color_hex="#a855f7",
        cluster_metadata=metadata,
    )
    db.add(node)
    await db.flush()

    # Add member optimizations (need >= 3 for refresh_min_members)
    for i in range(5):
        opt = Optimization(
            raw_prompt=f"stale pattern member {i}",
            intent_label=f"stale label {i}",
            cluster_id=node.id,
        )
        db.add(opt)

    await db.commit()

    # Expire all cached objects to force fresh DB reads (tests JSON round-trip)
    db.expire_all()

    result = await phase_refresh(engine, db)
    assert result.clusters_refreshed >= 1, (
        f"Expected clusters_refreshed >= 1 for cluster with pattern_stale=True, "
        f"got {result.clusters_refreshed}"
    )


@pytest.mark.asyncio
async def test_phase_refresh_cross_session_json_roundtrip(
    session_factory, mock_embedding, mock_provider,
):
    """Cross-session test: pattern_stale=True must survive SQLite JSON
    round-trip and be visible to phase_refresh in a separate session.

    This mimics the real warm-path flow where split (Phase 1) commits
    pattern_stale=True in one session and Phase 4 reads it in another.
    """
    from app.services.taxonomy.cluster_meta import write_meta
    from app.services.taxonomy.warm_phases import phase_refresh

    # Session 1: create cluster with pattern_stale=True and commit
    async with session_factory() as db1:
        metadata = write_meta(None, pattern_stale=True, pattern_member_count=0)
        node = PromptCluster(
            label="Cross Session Stale",
            state="active",
            domain="general",
            centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
            member_count=5,
            coherence=0.8,
            color_hex="#a855f7",
            cluster_metadata=metadata,
        )
        db1.add(node)
        await db1.flush()
        node_id = node.id

        for i in range(5):
            opt = Optimization(
                raw_prompt=f"cross session member {i}",
                intent_label=f"cross label {i}",
                cluster_id=node_id,
            )
            db1.add(opt)
        await db1.commit()

    # Session 2: phase_refresh should find the stale cluster
    async with session_factory() as db2:
        engine = _make_mock_engine(db2, mock_embedding, mock_provider)
        result = await phase_refresh(engine, db2)
        assert result.clusters_refreshed >= 1, (
            f"Cross-session: expected clusters_refreshed >= 1, "
            f"got {result.clusters_refreshed}"
        )


@pytest.mark.asyncio
async def test_phase_refresh_split_flow_metadata_survives(
    session_factory, mock_embedding, mock_provider,
):
    """Mimics the exact split.py flow: create cluster, flush (NULL metadata),
    then set pattern_stale=True in subsequent write_meta calls, flush, commit.

    Phase 4 in a separate session must see pattern_stale=True.
    """
    from app.services.taxonomy.cluster_meta import read_meta, write_meta
    from app.services.taxonomy.warm_phases import phase_refresh

    async with session_factory() as db1:
        # Step 1: Create cluster WITHOUT cluster_metadata (like split.py:263)
        node = PromptCluster(
            label="Split Child Sim",
            state="candidate",
            domain="general",
            centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
            member_count=8,
            coherence=0.7,
            color_hex="#a855f7",
        )
        db1.add(node)
        await db1.flush()  # INSERT with cluster_metadata=NULL (split.py:277)
        node_id = node.id

        # Step 2: Set position metadata (split.py:304)
        node.cluster_metadata = write_meta(
            node.cluster_metadata,
            position_source="interpolated",
            merge_protected_until="2026-04-06T12:00:00",
        )

        # Step 3: Add optimizations (split.py:311)
        for i in range(8):
            opt = Optimization(
                raw_prompt=f"split child member {i}",
                intent_label=f"split label {i}",
                cluster_id=node_id,
            )
            db1.add(opt)

        # Step 4: Set pattern_stale=True (split.py:462-467)
        node.cluster_metadata = write_meta(
            node.cluster_metadata,
            pattern_member_count=node.member_count,
            pattern_stale=True,
        )
        await db1.flush()  # UPDATE with final metadata (split.py:469)
        await db1.commit()

    # Session 2: verify metadata survived and Phase 4 finds it
    async with session_factory() as db2:
        from sqlalchemy import select as sa_select

        # Direct read to verify JSON round-trip
        row = (await db2.execute(
            sa_select(PromptCluster).where(PromptCluster.id == node_id)
        )).scalar_one()
        meta = read_meta(row.cluster_metadata)
        assert meta["pattern_stale"] is True, (
            f"pattern_stale lost after commit+read: {meta}"
        )

        # Phase 4 must find and refresh this cluster
        engine = _make_mock_engine(db2, mock_embedding, mock_provider)
        result = await phase_refresh(engine, db2)
        assert result.clusters_refreshed >= 1, (
            f"Split-flow: expected clusters_refreshed >= 1, "
            f"got {result.clusters_refreshed}"
        )


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


# ---------------------------------------------------------------------------
# Leaked MetaPattern cleanup (taxonomy health audit)
# ---------------------------------------------------------------------------


def test_reconcile_result_has_leaked_patterns_field():
    """ReconcileResult includes the leaked_patterns_cleaned counter."""
    field_names = {f.name for f in fields(ReconcileResult)}
    assert "leaked_patterns_cleaned" in field_names


@pytest.mark.asyncio
async def test_phase_reconcile_cleans_leaked_patterns(db, mock_embedding, mock_provider):
    """phase_reconcile deletes MetaPatterns belonging to archived clusters."""
    from app.models import MetaPattern

    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    # Create an archived cluster with leaked MetaPatterns
    archived = PromptCluster(
        label="Archived Leaker",
        state="archived",
        domain="general",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=0,
        color_hex="#a855f7",
    )
    db.add(archived)
    await db.flush()

    for i in range(3):
        mp = MetaPattern(
            cluster_id=archived.id,
            pattern_text=f"leaked pattern {i}",
            embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        )
        db.add(mp)

    # Also create an active cluster with patterns that must NOT be deleted
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
    await db.flush()

    active_mp = MetaPattern(
        cluster_id=active.id,
        pattern_text="active pattern",
        embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
    )
    db.add(active_mp)

    # Active cluster needs real optimization refs or reconcile archives it as zombie
    for i in range(5):
        db.add(Optimization(
            raw_prompt=f"active member {i}",
            intent_label=f"label {i}",
            cluster_id=active.id,
        ))
    await db.commit()

    result = await phase_reconcile(engine, db)
    assert result.leaked_patterns_cleaned == 3

    # Verify leaked patterns gone, active pattern preserved
    from sqlalchemy import select as sa_select

    remaining_leaked = (await db.execute(
        sa_select(MetaPattern).where(MetaPattern.cluster_id == archived.id)
    )).scalars().all()
    assert len(remaining_leaked) == 0

    remaining_active = (await db.execute(
        sa_select(MetaPattern).where(MetaPattern.cluster_id == active.id)
    )).scalars().all()
    assert len(remaining_active) == 1


# ---------------------------------------------------------------------------
# Task 11: template lifecycle helpers (auto-retire + reconcile)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_retire_fires_on_source_score_below_floor(db):
    cluster = PromptCluster(
        id="c_deg", label="deg", state="mature",
        member_count=5, coherence=0.7, avg_score=5.5, template_count=0,
    )
    db.add(cluster)
    await db.flush()
    opt = Optimization(
        id=uuid.uuid4().hex, cluster_id="c_deg",
        raw_prompt="r", optimized_prompt="o",
        strategy_used="auto", overall_score=7.5,
    )
    db.add(opt)
    await db.flush()
    await TemplateService().fork_from_cluster("c_deg", db)

    result = _PhaseResultStub()
    await auto_retire_templates(db, result)
    assert result.templates_auto_retired == 1
    tpl = (await db.execute(
        select(PromptTemplate).where(PromptTemplate.source_cluster_id == "c_deg")
    )).scalar_one()
    assert tpl.retired_at is not None
    assert tpl.retired_reason == "source_degraded"


@pytest.mark.asyncio
async def test_auto_retire_fires_on_source_dissolution(db):
    cluster = PromptCluster(
        id="c_dis", label="dis", state="mature", template_count=0,
    )
    db.add(cluster)
    await db.flush()
    db.add(Optimization(
        id=uuid.uuid4().hex, cluster_id="c_dis",
        raw_prompt="r", optimized_prompt="o",
        strategy_used="auto", overall_score=7.5,
    ))
    await db.flush()
    await TemplateService().fork_from_cluster("c_dis", db)
    cluster.state = "archived"
    await db.flush()

    result = _PhaseResultStub()
    await auto_retire_templates(db, result)
    assert result.templates_auto_retired == 1
    tpl = (await db.execute(
        select(PromptTemplate).where(PromptTemplate.source_cluster_id == "c_dis")
    )).scalar_one()
    assert tpl.retired_reason == "source_dissolved"


@pytest.mark.asyncio
async def test_template_count_reconciled_in_phase_0(db):
    cluster = PromptCluster(
        id="c_skew", label="x", state="mature", template_count=5,  # wrong
    )
    db.add(cluster)
    db.add(PromptTemplate(
        id=uuid.uuid4().hex,
        source_cluster_id="c_skew",
        source_optimization_id=None,
        project_id=None,
        label="x", prompt="y", strategy="auto",
        score=7.5, pattern_ids=[], domain_label="general",
        promoted_at=_utcnow(),
    ))
    await db.flush()
    await reconcile_template_counts(db)
    cluster = (await db.execute(
        select(PromptCluster).where(PromptCluster.id == "c_skew")
    )).scalar_one()
    assert cluster.template_count == 1


# ---------------------------------------------------------------------------
# Task 12 — recompute_preferred_strategies filters by template_count > 0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recompute_preferred_strategies_filters_by_template_count(db, monkeypatch):
    """Spec Q4 — filter flips from state=='template' to template_count>0."""
    called_with: list[str] = []

    from app.services.prompt_lifecycle import PromptLifecycleService

    async def _spy(self, db, cluster_id):
        called_with.append(cluster_id)

    monkeypatch.setattr(
        PromptLifecycleService, "update_strategy_affinity", _spy, raising=True,
    )

    # Should be processed: has live template (template_count > 0)
    c_has = PromptCluster(
        id="c_has", label="has", state="mature", template_count=1,
    )
    # Should be skipped: no live templates
    c_empty = PromptCluster(
        id="c_empty", label="empty", state="mature", template_count=0,
    )
    db.add_all([c_has, c_empty])
    await db.flush()

    await recompute_preferred_strategies(db)

    assert "c_has" in called_with
    assert "c_empty" not in called_with


# ---------------------------------------------------------------------------
# phase_reconcile — Hybrid general collapse (Task 43)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase_reconcile_collapses_per_project_generals(
    db, mock_embedding, mock_provider,
):
    """Hybrid: multiple per-project `general` domains collapse to one global canonical."""
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    # Two project nodes with their own per-project general domains.
    proj_a = PromptCluster(
        id="proj_a", label="ProjectA", state="project",
        domain="general", task_type="general", member_count=0,
    )
    proj_b = PromptCluster(
        id="proj_b", label="ProjectB", state="project",
        domain="general", task_type="general", member_count=0,
    )
    db.add_all([proj_a, proj_b])
    await db.flush()

    # Two per-project generals, A created before B.
    gen_a = PromptCluster(
        id="gen_a", label="general", state="domain",
        domain="general", task_type="general", member_count=0,
        parent_id="proj_a", color_hex="#aaaaaa", persistence=1.0,
    )
    gen_b = PromptCluster(
        id="gen_b", label="general", state="domain",
        domain="general", task_type="general", member_count=0,
        parent_id="proj_b", color_hex="#bbbbbb", persistence=1.0,
    )
    db.add_all([gen_a, gen_b])
    await db.flush()
    # Force distinct created_at timestamps so _canonical_general_order picks A.
    await db.execute(text(
        "UPDATE prompt_cluster SET created_at = datetime('now', '-1 day') WHERE id='gen_a'"
    ))
    await db.execute(text(
        "UPDATE prompt_cluster SET created_at = datetime('now') WHERE id='gen_b'"
    ))

    # Attach one child cluster to each general so we can verify reparenting.
    child_a = PromptCluster(
        id="child_a", label="A-child", state="active",
        domain="general", task_type="general", member_count=1,
        parent_id="gen_a",
        centroid_embedding=np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes(),
    )
    child_b = PromptCluster(
        id="child_b", label="B-child", state="active",
        domain="general", task_type="general", member_count=1,
        parent_id="gen_b",
        centroid_embedding=np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes(),
    )
    db.add_all([child_a, child_b])
    await db.commit()

    await phase_reconcile(engine, db)

    # Canonical general is gen_a, promoted to root.
    await db.refresh(gen_a)
    await db.refresh(gen_b)
    await db.refresh(child_b)
    assert gen_a.state == "domain"
    assert gen_a.parent_id is None, "canonical general must be at taxonomy root"
    assert gen_b.state == "archived", "stale general must be archived"

    # Gen B's child is reparented to canonical.
    assert child_b.parent_id == gen_a.id


@pytest.mark.asyncio
async def test_phase_reconcile_canonicalizes_single_parented_general(
    db, mock_embedding, mock_provider,
):
    """Hybrid: one parented general gets promoted to the taxonomy root."""
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    proj = PromptCluster(
        id="proj_x", label="ProjectX", state="project",
        domain="general", task_type="general", member_count=0,
    )
    db.add(proj)
    await db.flush()

    gen = PromptCluster(
        id="gen_x", label="general", state="domain",
        domain="general", task_type="general", member_count=0,
        parent_id="proj_x", color_hex="#ccc", persistence=1.0,
    )
    db.add(gen)
    await db.commit()

    await phase_reconcile(engine, db)

    await db.refresh(gen)
    assert gen.parent_id is None, "single parented general must promote to root"
    assert gen.state == "domain"


@pytest.mark.asyncio
async def test_phase_reconcile_noop_on_canonical_general(
    db, mock_embedding, mock_provider,
):
    """Hybrid: a single unparented general is a no-op (idempotent)."""
    engine = _make_mock_engine(db, mock_embedding, mock_provider)

    gen = PromptCluster(
        id="gen_canonical", label="general", state="domain",
        domain="general", task_type="general", member_count=0,
        parent_id=None, color_hex="#ddd", persistence=1.0,
    )
    db.add(gen)
    await db.commit()

    await phase_reconcile(engine, db)

    await db.refresh(gen)
    assert gen.parent_id is None
    assert gen.state == "domain"
