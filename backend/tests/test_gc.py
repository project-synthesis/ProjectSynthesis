"""Dedicated unit coverage for ``services.gc``.

The startup GC has three independent passes — failed optimizations,
archived zero-member clusters, and orphan meta-patterns — each with
explicit safety gates. Previously the module sat at 24% isolated
coverage because none of the suite exercised its entry point directly.

This file pins the contract:

1. ``_gc_failed_optimizations`` — deletes only rows with
   ``status='failed' AND optimized_prompt IS NULL`` + cascades to the
   three dependent tables (``Feedback``, ``RefinementTurn``,
   ``OptimizationPattern``). Other failed rows with partial output
   are preserved.
2. ``_gc_archived_zero_member_clusters`` — candidate filter is
   ``state='archived' AND member_count=0``. Safety gates: skip if
   any ``Optimization.cluster_id`` / ``OptimizationPattern.cluster_id``
   / child ``PromptCluster.parent_id`` still references the row. Only
   fully-dangling tombstones are reaped.
3. ``_gc_orphan_meta_patterns`` — deletes meta-patterns whose
   ``cluster_id`` no longer resolves in ``prompt_cluster`` (subquery
   NOT IN).
4. ``run_startup_gc`` — orchestrates all three, commits iff any pass
   cleaned >0 rows, silent (debug-log) on no-ops.

Copyright 2025-2026 Project Synthesis contributors.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import (
    Feedback,
    MetaPattern,
    Optimization,
    OptimizationPattern,
    ProbeRun,
    PromptCluster,
    RefinementTurn,
)
from app.services.gc import (
    _gc_archived_zero_member_clusters,
    _gc_failed_optimizations,
    _gc_orphan_meta_patterns,
    _gc_orphan_probe_runs,
    run_startup_gc,
)

# ---------------------------------------------------------------------------
# _gc_failed_optimizations
# ---------------------------------------------------------------------------

class TestGCFailedOptimizations:
    async def test_deletes_failed_with_null_output(self, db_session):
        opt_id = str(uuid.uuid4())
        db_session.add(Optimization(
            id=opt_id,
            raw_prompt="raw",
            optimized_prompt=None,  # failed before output
            status="failed",
        ))
        await db_session.commit()

        deleted = await _gc_failed_optimizations(db_session)
        await db_session.commit()

        assert deleted == 1
        remaining = (await db_session.execute(
            select(Optimization.id).where(Optimization.id == opt_id)
        )).scalar_one_or_none()
        assert remaining is None

    async def test_preserves_failed_with_partial_output(self, db_session):
        """A failed row with optimized_prompt set is preserved — it still
        carries useful data (partial output, audit trail)."""
        opt_id = str(uuid.uuid4())
        db_session.add(Optimization(
            id=opt_id,
            raw_prompt="raw",
            optimized_prompt="partial output recovered",
            status="failed",
        ))
        await db_session.commit()

        deleted = await _gc_failed_optimizations(db_session)
        assert deleted == 0

        still_there = await db_session.get(Optimization, opt_id)
        assert still_there is not None

    async def test_preserves_completed_rows(self, db_session):
        opt_id = str(uuid.uuid4())
        db_session.add(Optimization(
            id=opt_id,
            raw_prompt="raw",
            optimized_prompt="good output",
            status="completed",
        ))
        await db_session.commit()

        deleted = await _gc_failed_optimizations(db_session)
        assert deleted == 0

    async def test_cascades_feedback_refinement_and_patterns(self, db_session):
        """Before deleting the optimization, dependent rows in Feedback +
        RefinementTurn + OptimizationPattern must go first (FK safety)."""
        opt_id = str(uuid.uuid4())
        cluster_id = str(uuid.uuid4())

        db_session.add_all([
            PromptCluster(id=cluster_id, label="C", state="active"),
            Optimization(
                id=opt_id,
                raw_prompt="raw",
                optimized_prompt=None,
                status="failed",
            ),
            Feedback(optimization_id=opt_id, rating="thumbs_up"),
            RefinementTurn(
                optimization_id=opt_id,
                branch_id=str(uuid.uuid4()),
                version=1,
                prompt="refined prompt",
                strategy_used="auto",
            ),
            OptimizationPattern(
                optimization_id=opt_id,
                cluster_id=cluster_id,
                relationship="source",
            ),
        ])
        await db_session.commit()

        deleted = await _gc_failed_optimizations(db_session)
        await db_session.commit()

        assert deleted == 1
        # All dependent rows must be gone.
        fb = (await db_session.execute(
            select(Feedback).where(Feedback.optimization_id == opt_id)
        )).scalars().all()
        assert fb == []
        rt = (await db_session.execute(
            select(RefinementTurn).where(RefinementTurn.optimization_id == opt_id)
        )).scalars().all()
        assert rt == []
        op = (await db_session.execute(
            select(OptimizationPattern).where(
                OptimizationPattern.optimization_id == opt_id
            )
        )).scalars().all()
        assert op == []

    async def test_empty_database_is_noop(self, db_session):
        assert await _gc_failed_optimizations(db_session) == 0


# ---------------------------------------------------------------------------
# _gc_archived_zero_member_clusters
# ---------------------------------------------------------------------------

class TestGCArchivedZeroMemberClusters:
    async def test_deletes_dangling_tombstone(self, db_session):
        cid = str(uuid.uuid4())
        db_session.add(PromptCluster(
            id=cid,
            label="archived-tombstone",
            state="archived",
            member_count=0,
        ))
        await db_session.commit()

        deleted = await _gc_archived_zero_member_clusters(db_session)
        await db_session.commit()

        assert deleted == 1
        assert await db_session.get(PromptCluster, cid) is None

    async def test_preserves_if_optimization_still_references(self, db_session):
        cid = str(uuid.uuid4())
        opt_id = str(uuid.uuid4())
        db_session.add_all([
            PromptCluster(
                id=cid, label="c", state="archived", member_count=0,
            ),
            Optimization(
                id=opt_id,
                raw_prompt="r",
                optimized_prompt="o",
                status="completed",
                cluster_id=cid,  # still referencing
            ),
        ])
        await db_session.commit()

        deleted = await _gc_archived_zero_member_clusters(db_session)
        assert deleted == 0
        assert await db_session.get(PromptCluster, cid) is not None

    async def test_preserves_if_optimization_pattern_still_references(
        self, db_session,
    ):
        cid = str(uuid.uuid4())
        other_cid = str(uuid.uuid4())
        opt_id = str(uuid.uuid4())
        db_session.add_all([
            PromptCluster(
                id=cid, label="c", state="archived", member_count=0,
            ),
            PromptCluster(id=other_cid, label="o", state="active"),
            Optimization(
                id=opt_id, raw_prompt="r", optimized_prompt="o",
                status="completed", cluster_id=other_cid,
            ),
            OptimizationPattern(
                optimization_id=opt_id,
                cluster_id=cid,  # pattern still points at archived row
                relationship="injected",
            ),
        ])
        await db_session.commit()

        deleted = await _gc_archived_zero_member_clusters(db_session)
        assert deleted == 0

    async def test_preserves_if_child_cluster_still_references_as_parent(
        self, db_session,
    ):
        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        db_session.add_all([
            PromptCluster(
                id=parent_id, label="archived-parent",
                state="archived", member_count=0,
            ),
            PromptCluster(
                id=child_id, label="child", parent_id=parent_id,
                state="active",
            ),
        ])
        await db_session.commit()

        deleted = await _gc_archived_zero_member_clusters(db_session)
        assert deleted == 0

    async def test_preserves_active_clusters_even_with_zero_members(
        self, db_session,
    ):
        cid = str(uuid.uuid4())
        db_session.add(PromptCluster(
            id=cid, label="active-empty", state="active", member_count=0,
        ))
        await db_session.commit()

        deleted = await _gc_archived_zero_member_clusters(db_session)
        assert deleted == 0
        assert await db_session.get(PromptCluster, cid) is not None

    async def test_cascades_meta_patterns_for_safe_ids_only(self, db_session):
        """MetaPatterns on the dangling cluster are dropped; MetaPatterns on
        the referenced cluster survive."""
        safe_cid = str(uuid.uuid4())      # will be deleted
        unsafe_cid = str(uuid.uuid4())    # referenced → skipped
        other_cid = str(uuid.uuid4())     # active, for the opt to point at
        opt_id = str(uuid.uuid4())

        db_session.add_all([
            PromptCluster(
                id=safe_cid, label="safe",
                state="archived", member_count=0,
            ),
            PromptCluster(
                id=unsafe_cid, label="unsafe",
                state="archived", member_count=0,
            ),
            PromptCluster(id=other_cid, label="active", state="active"),
            Optimization(
                id=opt_id, raw_prompt="r", optimized_prompt="o",
                status="completed", cluster_id=other_cid,
            ),
            OptimizationPattern(
                optimization_id=opt_id, cluster_id=unsafe_cid,
                relationship="injected",
            ),
            MetaPattern(
                cluster_id=safe_cid, pattern_text="safe-pat",
            ),
            MetaPattern(
                cluster_id=unsafe_cid, pattern_text="unsafe-pat",
            ),
        ])
        await db_session.commit()

        deleted = await _gc_archived_zero_member_clusters(db_session)
        await db_session.commit()
        assert deleted == 1

        safe_pats = (await db_session.execute(
            select(MetaPattern).where(MetaPattern.cluster_id == safe_cid)
        )).scalars().all()
        assert safe_pats == []  # cascaded out
        unsafe_pats = (await db_session.execute(
            select(MetaPattern).where(MetaPattern.cluster_id == unsafe_cid)
        )).scalars().all()
        assert len(unsafe_pats) == 1

    async def test_no_candidates_returns_zero(self, db_session):
        assert await _gc_archived_zero_member_clusters(db_session) == 0


# ---------------------------------------------------------------------------
# _gc_orphan_meta_patterns
# ---------------------------------------------------------------------------

class TestGCOrphanMetaPatterns:
    async def test_deletes_patterns_with_missing_cluster(self, db_session):
        """Pattern with a cluster_id that doesn't resolve → orphan."""
        orphan_cluster_id = str(uuid.uuid4())  # never persisted
        db_session.add(MetaPattern(
            cluster_id=orphan_cluster_id,
            pattern_text="orphan",
        ))
        await db_session.commit()

        deleted = await _gc_orphan_meta_patterns(db_session)
        await db_session.commit()

        assert deleted == 1
        remaining = (await db_session.execute(
            select(MetaPattern).where(
                MetaPattern.cluster_id == orphan_cluster_id,
            )
        )).scalars().all()
        assert remaining == []

    async def test_preserves_patterns_with_live_cluster(self, db_session):
        cid = str(uuid.uuid4())
        db_session.add_all([
            PromptCluster(id=cid, label="live", state="active"),
            MetaPattern(cluster_id=cid, pattern_text="live-pat"),
        ])
        await db_session.commit()

        deleted = await _gc_orphan_meta_patterns(db_session)
        assert deleted == 0

        pat = (await db_session.execute(
            select(MetaPattern).where(MetaPattern.cluster_id == cid)
        )).scalars().one()
        assert pat.pattern_text == "live-pat"


# ---------------------------------------------------------------------------
# run_startup_gc — orchestrator
# ---------------------------------------------------------------------------

class TestRunStartupGC:
    async def test_runs_all_three_passes_and_commits(self, db_session):
        """Seeds one item per pass — verifies the orchestrator wires them."""
        # Pass 1: failed opt
        failed_opt_id = str(uuid.uuid4())
        # Pass 2: dangling archived cluster
        arch_cid = str(uuid.uuid4())
        # Pass 3: orphan meta-pattern (cluster never persisted)
        orphan_cid = str(uuid.uuid4())

        db_session.add_all([
            Optimization(
                id=failed_opt_id, raw_prompt="r",
                optimized_prompt=None, status="failed",
            ),
            PromptCluster(
                id=arch_cid, label="arch",
                state="archived", member_count=0,
            ),
            MetaPattern(
                cluster_id=orphan_cid, pattern_text="orphan",
            ),
        ])
        await db_session.commit()

        await run_startup_gc(db_session)

        # All three should be gone after the commit inside run_startup_gc.
        assert await db_session.get(Optimization, failed_opt_id) is None
        assert await db_session.get(PromptCluster, arch_cid) is None
        orphan = (await db_session.execute(
            select(MetaPattern).where(MetaPattern.cluster_id == orphan_cid)
        )).scalars().all()
        assert orphan == []

    async def test_empty_db_is_safe(self, db_session):
        """Orchestrator on a fresh DB must not raise and must not commit."""
        # Should simply log and return without touching anything.
        await run_startup_gc(db_session)


# ---------------------------------------------------------------------------
# _gc_orphan_probe_runs
# ---------------------------------------------------------------------------

class TestGCOrphanProbeRuns:
    """Foundation P3 (v0.4.18): ``_gc_orphan_probe_runs`` is a no-op stub.

    Superseded by ``_gc_orphan_runs`` which sweeps both ``topic_probe`` and
    ``seed_agent`` mode rows in one pass. The legacy helper is preserved as
    a no-op returning 0 so ``run_startup_gc._do_sweep`` can keep calling
    both helpers without double-processing the same row set (the
    ``ProbeRun`` Python alias selects ALL ``run_row`` rows regardless of
    mode — STI discriminator absent). Deleted in PR2.

    See ``test_gc_runs.py`` for the live contract on
    ``_gc_orphan_runs``.
    """

    async def test_legacy_helper_is_no_op_after_p3(self, db_session):
        """v0.4.18 Foundation P3: the legacy helper returns 0 even when
        orphan rows are present. ``_gc_orphan_runs`` covers what it did."""
        old = datetime.now(timezone.utc) - timedelta(hours=2)

        db_session.add(ProbeRun(
            id="orphan-old", topic="x", scope="**/*",
            intent_hint="explore", repo_full_name="o/r",
            started_at=old, status="running",
        ))
        await db_session.commit()

        cleaned = await _gc_orphan_probe_runs(db_session)
        assert cleaned == 0  # No-op; row is left untouched here.

    async def test_no_orphans_returns_zero(self, db_session):
        """Idempotent: safe to call on a DB with no orphan rows."""
        cleaned = await _gc_orphan_probe_runs(db_session)
        assert cleaned == 0
