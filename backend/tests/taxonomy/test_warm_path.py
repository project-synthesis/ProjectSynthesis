"""Integration tests for warm_path.py — the warm-path orchestrator.

Tests the WarmPathResult dataclass contract, phase ordering, speculative
phase execution (commit/rollback), and per-phase deadlock breaker.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import fields
from unittest.mock import patch

import numpy as np
import pytest

from app.models import PromptCluster
from app.services.taxonomy._constants import DEADLOCK_BREAKER_THRESHOLD
from app.services.taxonomy.warm_path import (
    WarmPathResult,
    _run_speculative_phase,
    _update_phase_rejection_counters,
    execute_maintenance_phases,
    execute_warm_path,
)
from app.services.taxonomy.warm_phases import PhaseResult
from tests.taxonomy.conftest import EMBEDDING_DIM

# ---------------------------------------------------------------------------
# WarmPathResult dataclass
# ---------------------------------------------------------------------------


def test_warm_path_result_q_system_backward_compat():
    """WarmPathResult: q_system is auto-populated from q_final via __post_init__."""
    result = WarmPathResult(
        snapshot_id="snap-1",
        q_baseline=0.5,
        q_final=0.72,
        phase_results=[],
        operations_attempted=3,
        operations_accepted=2,
        deadlock_breaker_used=False,
        deadlock_breaker_phase=None,
        # q_system omitted — should be filled from q_final
    )
    assert result.q_system == 0.72


def test_warm_path_result_q_system_explicit_value():
    """WarmPathResult: explicit q_system is not overwritten."""
    result = WarmPathResult(
        snapshot_id="snap-2",
        q_baseline=0.5,
        q_final=0.72,
        phase_results=[],
        operations_attempted=3,
        operations_accepted=2,
        deadlock_breaker_used=False,
        deadlock_breaker_phase=None,
        q_system=0.99,
    )
    assert result.q_system == 0.99


def test_warm_path_result_q_system_none_when_q_final_none():
    """WarmPathResult: q_system stays None when q_final is also None."""
    result = WarmPathResult(
        snapshot_id="snap-3",
        q_baseline=None,
        q_final=None,
        phase_results=[],
        operations_attempted=0,
        operations_accepted=0,
        deadlock_breaker_used=False,
        deadlock_breaker_phase=None,
    )
    assert result.q_system is None


def test_warm_path_result_fields():
    """WarmPathResult has all expected fields."""
    field_names = {f.name for f in fields(WarmPathResult)}
    assert "snapshot_id" in field_names
    assert "q_baseline" in field_names
    assert "q_final" in field_names
    assert "q_system" in field_names
    assert "phase_results" in field_names
    assert "operations_attempted" in field_names
    assert "operations_accepted" in field_names
    assert "deadlock_breaker_used" in field_names
    assert "deadlock_breaker_phase" in field_names


# ---------------------------------------------------------------------------
# execute_warm_path — phase ordering
# ---------------------------------------------------------------------------


def _make_phase_result(phase: str, ops_attempted: int = 0, ops_accepted: int = 0) -> PhaseResult:
    return PhaseResult(
        phase=phase,
        q_before=0.5,
        q_after=0.5,
        accepted=ops_accepted > 0,
        ops_attempted=ops_attempted,
        ops_accepted=ops_accepted,
    )


@pytest.mark.asyncio
async def test_execute_warm_path_phases_called_in_order(db, mock_embedding, mock_provider):
    """execute_warm_path calls phases in the correct order (0→6)."""
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    call_order: list[str] = []

    async def fake_reconcile(eng, session):
        call_order.append("reconcile")
        from app.services.taxonomy.warm_phases import ReconcileResult
        return ReconcileResult()

    async def fake_split_emerge(eng, session, split_protected_ids, dirty_ids=None):
        call_order.append("split_emerge")
        return _make_phase_result("split_emerge")

    async def fake_merge(eng, session, split_protected_ids, dirty_ids=None):
        call_order.append("merge")
        return _make_phase_result("merge")

    async def fake_retire(eng, session):
        call_order.append("retire")
        return _make_phase_result("retire")

    async def fake_refresh(eng, session):
        call_order.append("refresh")
        from app.services.taxonomy.warm_phases import RefreshResult
        return RefreshResult()

    async def fake_discover(eng, session):
        call_order.append("discover")
        from app.services.taxonomy.warm_phases import DiscoverResult
        return DiscoverResult()

    async def fake_archive_sub_domains(eng, session):
        call_order.append("archive_sub_domains")
        return 0

    async def fake_audit(eng, session, phase_results, q_baseline):
        call_order.append("audit")
        from app.services.taxonomy.warm_phases import AuditResult
        return AuditResult(snapshot_id="snap-test", q_final=0.5)

    @asynccontextmanager
    async def session_factory():
        yield db

    with (
        patch("app.services.taxonomy.warm_path.phase_reconcile", fake_reconcile),
        patch("app.services.taxonomy.warm_path.phase_split_emerge", fake_split_emerge),
        patch("app.services.taxonomy.warm_path.phase_merge", fake_merge),
        patch("app.services.taxonomy.warm_path.phase_retire", fake_retire),
        patch("app.services.taxonomy.warm_path.phase_refresh", fake_refresh),
        patch("app.services.taxonomy.warm_path.phase_discover", fake_discover),
        patch("app.services.taxonomy.warm_path.phase_archive_empty_sub_domains", fake_archive_sub_domains),
        patch("app.services.taxonomy.warm_path.phase_audit", fake_audit),
    ):
        result = await execute_warm_path(engine, session_factory)

    assert call_order == [
        "reconcile",
        "split_emerge",
        "merge",
        "retire",
        "refresh",
        "discover",
        "archive_sub_domains",
        "audit",
    ], f"Phase order wrong: {call_order}"
    assert result.snapshot_id == "snap-test"


# ---------------------------------------------------------------------------
# _run_speculative_phase — commit and rollback paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_speculative_phase_accepted_commits(db, mock_embedding, mock_provider):
    """Speculative phase that passes Q gate should commit and return accepted=True."""
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # A5: the Q gate needs Q to be defined on both sides, which requires
    # ≥2 active non-structural clusters. Seed two pre-existing nodes so a
    # no-op phase produces real Q_before and Q_after numbers.
    for label in ("Pre-existing-A", "Pre-existing-B"):
        db.add(
            PromptCluster(
                label=label,
                state="active",
                domain="general",
                centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
                member_count=3,
                coherence=0.8,
                separation=0.8,
                color_hex="#a855f7",
            )
        )
    await db.commit()

    # Phase function that does nothing (Q_before == Q_after → non-regressive)
    async def no_op_phase(eng, session, split_protected_ids, dirty_ids=None):
        return PhaseResult(
            phase="test",
            q_before=0.5,
            q_after=0.5,
            accepted=False,  # will be overwritten by _run_speculative_phase
            ops_attempted=0,
            ops_accepted=0,
        )

    @asynccontextmanager
    async def session_factory():
        yield db

    result = await _run_speculative_phase(
        "test_phase", no_op_phase, engine, session_factory
    )
    assert result.accepted is True


@pytest.mark.asyncio
async def test_run_speculative_phase_rejected_rolls_back(db, mock_embedding, mock_provider):
    """Speculative phase that regresses Q must rollback and return accepted=False."""
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Add an active node so Q_before > 0
    node = PromptCluster(
        label="Pre-existing",
        state="active",
        domain="general",
        centroid_embedding=np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes(),
        member_count=3,
        coherence=0.8,
        separation=0.8,
        color_hex="#a855f7",
    )
    db.add(node)
    await db.commit()

    # Phase function that deletes the node and archives it (lowers Q)
    async def destructive_phase(eng, session, split_protected_ids, dirty_ids=None):
        # Archive the node to force Q_after < Q_before
        result = await session.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(PromptCluster).where(
                PromptCluster.label == "Pre-existing"
            )
        )
        n = result.scalar_one_or_none()
        if n:
            n.state = "archived"
            n.member_count = 0
        return PhaseResult(
            phase="destructive",
            q_before=0.7,
            q_after=0.0,  # massive regression
            accepted=False,
            ops_attempted=1,
            ops_accepted=1,
        )

    @asynccontextmanager
    async def session_factory():
        yield db

    result = await _run_speculative_phase(
        "destructive", destructive_phase, engine, session_factory
    )
    # Q regression should cause rollback → accepted=False
    assert result.accepted is False

    # Verify node was rolled back (still active)
    await db.refresh(node)
    assert node.state == "active"


@pytest.mark.asyncio
async def test_run_speculative_phase_restores_embedding_index_on_rollback(
    db, mock_embedding, mock_provider
):
    """Embedding index must be restored on rollback (snapshot/restore pattern)."""
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Seed index with one vector
    test_id = "test-node-id"
    test_vec = np.random.randn(EMBEDDING_DIM).astype(np.float32)
    await engine._embedding_index.upsert(test_id, test_vec)

    size_before = engine._embedding_index.size

    # Phase that removes the vector from index then causes Q regression
    async def index_mutating_phase(eng, session, split_protected_ids, dirty_ids=None):
        await eng._embedding_index.remove(test_id)
        return PhaseResult(
            phase="index_mutating",
            q_before=0.7,
            q_after=0.0,  # regression → triggers rollback
            accepted=False,
            ops_attempted=1,
            ops_accepted=1,
        )

    @asynccontextmanager
    async def session_factory():
        yield db

    with patch(
        "app.services.taxonomy.warm_path.is_non_regressive", return_value=False
    ):
        result = await _run_speculative_phase(
            "index_mutating", index_mutating_phase, engine, session_factory
        )

    # Index should be restored to its pre-phase state
    assert engine._embedding_index.size == size_before
    assert result.accepted is False


# ---------------------------------------------------------------------------
# _update_phase_rejection_counters — deadlock breaker
# ---------------------------------------------------------------------------


def test_update_phase_rejection_counters_increments_on_rejection():
    """Rejected phase increments the per-phase counter."""
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine()
    engine._phase_rejection_counters = {}
    engine._cold_path_needed = False

    rejected = PhaseResult("merge", 0.5, 0.5, False, 1, 0)
    _update_phase_rejection_counters(engine, [("merge", rejected)])

    assert engine._phase_rejection_counters.get("merge", 0) == 1
    assert not engine._cold_path_needed


def test_update_phase_rejection_counters_resets_on_acceptance():
    """Accepted phase resets its per-phase counter."""
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine()
    engine._phase_rejection_counters = {"merge": 3}
    engine._cold_path_needed = False

    accepted = PhaseResult("merge", 0.5, 0.6, True, 1, 1)
    _update_phase_rejection_counters(engine, [("merge", accepted)])

    assert engine._phase_rejection_counters["merge"] == 0


def test_update_phase_rejection_counters_triggers_cold_path_at_threshold():
    """Counter reaching DEADLOCK_BREAKER_THRESHOLD sets _cold_path_needed."""
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine()
    engine._phase_rejection_counters = {"split_emerge": DEADLOCK_BREAKER_THRESHOLD - 1}
    engine._cold_path_needed = False

    rejected = PhaseResult("split_emerge", 0.5, 0.5, False, 1, 0)
    deadlock_used, deadlock_phase = _update_phase_rejection_counters(
        engine, [("split_emerge", rejected)]
    )

    assert deadlock_used is True
    assert deadlock_phase == "split_emerge"
    assert engine._cold_path_needed is True


def test_update_phase_rejection_counters_no_deadlock_below_threshold():
    """Counter below threshold does not trigger deadlock."""
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine()
    engine._phase_rejection_counters = {}
    engine._cold_path_needed = False

    # Reject 4 times (threshold is 5)
    rejected = PhaseResult("retire", 0.5, 0.5, False, 1, 0)
    for _ in range(DEADLOCK_BREAKER_THRESHOLD - 1):
        deadlock_used, _ = _update_phase_rejection_counters(engine, [("retire", rejected)])
    assert not deadlock_used
    assert not engine._cold_path_needed


# ---------------------------------------------------------------------------
# execute_warm_path — integration (empty DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_warm_path_on_empty_db(db, mock_embedding, mock_provider):
    """execute_warm_path completes on an empty database without errors."""
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    @asynccontextmanager
    async def session_factory():
        yield db

    result = await execute_warm_path(engine, session_factory)

    assert result is not None
    assert result.snapshot_id is not None
    assert isinstance(result.deadlock_breaker_used, bool)
    assert result.operations_attempted >= 0
    assert result.operations_accepted <= result.operations_attempted
    # q_system backward-compat
    assert result.q_system == result.q_final


# ---------------------------------------------------------------------------
# execute_maintenance_phases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_maintenance_phases_calls_discover_and_audit(db, mock_embedding, mock_provider):
    """execute_maintenance_phases runs discover, archive, and audit in order."""
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    call_order: list[str] = []

    async def fake_discover(eng, session):
        call_order.append("discover")
        from app.services.taxonomy.warm_phases import DiscoverResult
        return DiscoverResult()

    async def fake_archive(eng, session):
        call_order.append("archive")
        return 0

    async def fake_audit(eng, session, phase_results, q_baseline):
        call_order.append("audit")
        from app.services.taxonomy.warm_phases import AuditResult
        return AuditResult(snapshot_id="maint-snap", q_final=0.5)

    @asynccontextmanager
    async def session_factory():
        yield db

    with (
        patch("app.services.taxonomy.warm_path.phase_discover", fake_discover),
        patch("app.services.taxonomy.warm_path.phase_archive_empty_sub_domains", fake_archive),
        patch("app.services.taxonomy.warm_path.phase_audit", fake_audit),
    ):
        result = await execute_maintenance_phases(engine, session_factory)

    assert call_order == ["discover", "archive", "audit"]
    assert result.snapshot_id == "maint-snap"
    assert result.operations_attempted == 0
    assert result.operations_accepted == 0


@pytest.mark.asyncio
async def test_execute_maintenance_phases_sets_retry_on_discover_failure(
    db, mock_embedding, mock_provider
):
    """When phase_discover raises, _maintenance_pending is set for retry."""
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    assert engine._maintenance_pending is False

    async def failing_discover(eng, session):
        raise Exception("database is locked")

    async def fake_archive(eng, session):
        return 0

    async def fake_audit(eng, session, phase_results, q_baseline):
        from app.services.taxonomy.warm_phases import AuditResult
        return AuditResult(snapshot_id="maint-fail", q_final=0.5)

    @asynccontextmanager
    async def session_factory():
        yield db

    with (
        patch("app.services.taxonomy.warm_path.phase_discover", failing_discover),
        patch("app.services.taxonomy.warm_path.phase_archive_empty_sub_domains", fake_archive),
        patch("app.services.taxonomy.warm_path.phase_audit", fake_audit),
    ):
        result = await execute_maintenance_phases(engine, session_factory)

    assert engine._maintenance_pending is True
    assert result.snapshot_id == "maint-fail"


@pytest.mark.asyncio
async def test_execute_maintenance_phases_clears_retry_on_success(
    db, mock_embedding, mock_provider
):
    """Successful discovery clears the _maintenance_pending flag."""
    from app.services.taxonomy.engine import TaxonomyEngine

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)
    engine._maintenance_pending = True

    async def ok_discover(eng, session):
        from app.services.taxonomy.warm_phases import DiscoverResult
        return DiscoverResult()

    async def fake_archive(eng, session):
        return 0

    async def fake_audit(eng, session, phase_results, q_baseline):
        from app.services.taxonomy.warm_phases import AuditResult
        return AuditResult(snapshot_id="maint-ok", q_final=0.5)

    @asynccontextmanager
    async def session_factory():
        yield db

    with (
        patch("app.services.taxonomy.warm_path.phase_discover", ok_discover),
        patch("app.services.taxonomy.warm_path.phase_archive_empty_sub_domains", fake_archive),
        patch("app.services.taxonomy.warm_path.phase_audit", fake_audit),
    ):
        result = await execute_maintenance_phases(engine, session_factory)

    assert engine._maintenance_pending is False
    assert result.snapshot_id == "maint-ok"


# ---------------------------------------------------------------------------
# Cycle 6 RED tests — warm-path phase-level routing through the WriteQueue
# ---------------------------------------------------------------------------
#
# Per spec § 3.6 + plan task 6.2: each warm-path phase function becomes ONE
# ``submit()`` to the single-writer queue. The 12 v0.4.12 commit sites in
# ``warm_path.py`` collapse into ~6 phase-level submits. The savepoint +
# autobegin pattern preserves the v0.4.12 split_failures persistence semantics
# WITHOUT a separate ``meta_db`` session. Phase 4.5 sub-steps preserve
# H-v4-3 isolation as 3 separate submits with try/except per sub-step.
#
# These tests pin the cycle-6 contract:
#
# * ``_run_speculative_phase`` accepts ``write_queue=`` and routes the
#   Q-gate decision (commit-or-rollback) INSIDE the submit callback.
# * Split-rejection metadata persists on Q-gate rollback even though the
#   v0.4.12 ``async with session_factory() as meta_db`` block is gone --
#   the savepoint pattern + autobegin lets the same writer session that
#   ran the speculative phase persist the post-rejection writes.
# * Phase 4.5 (global pattern lifecycle) sub-steps are 3 separate
#   ``submit()`` calls (promote, validate, retire); a transient failure
#   in any one sub-step does NOT prevent the others from running --
#   matches v0.4.12's per-sub-step ``begin_nested()`` SAVEPOINT semantics.
#
# Mirrors cycle 2/3/4/5 Option C dual-typed signature. Until cycle 7
# wires write_queue into engine.py callers, ``write_queue=None`` keeps
# the legacy ``session_factory`` path live.
# ---------------------------------------------------------------------------


class TestWarmPathPhaseRouting:
    """Cycle 6 RED → GREEN: warm-path phases route through ``WriteQueue``.

    Mirrors cycles 2-5 Option C dual-typed signatures
    (``session_factory`` legacy → ``write_queue`` canonical with
    ``write_queue is not None`` dispatch).
    """

    pytestmark = pytest.mark.usefixtures("reset_taxonomy_engine")

    @pytest.mark.asyncio
    async def test_speculative_phase_q_gate_decision_inside_submit_callback(
        self, write_queue_inmem, monkeypatch,
    ):
        """RED: passing ``write_queue=`` to ``_run_speculative_phase`` must
        produce at least one ``submit()`` call labelled
        ``warm_phase_<phase_name>``. Q-gate decision (commit-or-rollback)
        must happen INSIDE the submit callback per spec § 3.6.

        FAILS pre-GREEN with ``TypeError: _run_speculative_phase() got an
        unexpected keyword argument 'write_queue'`` -- the v0.4.12 signature
        is ``(phase_name, phase_fn, engine, session_factory, ...)`` only.

        After GREEN, the queue captures one submit per phase invocation;
        legacy path (when ``write_queue=None``) continues to operate on
        ``session_factory`` directly.
        """
        # Pre-stage two active clusters on the writer engine so Q computes
        # to a real number (≥2 active non-structural clusters required by
        # the Q-system). Mirrors cycle 4's prestaged-cluster pattern but
        # for the warm-path (writer-engine) case.
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.models import Base
        from app.services.taxonomy.engine import TaxonomyEngine
        from app.services.taxonomy.warm_path import _run_speculative_phase

        async with write_queue_inmem._writer_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        sf = async_sessionmaker(
            write_queue_inmem._writer_engine,
            class_=AsyncSession, expire_on_commit=False,
        )
        async with sf() as setup_db:
            for label in ("Cycle6-SeedA", "Cycle6-SeedB"):
                setup_db.add(
                    PromptCluster(
                        label=label,
                        state="active",
                        domain="general",
                        centroid_embedding=np.random.randn(EMBEDDING_DIM)
                        .astype(np.float32).tobytes(),
                        member_count=3,
                        coherence=0.8,
                        separation=0.8,
                        color_hex="#a855f7",
                    )
                )
            await setup_db.commit()

        # Build a minimal engine — same fixture the cycle 4 OPERATE tests use.
        from app.services.embedding_service import EmbeddingService
        engine = TaxonomyEngine(embedding_service=EmbeddingService())

        captured: list[str | None] = []
        original_submit = write_queue_inmem.submit

        async def _capture_submit(work, *, timeout=None, operation_label=None):
            captured.append(operation_label)
            return await original_submit(
                work, timeout=timeout, operation_label=operation_label,
            )

        monkeypatch.setattr(write_queue_inmem, "submit", _capture_submit)

        # No-op phase function — keeps Q_after == Q_before so the gate accepts.
        async def no_op_phase(eng, session, split_protected_ids, dirty_ids=None):
            return PhaseResult(
                phase="cycle6_test",
                q_before=0.5,
                q_after=0.5,
                accepted=False,  # _run_speculative_phase overwrites this
                ops_attempted=0,
                ops_accepted=0,
            )

        # Pre-GREEN: TypeError because ``_run_speculative_phase`` does not
        # accept ``write_queue=``. Post-GREEN: routes via the queue worker.
        await _run_speculative_phase(  # type: ignore[call-arg]
            "split_emerge", no_op_phase, engine,
            session_factory=None,  # canonical: queue replaces session_factory
            write_queue=write_queue_inmem,
        )

        assert any(
            (label or "").startswith("warm_phase_") for label in captured
        ), (
            "expected at least one submit() with operation_label "
            f"prefix='warm_phase_'; got {captured!r}"
        )

    @pytest.mark.asyncio
    async def test_split_failures_persist_when_speculative_phase_rolls_back(
        self, write_queue_inmem,
    ):
        """RED: when speculative phase rolls back (Q regression), the
        split_failures counter on attempted clusters must still increment.

        Pin spec § 3.6: split_failures metadata persists on Q-rejection
        WITHOUT requiring a separate ``meta_db`` session — the savepoint
        + autobegin pattern lets the same writer session persist the
        post-rejection writes after ``savepoint.rollback()``.

        FAILS pre-GREEN: ``write_queue=`` is rejected (TypeError) before
        the phase even runs. Post-GREEN: the counter increments because
        the queue callback runs split_failures persistence inside the
        same writer session that performed the speculative rollback.
        """
        from app.models import Base
        from app.services.embedding_service import EmbeddingService
        from app.services.taxonomy.cluster_meta import read_meta
        from app.services.taxonomy.engine import TaxonomyEngine
        from app.services.taxonomy.warm_path import _run_speculative_phase

        async with write_queue_inmem._writer_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        sf = async_sessionmaker(
            write_queue_inmem._writer_engine,
            class_=AsyncSession, expire_on_commit=False,
        )

        # Pre-create one cluster that will be the split-attempt target.
        target_cid = "test-split-fail-cluster-cycle6"
        async with sf() as setup_db:
            setup_db.add(
                PromptCluster(
                    id=target_cid,
                    label="cycle6-split-target",
                    state="active",
                    domain="general",
                    centroid_embedding=np.random.randn(EMBEDDING_DIM)
                    .astype(np.float32).tobytes(),
                    member_count=10,
                    coherence=0.8,
                    separation=0.8,
                    color_hex="#a855f7",
                    cluster_metadata={"split_failures": 0},
                )
            )
            # Need a 2nd active cluster so Q is defined.
            setup_db.add(
                PromptCluster(
                    label="cycle6-secondary",
                    state="active",
                    domain="general",
                    centroid_embedding=np.random.randn(EMBEDDING_DIM)
                    .astype(np.float32).tobytes(),
                    member_count=3,
                    coherence=0.8,
                    separation=0.8,
                    color_hex="#a855f7",
                )
            )
            await setup_db.commit()

        engine = TaxonomyEngine(embedding_service=EmbeddingService())

        async def force_reject_phase(eng, session, split_protected_ids, dirty_ids=None):
            """Phase function that returns split_attempted_ids for the
            target cluster and a Q regression that triggers rollback."""
            return PhaseResult(
                phase="split_emerge",
                q_before=0.7,
                q_after=0.0,  # massive regression -> Q-gate rejects
                accepted=False,
                ops_attempted=1,
                ops_accepted=1,
                split_attempted_ids=[target_cid],
                split_content_hashes={target_cid: "deadbeef0123abcd"},
            )

        # Force is_non_regressive to return False so we hit the rollback path
        # regardless of whether Q_before/Q_after computation drifts.
        with patch(
            "app.services.taxonomy.warm_path.is_non_regressive", return_value=False,
        ):
            result = await _run_speculative_phase(  # type: ignore[call-arg]
                "split_emerge", force_reject_phase, engine,
                session_factory=None,
                write_queue=write_queue_inmem,
            )

        assert result.accepted is False, "Q-gate must reject"

        # Verify split_failures counter incremented (i.e., metadata persisted
        # after the speculative rollback).
        async with sf() as verify_db:
            cluster = await verify_db.get(PromptCluster, target_cid)
            assert cluster is not None
            meta = read_meta(cluster.cluster_metadata)
            assert meta["split_failures"] >= 1, (
                "split_failures must persist on Q-rejection (savepoint + "
                "autobegin). Got cluster_metadata="
                f"{cluster.cluster_metadata!r}"
            )

    @pytest.mark.asyncio
    async def test_phase_4_5_sub_step_failure_does_not_poison_other_steps(
        self, write_queue_inmem, monkeypatch,
    ):
        """RED: each Phase 4.5 sub-step is its own submit; a transient
        failure in one sub-step does NOT prevent the others from running.

        Pin spec § 3.6 H-v4-3. v0.4.12 used ``db.begin_nested()`` SAVEPOINTs
        per sub-step inside ``run_global_pattern_phase``; v0.4.13 cycle 6
        moves the per-sub-step boundary to ``submit()`` calls so the queue
        owns the transaction lifecycle.

        FAILS pre-GREEN: the new ``run_phase_4_5(write_queue)`` helper
        does not yet exist. Post-GREEN: the helper invokes 3 separate
        submits (promote / validate / retire) with try/except around each.
        """
        from app.models import Base
        from app.services.taxonomy import warm_path

        async with write_queue_inmem._writer_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        called: list[str] = []

        async def _ok_promote(db):
            called.append("promote")
            return (0, 0)  # mirrors _discover_promotion_candidates signature

        async def _failing_validate(db):
            called.append("validate")
            raise RuntimeError("simulated validate failure")

        async def _ok_retire(db):
            called.append("retire")
            return 0  # mirrors _enforce_retention_cap signature

        # Patch the underlying sub-steps in global_patterns module so the
        # cycle 6 helper invokes them via 3 separate submits.
        monkeypatch.setattr(
            "app.services.taxonomy.global_patterns._discover_promotion_candidates",
            _ok_promote,
        )
        monkeypatch.setattr(
            "app.services.taxonomy.global_patterns._validate_existing_patterns",
            _failing_validate,
        )
        monkeypatch.setattr(
            "app.services.taxonomy.global_patterns._enforce_retention_cap",
            _ok_retire,
        )

        # Pre-GREEN: AttributeError because ``run_phase_4_5`` doesn't exist.
        await warm_path.run_phase_4_5(write_queue_inmem)  # type: ignore[attr-defined]

        # All three sub-steps were attempted despite middle one raising.
        assert called == ["promote", "validate", "retire"], (
            "Phase 4.5 sub-step isolation broken: expected all three "
            f"sub-steps to run; got {called}"
        )


# ---------------------------------------------------------------------------
# Cycle 6 OPERATE — warm-path concurrency + real Q-rejection + sub-step isolation
# ---------------------------------------------------------------------------
#
# Per ``feedback_tdd_protocol.md`` Phase 5: dynamic verification under realistic
# concurrent load. The warm-path is the most complex single migration in
# v0.4.13 — INTEGRATE flagged 3 OPERATE concerns the GREEN/REFACTOR contracts
# cannot prove because they relied on synthesized failure modes:
#
#   1. Concurrent stress — does a long-running warm cycle (split + merge +
#      retire firing through the queue) block other concurrent writers?
#   2. Real Q regression — does the savepoint+autobegin pattern persist
#      split_failures when an honest Q drop (not a hard-coded 0.0) triggers
#      rollback?
#   3. Real sub-step failure — does a SQLAlchemy-shaped exception in
#      ``_validate_existing_patterns`` poison ``_enforce_retention_cap`` via
#      shared session state, or is the per-submit boundary truly isolating?
#
# OPERATE pins those contracts under live load.
# ---------------------------------------------------------------------------


class TestWarmPathOperate:
    """OPERATE phase: warm-path under realistic concurrent load + real Q
    rejection + real sub-step failure isolation.

    Mirrors the cycle 4 ``TestPersistAndPropagateOperate`` structure.
    Tests #1-2 use ``writer_engine_file`` so real WAL semantics apply (the
    failure mode the queue exists to eliminate — ``database is locked`` —
    only manifests against on-disk SQLite). Tests #3-5 use
    ``writer_engine_inmem`` for logic-only assertions.

    The class-level ``reset_taxonomy_engine`` fixture ensures every test
    starts with a fresh ``TaxonomyEngine`` singleton + dirty-set so
    accumulated state from prior tests doesn't bleed into assert paths.
    """

    pytestmark = pytest.mark.usefixtures("reset_taxonomy_engine")

    # ------------------------------------------------------------------
    # Test #1: warm-path lifecycle phases as 6+ submits, no DB locks
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_warm_phase_n_concurrent_submits_no_database_locked(
        self, writer_engine_file, caplog,
    ):
        """A full warm-path lifecycle pass routes every phase through ONE
        ``submit()`` call; sustained queue traffic produces zero
        ``database is locked`` records and queue depth stays bounded.

        Pre-stages 5 candidate-state clusters via ``create_prestaged_cluster``
        so the lifecycle phases (reconcile / 0.5 evaluate / split_emerge /
        merge / retire / 4-refresh / 4.25 / 4.75 / 4.76 / maintenance) all
        actually execute. Asserts:

        * ≥6 unique ``warm_phase_*`` operation labels captured (one per
          phase function — collapses the 12 v0.4.12 commit sites).
        * Zero ``database is locked`` records at WARNING+ level.
        * Queue depth never exceeded a reasonable cap during the run
          (sampled at ~10ms intervals; cap=50 since each phase serializes).
        * Warm cycle wall-clock <60s on file-mode WAL (real SQLite latency).

        Pin INTEGRATE concern #1: warm-path phase commits do not block
        the queue worker; depth never spikes pathologically.
        """
        import asyncio as _asyncio
        import contextlib
        import logging as _logging
        import time as _time

        from app.models import Base
        from app.services.embedding_service import EmbeddingService
        from app.services.taxonomy.engine import TaxonomyEngine
        from app.services.taxonomy.warm_path import execute_warm_path
        from app.services.write_queue import WriteQueue
        from tests._write_queue_helpers import create_prestaged_cluster

        # Materialize schema on the file-mode engine (file-mode does NOT
        # auto-create, only writer_engine_inmem does).
        async with writer_engine_file.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Pre-stage 5 candidate clusters so warm-path actually has work
        # to do. ``create_prestaged_cluster`` is idempotent on caller-
        # supplied IDs so tests can FK against them.
        for i in range(5):
            await create_prestaged_cluster(
                writer_engine_file,
                label=f"warm-op-test-cluster-{i}",
                state="active",
                domain="general",
            )

        queue = WriteQueue(writer_engine_file, max_depth=64)
        await queue.start()

        # Capture every submit() with its operation_label so we can verify
        # the warm-path emits one submit per phase.
        captured_labels: list[str | None] = []
        original_submit = queue.submit

        async def _capture_submit(work, *, timeout=None, operation_label=None):
            captured_labels.append(operation_label)
            return await original_submit(
                work, timeout=timeout, operation_label=operation_label,
            )
        queue.submit = _capture_submit  # type: ignore[method-assign]

        # Sample queue depth in the background.
        observed_depths: list[int] = []
        depth_done = _asyncio.Event()

        async def _sample_depth() -> None:
            while not depth_done.is_set():
                observed_depths.append(queue.queue_depth)
                try:
                    await _asyncio.wait_for(depth_done.wait(), timeout=0.01)
                except _asyncio.TimeoutError:
                    pass
        sampler_task = _asyncio.create_task(_sample_depth())

        engine = TaxonomyEngine(embedding_service=EmbeddingService())

        try:
            t0 = _time.monotonic()
            with caplog.at_level(_logging.WARNING):
                await execute_warm_path(engine, write_queue=queue)
            elapsed = _time.monotonic() - t0
            depth_done.set()
            with contextlib.suppress(_asyncio.CancelledError):
                await sampler_task

            # Verify warm-phase submits collapsed into phase-level granularity.
            warm_phase_labels = [
                lbl for lbl in captured_labels
                if (lbl or "").startswith("warm_phase_")
            ]
            unique_warm_phases = {lbl for lbl in warm_phase_labels if lbl}
            # Per spec § 3.6: 6+ phase-level submits (reconcile, 0.5, split,
            # merge, retire, refresh, 4.25, 4.75, 4.76, vocab_refresh, 5,
            # 5.5, 6, snapshot_prune). Conservative floor of 6.
            assert len(unique_warm_phases) >= 6, (
                "expected ≥6 unique warm_phase_* operation labels "
                f"(one per phase function); got {len(unique_warm_phases)} "
                f"unique labels: {sorted(unique_warm_phases)}"
            )

            # O2: zero ``database is locked`` records anywhere.
            locked_records = [
                r for r in caplog.records
                if "database is locked" in r.getMessage().lower()
            ]
            assert locked_records == [], (
                f"got {len(locked_records)} 'database is locked' records "
                f"under warm-path stress: "
                f"{[r.getMessage() for r in locked_records[:3]]}"
            )

            # Queue depth bounded — phase submits serialize so depth
            # should never spike pathologically. Cap=50 is generous
            # given max_depth=64 and per-phase serialization.
            max_seen_depth = max(observed_depths) if observed_depths else 0
            assert max_seen_depth < 50, (
                f"queue depth peaked at {max_seen_depth}, exceeded sane "
                f"upper bound for serialized warm-path execution"
            )

            # Wall-clock budget per OPERATE acceptance: <60s combined.
            assert elapsed < 60.0, (
                f"warm-path stress run took {elapsed:.1f}s, > 60s budget"
            )
        finally:
            depth_done.set()
            if not sampler_task.done():
                with contextlib.suppress(_asyncio.CancelledError):
                    await sampler_task
            await queue.stop(drain_timeout=5.0)

    # ------------------------------------------------------------------
    # Test #2: warm-path co-existing with concurrent bulk_persist hot writers
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_warm_phase_concurrent_with_bulk_persist(
        self, writer_engine_file, caplog,
    ):
        """A warm-path lifecycle cycle running concurrently with N=5
        ``bulk_persist`` calls completes WITHOUT deadlock and WITHOUT
        ``database is locked`` records.

        This is the cycle 6 production-stress equivalent — warm-path
        coexisting with hot-path probe-style writers. The queue's
        single-writer guarantee is the only defense; if the savepoint
        boundary leaked or the warm-path held the connection, hot-path
        bulk_persist callers would surface 'database is locked'.

        Asserts:

        * All 5 ``bulk_persist`` calls return their pending count (3 each).
        * 15 ``Optimization`` rows visible via direct SELECT.
        * Warm-path completes with a valid ``WarmPathResult``.
        * Zero ``database is locked`` records.
        * Wall-clock budget <60s.

        Pin INTEGRATE concern #1 (combined hot-and-warm load).
        """
        import asyncio as _asyncio
        import logging as _logging
        import time as _time

        from sqlalchemy import text as _sa_text

        from app.models import Base
        from app.services import batch_persistence
        from app.services.embedding_service import EmbeddingService
        from app.services.taxonomy.engine import TaxonomyEngine
        from app.services.taxonomy.warm_path import (
            WarmPathResult,
            execute_warm_path,
        )
        from app.services.write_queue import WriteQueue
        from tests._write_queue_helpers import (
            _make_passing_pending,
            create_prestaged_cluster,
        )

        async with writer_engine_file.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Pre-stage clusters so warm-path has lifecycle work.
        for i in range(3):
            await create_prestaged_cluster(
                writer_engine_file,
                label=f"warm-stress-{i}",
                state="active",
                domain="general",
            )

        queue = WriteQueue(writer_engine_file, max_depth=128)
        await queue.start()

        engine = TaxonomyEngine(embedding_service=EmbeddingService())

        # Build 5 bulk_persist batches with distinct batch_ids, 3 rows each.
        batches = [
            [_make_passing_pending(batch_id=f"warm-co-{i}") for _ in range(3)]
            for i in range(5)
        ]

        try:
            t0 = _time.monotonic()
            with caplog.at_level(_logging.WARNING):
                # Warm-path runs concurrently with hot-path writers — the
                # queue serializes them but neither caller blocks the other.
                results = await _asyncio.gather(
                    execute_warm_path(engine, write_queue=queue),
                    *[
                        batch_persistence.bulk_persist(
                            batches[i], queue, batch_id=f"warm-co-{i}",
                        )
                        for i in range(5)
                    ],
                )
            elapsed = _time.monotonic() - t0

            warm_result, *bulk_results = results

            # Warm-path returned a valid WarmPathResult.
            assert isinstance(warm_result, WarmPathResult), (
                f"expected WarmPathResult, got {type(warm_result).__name__}"
            )
            assert warm_result.snapshot_id is not None

            # Each bulk_persist inserted 3 rows.
            assert bulk_results == [3, 3, 3, 3, 3], (
                f"expected each bulk_persist to insert 3 rows; "
                f"got {bulk_results}"
            )

            # O1: SELECT to verify 15 hot-path rows landed.
            async with writer_engine_file.connect() as conn:
                count_q = await conn.execute(_sa_text(
                    "SELECT COUNT(*) FROM optimizations "
                    "WHERE json_extract(context_sources, '$.batch_id') "
                    "LIKE 'warm-co-%'"
                ))
                row = count_q.first()
                row_count = int(row[0]) if row else 0
            assert row_count == 15, (
                f"expected 15 Optimization rows from concurrent "
                f"bulk_persist + warm-path, got {row_count}"
            )

            # O2: zero 'database is locked' under combined load.
            locked_records = [
                r for r in caplog.records
                if "database is locked" in r.getMessage().lower()
            ]
            assert locked_records == [], (
                f"got {len(locked_records)} 'database is locked' records "
                f"under warm + hot combined load: "
                f"{[r.getMessage() for r in locked_records[:3]]}"
            )

            # Wall-clock budget <60s combined.
            assert elapsed < 60.0, (
                f"combined warm + hot stress took {elapsed:.1f}s, > 60s budget"
            )
        finally:
            await queue.stop(drain_timeout=5.0)

    # ------------------------------------------------------------------
    # Test #3: real autoflush-shaped failure in middle Phase 4.5 sub-step
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_warm_phase_4_5_sub_step_real_failure_isolation(
        self, write_queue_inmem, monkeypatch,
    ):
        """A SQLAlchemy ``OperationalError`` raised by the middle Phase 4.5
        sub-step does NOT prevent the surrounding sub-steps from running
        and committing.

        Mirrors the GREEN test ``test_phase_4_5_sub_step_failure_does_not_
        poison_other_steps`` but uses a realistic exception class
        (``sqlalchemy.exc.OperationalError``) to exercise the real
        production failure surface. v0.4.12 used ``begin_nested()``
        SAVEPOINTs per sub-step; cycle 6 promotes each sub-step to its
        own ``submit()`` call. The per-submit boundary is the only thing
        that prevents the autoflush integrity error in
        ``_validate_existing_patterns`` from poisoning the surrounding
        transactional state for ``_enforce_retention_cap``.

        Asserts:

        * All three sub-steps fire (call order preserved).
        * Sub-steps 1 and 3 commit their work (verified by counter return
          values surfacing in ``stats``).
        * Sub-step 2's failure is contained — no propagated exception.
        * The aggregated ``stats`` dict reflects the partial-success
          semantics: promote/retire counters set, validate counters at 0.

        Pin INTEGRATE concern #3 with a real SQLAlchemy exception.
        """
        from sqlalchemy.exc import OperationalError

        from app.models import Base
        from app.services.taxonomy import warm_path

        async with write_queue_inmem._writer_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        called: list[str] = []

        async def _ok_promote(db):
            called.append("promote")
            # Mirrors _discover_promotion_candidates real return shape.
            return (2, 1)  # promoted=2, updated=1

        async def _failing_validate(db):
            """Raise a SQLAlchemy OperationalError to simulate the real
            failure mode (e.g. database lock, autoflush integrity error
            in production). This exercises the same production code path
            that the v0.4.12 ``begin_nested()`` SAVEPOINT was designed to
            isolate.
            """
            called.append("validate")
            # statement, params, orig — production-shaped args.
            raise OperationalError(
                "SELECT * FROM global_patterns",
                {},
                Exception("database is locked"),
            )

        async def _ok_retire(db):
            called.append("retire")
            return 4  # evicted=4

        # Patch the underlying sub-step functions in global_patterns module.
        monkeypatch.setattr(
            "app.services.taxonomy.global_patterns._discover_promotion_candidates",
            _ok_promote,
        )
        monkeypatch.setattr(
            "app.services.taxonomy.global_patterns._validate_existing_patterns",
            _failing_validate,
        )
        monkeypatch.setattr(
            "app.services.taxonomy.global_patterns._enforce_retention_cap",
            _ok_retire,
        )

        # run_phase_4_5 must not propagate the OperationalError; the failed
        # sub-step degrades gracefully while the others commit normally.
        stats = await warm_path.run_phase_4_5(write_queue_inmem)

        # Call ordering preserved despite middle failure.
        assert called == ["promote", "validate", "retire"], (
            "Real-failure isolation broken: expected all three sub-steps "
            f"to fire in order despite OperationalError in step 2; "
            f"got {called}"
        )

        # Sub-step 1 and 3 set their counters from successful return values.
        assert stats["promoted"] == 2, (
            f"sub-step 1 (promote) counter not preserved across step 2 "
            f"failure; got promoted={stats['promoted']}"
        )
        assert stats["updated"] == 1, (
            f"sub-step 1 (promote) updated counter mismatch: {stats['updated']}"
        )
        assert stats["evicted"] == 4, (
            f"sub-step 3 (retire) counter not preserved across step 2 "
            f"failure; got evicted={stats['evicted']}"
        )

        # Sub-step 2 contributes 0 to its counters because the exception
        # short-circuited the assignment block (mirrors v0.4.12 SAVEPOINT
        # rollback semantics — its writes are gone, others survive).
        assert stats["demoted"] == 0
        assert stats["re_promoted"] == 0
        assert stats["retired"] == 0

    # ------------------------------------------------------------------
    # Test #4: real Q drop via destructive archival (no monkeypatch)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_speculative_phase_rejection_via_real_q_drop(
        self, write_queue_inmem,
    ):
        """A phase that genuinely degrades the active set causes Q to
        transition from defined → None, triggering rollback via the
        REAL ``is_non_regressive`` gate (no monkeypatch).

        Setup: 2 active clusters → Q is defined.
        Phase: archives one of them → 1 active cluster → Q is None.
        Gate: ``is_non_regressive(defined, None)`` returns False per the
        A5 contract: "defined → None is a regression."

        Verifies the savepoint+autobegin pattern fires in production
        conditions: the speculative archive write is rolled back AND
        ``split_failures`` increments via the same writer session that
        executed the rolled-back work.

        Pin INTEGRATE concern #2 (real Q drop, not synthesized).
        """
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.models import Base
        from app.services.embedding_service import EmbeddingService
        from app.services.taxonomy.cluster_meta import read_meta
        from app.services.taxonomy.engine import TaxonomyEngine
        from app.services.taxonomy.warm_path import _run_speculative_phase

        async with write_queue_inmem._writer_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        sf = async_sessionmaker(
            write_queue_inmem._writer_engine,
            class_=AsyncSession, expire_on_commit=False,
        )

        # Pre-create exactly 2 active clusters so Q_before is defined
        # (≥2 active non-structural clusters required by the Q-system).
        target_cid = "real-q-drop-cluster"
        async with sf() as setup_db:
            setup_db.add(
                PromptCluster(
                    id=target_cid,
                    label="will-archive-this",
                    state="active",
                    domain="general",
                    centroid_embedding=np.random.randn(EMBEDDING_DIM)
                    .astype(np.float32).tobytes(),
                    member_count=10,
                    coherence=0.8,
                    separation=0.8,
                    color_hex="#a855f7",
                    cluster_metadata={"split_failures": 0},
                )
            )
            setup_db.add(
                PromptCluster(
                    label="survivor",
                    state="active",
                    domain="general",
                    centroid_embedding=np.random.randn(EMBEDDING_DIM)
                    .astype(np.float32).tobytes(),
                    member_count=5,
                    coherence=0.8,
                    separation=0.8,
                    color_hex="#a855f7",
                )
            )
            await setup_db.commit()

        engine = TaxonomyEngine(embedding_service=EmbeddingService())

        # Phase that archives the target — producing a real Q transition
        # from defined (2 active) to None (1 active). No q_before/q_after
        # synthesis: ``_execute_phase_in_session`` recomputes them from
        # nodes_before/nodes_after, and the REAL ``is_non_regressive``
        # rejects defined→None per the A5 contract.
        async def real_destructive_phase(eng, session, split_protected_ids, dirty_ids=None):
            from sqlalchemy import select as _select

            # Truly archive the cluster — this is what causes Q to drop
            # because _load_active_nodes(exclude_candidates=True) excludes
            # archived clusters from the post-phase Q computation.
            stmt = _select(PromptCluster).where(
                PromptCluster.id == target_cid,
            )
            result = await session.execute(stmt)
            cluster = result.scalar_one_or_none()
            if cluster:
                cluster.state = "archived"
                cluster.member_count = 0

            return PhaseResult(
                phase="split_emerge",
                # Initial values placeholder — ``_execute_phase_in_session``
                # overwrites these from nodes_before/nodes_after AFTER this
                # function returns. The Q gate sees the REAL transition.
                q_before=0.0,
                q_after=0.0,
                accepted=False,
                ops_attempted=1,
                ops_accepted=1,
                split_attempted_ids=[target_cid],
                split_content_hashes={target_cid: "feedface" * 4},
            )

        # NO ``patch(is_non_regressive=False)``. The real gate decides.
        result = await _run_speculative_phase(
            "split_emerge", real_destructive_phase, engine,
            session_factory=None,
            write_queue=write_queue_inmem,
        )

        # Real Q gate must have rejected: defined → None is regressive.
        assert result.accepted is False, (
            "Real is_non_regressive gate must reject the defined→None "
            "transition (the cluster was archived, leaving 1 active "
            f"cluster). Got accepted={result.accepted}"
        )

        # Verify the speculative archive was ROLLED BACK — cluster is
        # still active because savepoint.rollback() reverted the write.
        async with sf() as verify_db:
            cluster = await verify_db.get(PromptCluster, target_cid)
            assert cluster is not None
            assert cluster.state == "active", (
                "Speculative archive must be rolled back on Q-rejection; "
                f"cluster state stayed at {cluster.state!r} (savepoint "
                "rollback failed)"
            )

            # Verify split_failures persisted via savepoint+autobegin —
            # this is the cycle 6 contract: the post-rejection metadata
            # write rides on the SAME writer session that ran the
            # rolled-back speculative work, no separate ``meta_db``.
            meta = read_meta(cluster.cluster_metadata)
            assert meta["split_failures"] >= 1, (
                "split_failures must increment on REAL Q-gate rejection "
                "(savepoint+autobegin pattern). Got cluster_metadata="
                f"{cluster.cluster_metadata!r}"
            )

    # ------------------------------------------------------------------
    # Test #5: event emission ordering — events fire after submit() resolves
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_warm_phase_event_emission_after_queue_resolves(
        self, write_queue_inmem, monkeypatch,
    ):
        """Decision events emitted from inside a warm-path phase callback
        only become visible AFTER the phase's ``submit()`` returns —
        verifying the failure semantics inherited from cycles 2-5: events
        are committed observability, not phantom emissions.

        Setup: subscribe an event collector to ``event_bus._subscribers``
        BEFORE the phase fires. Run a no-op speculative phase. Assert:

        * The submit completes successfully (Q gate accepts no-op).
        * No ``warm`` decision events were buffered before submit returned
          (cannot easily verify this without timing instrumentation, so
          instead assert the "after" property: events are present in the
          queue after the await completes).
        * Run the same phase twice with a forced rejection in between;
          the rejected phase emits a ``rejected`` decision event; the
          accepted phase emits an ``accepted`` event. Both happen AFTER
          the submit boundary, so both are observable from outside.

        Pin failure semantics: events represent post-submit committed
        state, never pre-submit phantom state.
        """
        import asyncio as _asyncio

        from app.models import Base
        from app.services.embedding_service import EmbeddingService
        from app.services.taxonomy.engine import TaxonomyEngine
        from app.services.taxonomy.event_logger import (
            TaxonomyEventLogger,
            set_event_logger,
        )
        from app.services.taxonomy.warm_path import _run_speculative_phase

        async with write_queue_inmem._writer_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        sf = async_sessionmaker(
            write_queue_inmem._writer_engine,
            class_=AsyncSession, expire_on_commit=False,
        )
        async with sf() as setup_db:
            for label in ("EvtA", "EvtB"):
                setup_db.add(
                    PromptCluster(
                        label=label,
                        state="active",
                        domain="general",
                        centroid_embedding=np.random.randn(EMBEDDING_DIM)
                        .astype(np.float32).tobytes(),
                        member_count=3,
                        coherence=0.8,
                        separation=0.8,
                        color_hex="#a855f7",
                    )
                )
            await setup_db.commit()

        # Initialize event logger with an in-memory ring buffer per test.
        # set_event_logger() takes a TaxonomyEventLogger instance.
        original_logger: TaxonomyEventLogger | None
        try:
            from app.services.taxonomy.event_logger import get_event_logger
            original_logger = get_event_logger()
        except RuntimeError:
            original_logger = None

        captured_events: list[dict] = []

        class _CapturingLogger(TaxonomyEventLogger):
            def log_decision(self, **kwargs):
                captured_events.append(kwargs)

        # tmp directory needed for TaxonomyEventLogger init.
        import tempfile
        with tempfile.TemporaryDirectory() as _tmp:
            from pathlib import Path

            logger_inst = _CapturingLogger(
                events_dir=Path(_tmp),
                publish_to_bus=False,
                buffer_size=500,
            )
            set_event_logger(logger_inst)

            engine = TaxonomyEngine(embedding_service=EmbeddingService())

            # Track when submit() resolves vs when events appear, by
            # snapshotting the captured-events length at key points.
            events_before_submit = len(captured_events)

            # No-op phase: ops_attempted=0 → no decision event per the
            # idle-noise filter in _execute_phase_in_session. Use an
            # ops>0 phase so we get a real ``accepted`` event.
            async def ops_phase(eng, session, split_protected_ids, dirty_ids=None):
                return PhaseResult(
                    phase="merge",
                    q_before=0.5,
                    q_after=0.5,
                    accepted=False,  # _run_speculative_phase overwrites
                    ops_attempted=1,
                    ops_accepted=1,
                )

            # Submit completes. Per the queue contract, events appear in
            # the captured-events list ONLY after the await resolves.
            await _asyncio.wait_for(
                _run_speculative_phase(
                    "merge", ops_phase, engine,
                    session_factory=None,
                    write_queue=write_queue_inmem,
                ),
                timeout=10.0,
            )

            # Events are now visible (post-submit observability boundary).
            events_after_submit = len(captured_events)
            assert events_after_submit > events_before_submit, (
                "expected ≥1 decision event AFTER submit() returns; "
                f"events_before={events_before_submit} "
                f"events_after={events_after_submit}"
            )

            # Among captured events, find an accepted phase event for "merge".
            accepted_events = [
                e for e in captured_events
                if e.get("path") == "warm"
                and e.get("op") == "phase"
                and e.get("decision") == "accepted"
                and e.get("context", {}).get("phase_name") == "merge"
            ]
            assert len(accepted_events) >= 1, (
                "expected at least one 'accepted' decision event for the "
                f"merge phase (ops>0); got events={[e for e in captured_events]}"
            )

            # Now drive a second phase with FORCED rejection — verify
            # rejected events also fire post-submit (failure semantics
            # mirror accept path: events represent committed observability).
            captured_events.clear()

            async def reject_phase(eng, session, split_protected_ids, dirty_ids=None):
                return PhaseResult(
                    phase="merge",
                    q_before=0.7,
                    q_after=0.0,  # massive regression
                    accepted=False,
                    ops_attempted=1,
                    ops_accepted=1,
                )

            with patch(
                "app.services.taxonomy.warm_path.is_non_regressive",
                return_value=False,
            ):
                rejected_result = await _run_speculative_phase(
                    "merge", reject_phase, engine,
                    session_factory=None,
                    write_queue=write_queue_inmem,
                )
            assert rejected_result.accepted is False

            rejected_events = [
                e for e in captured_events
                if e.get("path") == "warm"
                and e.get("op") == "phase"
                and e.get("decision") == "rejected"
                and e.get("context", {}).get("phase_name") == "merge"
            ]
            assert len(rejected_events) >= 1, (
                "expected at least one 'rejected' decision event for the "
                "rejected merge phase (post-submit observability); "
                f"got events={captured_events}"
            )

            # ----------------------------------------------------------
            # Failure semantics from cycles 2-5: when submit() RAISES
            # (e.g. WriteQueueOverloadedError, WriteQueueDeadError), the
            # exception propagates to the caller and NO decision events
            # fire for that phase. The queue contract: events represent
            # post-COMMIT durable state, not pre-submit phantom state.
            # ----------------------------------------------------------
            captured_events.clear()

            from app.services.write_queue import WriteQueueOverloadedError

            async def _raising_submit(work, *, timeout=None, operation_label=None):
                # Simulate the queue rejecting the submit synchronously
                # (e.g. queue at max_depth → overload).
                raise WriteQueueOverloadedError(
                    "synthetic overload for failure-semantics test"
                )

            monkeypatch.setattr(
                write_queue_inmem, "submit", _raising_submit,
            )

            async def unused_phase(eng, session, split_protected_ids, dirty_ids=None):
                return PhaseResult(
                    phase="merge",
                    q_before=0.5,
                    q_after=0.5,
                    accepted=False,
                    ops_attempted=1,
                    ops_accepted=1,
                )

            with pytest.raises(WriteQueueOverloadedError):
                await _run_speculative_phase(
                    "merge", unused_phase, engine,
                    session_factory=None,
                    write_queue=write_queue_inmem,
                )

            # NO decision events fired for the failed submit — the queue
            # never reached the callback so the inner ``log_decision``
            # call site never executed.
            phase_events_after_overload = [
                e for e in captured_events
                if e.get("path") == "warm" and e.get("op") == "phase"
            ]
            assert phase_events_after_overload == [], (
                "Failure semantics violated: phase decision events fired "
                "even though submit() raised WriteQueueOverloadedError. "
                "Events represent committed state — a failed submit must "
                f"emit ZERO phase events. Got: {phase_events_after_overload}"
            )

            # Restore prior logger state to avoid leaking captured class
            # to subsequent tests.
            if original_logger is not None:
                set_event_logger(original_logger)
