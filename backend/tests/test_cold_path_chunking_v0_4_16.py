"""RED-phase tests for v0.4.16 P1a Cycle 1 — cold-path commit chunking core.

Spec: docs/specs/v0.4.16-cold-path-chunking-2026-05-04.md (v7 APPROVED)
Plan: docs/plans/v0.4.16-p1a-cold-path-chunking-2026-05-04.md (v2 APPROVED) Task 1.1

13 tests. 11 must FAIL pre-Cycle-1 (with the documented signal). #8 + #9 PASS as
regression guards. GREEN dispatch (Task 1.2) flips all 13 to passing.

Inline helpers (NO new @pytest.fixture defs per RED constraint):
  * _make_mock_embedding(): MagicMock spec=EmbeddingService — deterministic
    hash-based stand-in. Mirrors tests/taxonomy/conftest.mock_embedding but
    inline because this file lives at backend/tests/ and only sees the
    top-level conftest.
  * _make_mock_provider(): AsyncMock spec=LLMProvider — returns a label/
    pattern stub for generate_label/_extract_meta_patterns.
  * _seed_taxonomy(db, n): inserts n active PromptCluster rows with
    deterministic centroids + member_count, suitable for cold_path's
    HDBSCAN input gate (>=3).
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import time as time_mod
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from sqlalchemy import event as sa_event

from app.models import PromptCluster, TaxonomySnapshot

EMBEDDING_DIM = 384


# ---------------------------------------------------------------------------
# Inline helpers (NO @pytest.fixture per RED contract)
# ---------------------------------------------------------------------------


def _make_mock_embedding() -> Any:
    """Mirror of tests/taxonomy/conftest.mock_embedding without the fixture
    wrapper. Hash-based determinism: text -> stable unit vector.
    """
    from app.services.embedding_service import EmbeddingService

    svc = MagicMock(spec=EmbeddingService)
    svc.dimension = EMBEDDING_DIM

    def _embed(text: str) -> np.ndarray:
        rng = np.random.RandomState(hash(text) % 2**31)
        vec = rng.randn(EMBEDDING_DIM).astype(np.float32)
        return vec / (np.linalg.norm(vec) + 1e-9)

    svc.embed_single.side_effect = _embed
    svc.aembed_single = AsyncMock(side_effect=_embed)
    svc.embed_texts.side_effect = lambda ts: [_embed(t) for t in ts]
    svc.aembed_texts = AsyncMock(side_effect=lambda ts: [_embed(t) for t in ts])
    svc.cosine_search = EmbeddingService.cosine_search
    return svc


def _make_mock_provider() -> Any:
    from app.providers.base import LLMProvider

    provider = AsyncMock(spec=LLMProvider)
    provider.name = "mock"
    result = MagicMock()
    result.label = "Mock Label"
    result.patterns = ["pat-a", "pat-b"]
    provider.complete_parsed.return_value = result
    return provider


async def _seed_taxonomy(db, n_clusters: int = 10) -> list[PromptCluster]:
    """Seed n active clusters with deterministic centroids spread across
    domains so HDBSCAN won't collapse the whole input to noise. Returns
    the inserted nodes.
    """
    rng = np.random.RandomState(0xC0DE)
    nodes: list[PromptCluster] = []
    for i in range(n_clusters):
        center = rng.randn(EMBEDDING_DIM).astype(np.float32)
        center /= np.linalg.norm(center) + 1e-9
        node = PromptCluster(
            label=f"Cluster {i}",
            state="active",
            domain="general",
            centroid_embedding=center.tobytes(),
            member_count=5,
            coherence=0.7,
            separation=0.6,
            color_hex="#a855f7",
        )
        db.add(node)
        nodes.append(node)
    await db.commit()
    return nodes


def _make_engine() -> Any:
    from app.services.taxonomy.engine import TaxonomyEngine

    return TaxonomyEngine(
        embedding_service=_make_mock_embedding(),
        provider=_make_mock_provider(),
    )


# ===========================================================================
# Test 1 — phases emit 4 decision events in order
# ===========================================================================


async def test_cold_path_phases_emit_4_decision_events_in_order(db_session) -> None:
    """Cold-path runs 4 phase functions in order: reembed → reassign → relabel
    → repair. Pre-Cycle-1 the phases don't exist → patches fail with
    AttributeError. Post-Cycle-1 the four functions run in sequence.
    """
    from app.services.taxonomy import cold_path as cp_mod
    from app.services.taxonomy.cold_path import execute_cold_path

    await _seed_taxonomy(db_session, n_clusters=10)
    engine = _make_engine()

    visited: list[str] = []

    def _record(name: str):
        async def _wrapper(*args, **kwargs):
            visited.append(name)
            return {"silhouette": 0.5, "active_after": [], "cluster_result": MagicMock(silhouette=0.5)}
        return _wrapper

    # These phase functions only exist post-Cycle-1.  Pre-Cycle-1
    # ``getattr(cp_mod, "_phase_1_reembed")`` raises AttributeError before
    # patch() can install the stand-in — that is the documented failure
    # signal for this test.
    with patch.object(cp_mod, "_phase_1_reembed", _record("reembed")), \
         patch.object(cp_mod, "_phase_2_reassign", _record("reassign")), \
         patch.object(cp_mod, "_phase_3_relabel", _record("relabel")), \
         patch.object(cp_mod, "_phase_4_repair", _record("repair")):
        await execute_cold_path(engine, db_session)

    assert visited == ["reembed", "reassign", "relabel", "repair"], (
        f"Phases must run in order: got {visited!r}"
    )


# ===========================================================================
# Test 2 — Q-check fires only after Phases 1 and 2
# ===========================================================================


async def test_cold_path_q_check_fires_only_after_phases_1_and_2(db_session) -> None:
    """is_cold_path_non_regressive() is invoked exactly twice (post-Phase-1
    and post-Phase-2). Phases 3 + 4 are cosmetic / housekeeping with no
    Q-impact. Pre-Cycle-1 the gate fires once at end-of-refit → count == 1
    → assert fails. Post-Cycle-1 count == 2 + signature includes ``phase``.
    """
    from app.services.taxonomy.cold_path import execute_cold_path

    await _seed_taxonomy(db_session, n_clusters=10)
    engine = _make_engine()

    calls: list[dict] = []

    def _spy(q_before, q_after, **kw):
        calls.append({"q_before": q_before, "q_after": q_after, **kw})
        return True

    # Patch the live import site inside cold_path.py (not the source module)
    # so the gate inside _execute_cold_path_inner is intercepted regardless
    # of GREEN-phase whether the function gains ``phase`` as kwarg or arg.
    with patch("app.services.taxonomy.cold_path.is_cold_path_non_regressive",
               side_effect=_spy):
        await execute_cold_path(engine, db_session)

    assert len(calls) == 2, (
        f"Expected exactly 2 Q-gate calls (post-phase 1 + 2); got {len(calls)}"
    )
    phases_seen = sorted(c.get("phase") for c in calls)
    assert phases_seen == [1, 2], (
        f"Q-gate calls must be tagged phase=1 then phase=2; got {phases_seen!r}"
    )


# ===========================================================================
# Test 3 — Q-regression at Phase 2 rolls back to pre-refit
# ===========================================================================


async def test_cold_path_q_regression_phase_2_rolls_back_to_pre_refit(db_session) -> None:
    """If Phase 2 (reassignment) drops Q below baseline, the rollback target
    is ``cp_pre_reembed`` (full revert). DB state matches pre-refit and
    engine._last_silhouette is restored to the pre-refit snapshot.

    Pre-Cycle-1: there is only one Q-gate at end-of-refit, no per-phase gate
    → the test's selective Phase-2-fails injection has no place to bind →
    the test fails because the second-call rejection doesn't fire (or, more
    commonly, the assertion that DB is unchanged fails because pre-Cycle-1
    code does whole-refit rollback differently).
    """
    from sqlalchemy import select

    from app.services.taxonomy.cold_path import execute_cold_path

    await _seed_taxonomy(db_session, n_clusters=10)
    engine = _make_engine()
    engine._last_silhouette = 0.42  # known baseline

    # Snapshot pre-refit cluster IDs + centroid bytes
    pre_rows = (await db_session.execute(select(PromptCluster))).scalars().all()
    pre_state = {r.id: bytes(r.centroid_embedding) for r in pre_rows if r.centroid_embedding}

    # Q-gate is fine after Phase 1 but rejects after Phase 2
    call_n = {"i": 0}

    def _phase_aware(q_before, q_after, **kw):
        call_n["i"] += 1
        # Reject only on phase=2.  If the production code doesn't pass
        # ``phase`` (pre-Cycle-1 behavior), the kwarg lookup returns None
        # and the test correctly fails to reject at the right boundary.
        if kw.get("phase") == 2:
            return False
        return True

    with patch("app.services.taxonomy.cold_path.is_cold_path_non_regressive",
               side_effect=_phase_aware):
        result = await execute_cold_path(engine, db_session)

    assert result is not None
    assert result.accepted is False, "Phase-2 regression must mark refit rejected"

    # Full revert: every pre-refit cluster centroid still byte-identical
    post_rows = (await db_session.execute(select(PromptCluster))).scalars().all()
    post_state = {r.id: bytes(r.centroid_embedding) for r in post_rows if r.centroid_embedding}
    assert post_state == pre_state, (
        "Phase-2 rollback target must be cp_pre_reembed (full revert); "
        "centroids should be byte-identical to pre-refit"
    )

    # Silhouette restoration on rollback
    assert engine._last_silhouette == pytest.approx(0.42), (
        "engine._last_silhouette must be restored from pre-refit snapshot"
    )


# ===========================================================================
# Test 4 — Q-eval exception triggers full revert
# ===========================================================================


async def test_cold_path_q_check_eval_exception_triggers_full_revert(db_session) -> None:
    """When ``_compute_q_from_nodes()`` itself raises during a phase-boundary
    Q-check, the cold path raises ``ColdPathQCheckEvalFailure`` (typed
    exception, declared in ``app.services.taxonomy.cold_path``).

    Pre-Cycle-1: the exception class doesn't exist → ImportError on the
    bare ``from app.services.taxonomy.cold_path import ColdPathQCheckEvalFailure``
    line → test fails with the documented signal.
    """
    # Lazy import inside the test body: pre-Cycle-1, this raises ImportError
    # at runtime (not at collection), keeping the failure explicit.
    from app.services.taxonomy.cold_path import (
        ColdPathQCheckEvalFailure,
        execute_cold_path,
    )

    await _seed_taxonomy(db_session, n_clusters=10)
    engine = _make_engine()
    engine._last_silhouette = 0.55

    # Make Q-eval raise unconditionally
    def _boom(*a, **kw):
        raise RuntimeError("eval boom")

    with patch.object(engine, "_compute_q_from_nodes", side_effect=_boom):
        with pytest.raises(ColdPathQCheckEvalFailure):
            await execute_cold_path(engine, db_session)

    # Silhouette restored; pre-refit baseline was 0.55
    assert engine._last_silhouette == pytest.approx(0.55), (
        "Eval exception is conservative rollback (unknown Q == regression)"
    )


# ===========================================================================
# Test 5 — Phase-batch exception rolls back to pre-refit
# ===========================================================================


async def test_cold_path_phase_batch_exception_rolls_back_to_pre_refit(db_session) -> None:
    """If a phase function raises a ``SQLAlchemyError`` mid-batch, the cold
    path raises ``ColdPathPhaseFailure`` (typed exception declared in
    ``app.services.taxonomy.cold_path``) and the DB is fully reverted.

    Pre-Cycle-1: ``ColdPathPhaseFailure`` doesn't exist → ImportError →
    test fails with documented signal.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError

    from app.services.taxonomy import cold_path as cp_mod

    # Lazy import of new exception type
    from app.services.taxonomy.cold_path import (
        ColdPathPhaseFailure,
        execute_cold_path,
    )

    await _seed_taxonomy(db_session, n_clusters=10)
    engine = _make_engine()

    # Snapshot pre-refit centroids
    pre_rows = (await db_session.execute(select(PromptCluster))).scalars().all()
    pre_state = {r.id: bytes(r.centroid_embedding) for r in pre_rows if r.centroid_embedding}

    async def _explode(*args, **kwargs):
        raise SQLAlchemyError("network blip")

    # _phase_1_reembed only exists post-Cycle-1.  Patching pre-Cycle-1
    # raises AttributeError — the documented failure signal.
    with patch.object(cp_mod, "_phase_1_reembed", _explode):
        with pytest.raises(ColdPathPhaseFailure):
            await execute_cold_path(engine, db_session)

    # DB unchanged
    post_rows = (await db_session.execute(select(PromptCluster))).scalars().all()
    post_state = {r.id: bytes(r.centroid_embedding) for r in post_rows if r.centroid_embedding}
    assert post_state == pre_state, (
        "Phase-exception rollback target must be cp_pre_reembed (full revert)"
    )


# ===========================================================================
# Test 6 — concurrent invocations serialize via lock
# ===========================================================================


async def test_cold_path_concurrent_invocations_serialize_via_lock(db_session) -> None:
    """Two concurrent ``execute_cold_path()`` calls must serialize via the
    module-level ``_COLD_PATH_LOCK`` (asyncio.Lock).

    Detection strategy: replace ``_execute_cold_path_inner`` with a tiny
    instrumented sleeper.  Each invocation stamps t_start/t_end into a
    shared list AND sleeps long enough that an unprotected race would
    interleave.  If the cold path serializes via ``_COLD_PATH_LOCK``
    (Cycle 1), invocation B starts AFTER invocation A ends.
    Pre-Cycle-1 there is no such lock → the asyncio.gather() schedules both
    tasks, both stamp t_start, both sleep concurrently, both stamp t_end →
    intervals overlap → assertion fails with "got overlapping intervals".
    """
    from app.services.taxonomy import cold_path as cp_mod
    from app.services.taxonomy.cold_path import execute_cold_path

    engine_a = _make_engine()
    engine_b = _make_engine()

    intervals: list[tuple[float, float]] = []

    async def _slow_inner(engine, db):
        t0 = time_mod.monotonic()
        # Yield + brief sleep so any unprotected overlap is observable.
        await asyncio.sleep(0.10)
        t1 = time_mod.monotonic()
        intervals.append((t0, t1))
        # Return a minimal ColdPathResult-shaped dataclass so the outer
        # try/finally completes cleanly.
        return cp_mod.ColdPathResult(
            snapshot_id="fake",
            q_before=0.5, q_after=0.5,
            accepted=True, nodes_created=0, nodes_updated=0,
            umap_fitted=False,
        )

    with patch.object(cp_mod, "_execute_cold_path_inner", _slow_inner):
        await asyncio.gather(
            execute_cold_path(engine_a, db_session),
            execute_cold_path(engine_b, db_session),
        )

    assert len(intervals) == 2, "Both calls should have recorded intervals"
    a, b = sorted(intervals, key=lambda x: x[0])
    # Serialized: b started AT or AFTER a ended (small clock slack).
    assert b[0] >= a[1] - 1e-3, (
        f"Concurrent execute_cold_path calls must serialize via _COLD_PATH_LOCK; "
        f"got overlapping intervals a={a!r} b={b!r}"
    )


# ===========================================================================
# Test 7 — silhouette restored from snapshot at engine bootstrap
# ===========================================================================


async def test_silhouette_restored_from_snapshot_event_fires_at_engine_bootstrap(
    db_session,
) -> None:
    """Engine bootstrap calls ``_restore_silhouette_from_snapshot(db)``: if
    ``_last_silhouette`` is None (process restart) and a recent accepted
    snapshot exists, the silhouette is hydrated from it.

    Pre-Cycle-1 the method doesn't exist → AttributeError on the call →
    test fails with documented signal.
    """
    engine = _make_engine()
    engine._last_silhouette = None  # simulate cold-start

    # Seed a recent accepted snapshot.  Pre-Cycle-1 TaxonomySnapshot has no
    # ``silhouette`` column; GREEN-phase adds one (or stores it on
    # cluster_metadata of a designated row).  For the RED test we set
    # whichever is present and additionally stash a sentinel under
    # q_system, so _restore can pick a sensible source.
    snap = TaxonomySnapshot(
        trigger="cold_path",
        q_system=0.611,
        q_coherence=0.7,
        q_separation=0.6,
        q_coverage=0.5,
        q_dbcv=0.0,
    )
    db_session.add(snap)
    await db_session.commit()

    # Pre-Cycle-1: AttributeError here.  Post-Cycle-1: method present.
    await engine._restore_silhouette_from_snapshot(db_session)

    assert engine._last_silhouette is not None, (
        "After bootstrap, _last_silhouette must be hydrated from snapshot"
    )
    assert engine._last_silhouette > 0.0, (
        "Restored silhouette must be a real value, not zero"
    )


# ===========================================================================
# Test 8 — cold_path_mode flag still set during refit (regression guard)
# ===========================================================================


async def test_cold_path_mode_flag_still_set_during_refit_in_v0_4_16(
    db_session,
) -> None:
    """Regression guard: the audit-hook bypass flag ``cold_path_mode`` is
    flipped to True for the duration of execute_cold_path() and cleared
    after.  This test PASSES pre-Cycle-1 (already implemented) and post-
    Cycle-1 (still required because audit-hook RAISE-in-prod flip is gated
    on a 7-day quiet period AFTER v0.4.16 ships).
    """
    from app.database import read_engine_meta
    from app.services.taxonomy import cold_path as cp_mod
    from app.services.taxonomy.cold_path import execute_cold_path

    await _seed_taxonomy(db_session, n_clusters=4)
    engine = _make_engine()

    seen: dict[str, Any] = {"flag_during_refit": None}

    real_inner = cp_mod._execute_cold_path_inner

    async def _peek_inner(engine, db):
        seen["flag_during_refit"] = read_engine_meta.cold_path_mode
        return await real_inner(engine, db)

    with patch.object(cp_mod, "_execute_cold_path_inner", _peek_inner):
        await execute_cold_path(engine, db_session)

    assert seen["flag_during_refit"] is True, (
        "cold_path_mode must be True during cold-path inner execution"
    )
    # And cleared after
    assert read_engine_meta.cold_path_mode is False, (
        "cold_path_mode must be cleared after execute_cold_path returns"
    )


# ===========================================================================
# Test 9 — execute_cold_path signature unchanged (regression guard)
# ===========================================================================


def test_execute_cold_path_signature_unchanged() -> None:
    """Regression guard: ``execute_cold_path(engine, db)`` is the public API.
    Cycle 1 reshapes the *body*; the outer signature stays exactly two
    positional parameters so existing callers (engine.run_cold_path,
    /api/clusters/recluster) keep working without keyword churn.
    """
    from app.services.taxonomy.cold_path import execute_cold_path

    sig = inspect.signature(execute_cold_path)
    params = list(sig.parameters.values())
    names = [p.name for p in params]
    assert names == ["engine", "db"], (
        f"execute_cold_path signature must remain (engine, db); got {names!r}"
    )
    # All POSITIONAL_OR_KEYWORD (no *args / **kwargs sneak-in)
    for p in params:
        assert p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD, (
            f"Parameter {p.name!r} must be POSITIONAL_OR_KEYWORD; got {p.kind}"
        )


# ===========================================================================
# Test 10 — _validate_cold_path_constants runs at import
# ===========================================================================


def test_validate_cold_path_constants_runs_at_import(monkeypatch) -> None:
    """``_validate_cold_path_constants()`` is called at module import time;
    a zero/negative batch-size constant must trip an AssertionError on
    re-import (fail-fast invariant).

    Pre-Cycle-1: the function and constants don't exist → ImportError on
    the bare ``from app.services.taxonomy._constants import
    _validate_cold_path_constants`` line → test fails with documented
    signal.
    """
    # Lazy imports; both raise pre-Cycle-1.
    from app.services.taxonomy import _constants as const_mod
    assert hasattr(const_mod, "_validate_cold_path_constants"), (
        "Cycle 1 must add _validate_cold_path_constants to _constants.py"
    )

    # Direct invariant check: zero batch size must trip AssertionError.
    monkeypatch.setattr(const_mod, "COLD_PATH_REEMBED_BATCH_SIZE", 0,
                        raising=False)
    with pytest.raises(AssertionError):
        const_mod._validate_cold_path_constants()


# ===========================================================================
# Test 11 — cold-path constants have expected default values
# ===========================================================================


def test_cold_path_constants_have_expected_default_values() -> None:
    """The 7 new constants ship with the documented defaults from spec § 6.
    Pre-Cycle-1: ImportError on every name lookup → test fails with
    documented signal.
    """
    # Reload to pick up the canonical values (avoid stale monkeypatched
    # state from preceding test 10).
    from app.services.taxonomy import _constants as const_mod
    importlib.reload(const_mod)

    assert const_mod.COLD_PATH_REEMBED_BATCH_SIZE == 50
    assert const_mod.COLD_PATH_REASSIGN_BATCH_SIZE == 200
    assert const_mod.COLD_PATH_LABEL_BATCH_SIZE == 20
    assert const_mod.COLD_PATH_REPAIR_BATCH_SIZE == 100
    assert const_mod.COLD_PATH_REFIT_QUIESCE_TIMEOUT_MIN == 5
    assert const_mod.COLD_PATH_LOG_PROGRESS_BATCH_INTERVAL == 10
    assert const_mod.COLD_PATH_LATENCY_RESERVOIR_SIZE == 1000


# ===========================================================================
# Test 12 — exactly 5 SAVEPOINTs per refit
# ===========================================================================


async def test_cold_path_emits_5_savepoints_per_refit(db_session) -> None:
    """One outer (cp_pre_reembed) + four inner (cp_post_reembed/reassign/
    relabel/repair) = 5 SAVEPOINTs.

    NOTE: spec's ``cp_*`` names are Python variable names / semantic
    anchors for the begin_nested() handle, NOT literal SQL identifiers
    (SQLAlchemy 2.x auto-emits ``SAVEPOINT sp_<n>`` at the SQL layer).
    This test asserts the COUNT (5) only — order and naming are not
    contractual at the SQL level.

    Pre-Cycle-1: cold-path is a single transaction with 0 SAVEPOINTs →
    count is 0 → assert fails with documented signal.
    """
    from app.services.taxonomy.cold_path import execute_cold_path

    await _seed_taxonomy(db_session, n_clusters=10)
    engine = _make_engine()

    savepoint_stmts: list[str] = []

    # Hook the underlying sync engine's "before_cursor_execute" event.  We
    # walk to the bind via db_session.bind once a connection exists.
    async with db_session.bind.connect() as raw_conn:
        sync_engine = raw_conn.sync_engine

    @sa_event.listens_for(sync_engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):
        s = statement.upper().lstrip()
        if s.startswith("SAVEPOINT"):
            savepoint_stmts.append(statement)

    try:
        await execute_cold_path(engine, db_session)
    finally:
        sa_event.remove(sync_engine, "before_cursor_execute", _capture)

    assert len(savepoint_stmts) == 5, (
        f"Expected exactly 5 SAVEPOINT statements (1 outer + 4 phase-inner); "
        f"got {len(savepoint_stmts)}: {savepoint_stmts!r}"
    )


# ===========================================================================
# Test 13 — run_id propagates through phases
# ===========================================================================


async def test_cold_path_run_id_propagates_through_phases(
    db_session, caplog
) -> None:
    """Cold-path entry assigns a UUID to the ``_COLD_PATH_RUN_ID`` ContextVar
    (declared in ``app.services.taxonomy.cold_path``).  Every log line
    emitted during the refit carries the same run_id.

    Pre-Cycle-1: ``_COLD_PATH_RUN_ID`` doesn't exist → ImportError on the
    bare ``from ... import _COLD_PATH_RUN_ID`` line → test fails with
    documented signal.
    """
    from app.services.taxonomy.cold_path import (
        _COLD_PATH_RUN_ID,
        execute_cold_path,
    )

    await _seed_taxonomy(db_session, n_clusters=10)
    engine = _make_engine()

    captured_run_ids: list[Any] = []

    # Capture the value as observed inside the inner execution. Wrap the
    # inner so we sample the ContextVar mid-run.
    from app.services.taxonomy import cold_path as cp_mod
    real_inner = cp_mod._execute_cold_path_inner

    async def _peek_inner(engine, db):
        captured_run_ids.append(_COLD_PATH_RUN_ID.get())
        return await real_inner(engine, db)

    caplog.set_level(logging.INFO, logger="app.services.taxonomy.cold_path")
    with patch.object(cp_mod, "_execute_cold_path_inner", _peek_inner):
        await execute_cold_path(engine, db_session)

    # ContextVar populated mid-run
    assert captured_run_ids, "Inner execution should have been invoked"
    run_id = captured_run_ids[0]
    assert run_id is not None, "_COLD_PATH_RUN_ID must be populated by entry"
    assert isinstance(run_id, str) and len(run_id) >= 16, (
        f"run_id should be a UUID hex/string; got {run_id!r}"
    )

    # All log records emitted from cold_path during this refit reference
    # the same run_id — either as ``run_id=<value>`` substring or via
    # structured ``extra`` dict.  Pre-Cycle-1 there is no run_id at all
    # so the substring is absent from every line → test fails.
    cold_path_records = [
        r for r in caplog.records
        if r.name == "app.services.taxonomy.cold_path"
    ]
    assert cold_path_records, "cold_path should emit at least one log line"
    assert any(run_id in r.getMessage() for r in cold_path_records), (
        f"Expected at least one cold_path log line to reference run_id={run_id!r}; "
        f"got messages: {[r.getMessage() for r in cold_path_records]!r}"
    )


# ===========================================================================
# Cycle 1 OPERATE — concurrent cold-path serialization (real DB)
# ===========================================================================


@pytest.mark.asyncio
class TestCycle1ColdPathSerialization:
    """v0.4.16 P1a Cycle 1 OPERATE — concurrent cold-path serialization.

    Spec § 4.4 + § 8 acceptance criterion 7. Two concurrent execute_cold_path()
    invocations must serialize via _COLD_PATH_LOCK (asyncio.Lock module-level).
    Each call gets its own _COLD_PATH_RUN_ID; the second waits for the first
    to release the lock.
    """

    async def test_two_concurrent_cold_paths_serialize_via_lock(
        self, db_session
    ):
        """Two execute_cold_path() calls fire via asyncio.gather; both complete
        without deadlock; ordering is preserved (second observes lock blocking)."""
        import asyncio
        import time

        # Use the same helpers used by the RED tests (defined inline in this file)
        await _seed_taxonomy(db_session, n_clusters=10)

        from app.services.taxonomy import cold_path as cp_mod
        from app.services.taxonomy.cold_path import execute_cold_path

        # Surface regression early if the GREEN-phase ContextVar is missing.
        assert hasattr(cp_mod, "_COLD_PATH_RUN_ID"), (
            "GREEN-phase regression: _COLD_PATH_RUN_ID ContextVar must exist on cold_path module"
        )
        assert hasattr(cp_mod, "_COLD_PATH_LOCK"), (
            "GREEN-phase regression: _COLD_PATH_LOCK must exist on cold_path module"
        )

        # The module-level asyncio.Lock binds to the first event loop that
        # touches it (test 6 typically). pytest-asyncio gives each test a
        # fresh loop, so reset the lock onto THIS test's loop before use —
        # otherwise asyncio raises "bound to a different event loop".
        original_lock = cp_mod._COLD_PATH_LOCK
        cp_mod._COLD_PATH_LOCK = asyncio.Lock()

        # Instrument to capture per-call entry/exit timestamps
        original_inner = cp_mod._execute_cold_path_inner
        timeline: list[tuple[str, float, str]] = []  # (event, ts_monotonic, run_id)

        async def instrumented(engine, db, *args, **kwargs):
            run_id = cp_mod._COLD_PATH_RUN_ID.get()
            timeline.append(("enter", time.monotonic(), run_id or "unknown"))
            await asyncio.sleep(0.05)  # ensure overlap window if lock fails
            result = await original_inner(engine, db, *args, **kwargs)
            timeline.append(("exit", time.monotonic(), run_id or "unknown"))
            return result

        # Patch via monkeypatch-equivalent (the test must restore on teardown)
        cp_mod._execute_cold_path_inner = instrumented
        try:
            engine = _make_engine()
            results = await asyncio.gather(
                execute_cold_path(engine, db_session),
                execute_cold_path(engine, db_session),
                return_exceptions=True,
            )
        finally:
            cp_mod._execute_cold_path_inner = original_inner
            cp_mod._COLD_PATH_LOCK = original_lock

        # Both must complete without exception
        assert all(not isinstance(r, BaseException) for r in results), (
            f"both runs must complete without exception: {results}"
        )

        # Distinct run_ids
        run_ids = {entry[2] for entry in timeline if entry[2] != "unknown"}
        assert len(run_ids) == 2, f"expected 2 distinct run_ids, got {run_ids}"

        # Serialization check: events must be in interleaving order
        # [enter_A, exit_A, enter_B, exit_B] — if lock works correctly, second
        # 'enter' fires AFTER first 'exit'. The instrumented sleep ensures any
        # lock-failure produces overlapping timestamps. Identify runs by
        # timeline order (NOT set iteration, which is non-deterministic).
        # The timeline records (event, ts, run_id) in monotonic order because
        # appends happen under the GIL inside the same coroutine awaits.
        first_run_id = next(rid for e, _ts, rid in timeline if e == "enter")
        first_run_exit = next(
            ts for e, ts, rid in timeline if e == "exit" and rid == first_run_id
        )
        second_run_enter = next(
            (ts for e, ts, rid in timeline if e == "enter" and rid != first_run_id),
            None,
        )
        assert second_run_enter is not None, (
            "second run must have entered the inner during the test window"
        )
        # second_run_enter MUST be >= first_run_exit (serialized via _COLD_PATH_LOCK)
        assert second_run_enter >= first_run_exit, (
            f"concurrent invocation must serialize: second enter ({second_run_enter:.3f}) "
            f"must be >= first exit ({first_run_exit:.3f})"
        )
