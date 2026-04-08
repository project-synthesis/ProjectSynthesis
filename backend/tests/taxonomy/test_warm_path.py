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
