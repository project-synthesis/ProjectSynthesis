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
        from app.models import Base
        from app.services.taxonomy.engine import TaxonomyEngine
        from app.services.taxonomy.warm_path import _run_speculative_phase

        # Pre-stage two active clusters on the writer engine so Q computes
        # to a real number (≥2 active non-structural clusters required by
        # the Q-system). Mirrors cycle 4's prestaged-cluster pattern but
        # for the warm-path (writer-engine) case.
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
