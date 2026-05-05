"""RED-phase tests for v0.4.16 P1a Cycle 2 — peer-writer SKIP + observability.

Spec: docs/specs/v0.4.16-cold-path-chunking-2026-05-04.md (v7 APPROVED)
  § 3.3 (peer-writer quiescing), § 4.3 (flag corruption), § 5.1-5.7
  (decision events / progress / health / reservoir / logging),
  § 12 Cycle 2 binding-test rows.
Plan: docs/plans/v0.4.16-p1a-cold-path-chunking-2026-05-04.md (v2 APPROVED)
  Task 2.1.

10 tests. ~8 must FAIL pre-Cycle-2 (with documented signal). #3 + #8
mostly PASS as recovery-primitive guards (the GREEN-phase implementation
must not regress them; see per-test docstrings for the failure-on-regress
contract).

Inline helpers (NO @pytest.fixture per RED constraint, mirroring
``test_cold_path_chunking_v0_4_16.py``):
  * ``_make_mock_embedding()`` — deterministic hash-based embedding service.
  * ``_make_mock_provider()`` — AsyncMock provider stub.
  * ``_seed_taxonomy(db, n)`` — n active PromptCluster rows.
  * ``_make_engine()`` — TaxonomyEngine bound to the mocks.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.models import PromptCluster, TaxonomySnapshot

EMBEDDING_DIM = 384


# ---------------------------------------------------------------------------
# Inline helpers (NO @pytest.fixture per RED contract)
# ---------------------------------------------------------------------------


def _make_mock_embedding() -> Any:
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


def _capture_log_decisions() -> tuple[list[dict[str, Any]], Any]:
    """Build a list-recorder + a wrapper for ``log_decision`` to install via
    ``patch.object(EventLogger, 'log_decision', new=wrapper)``.  Returns the
    backing list and the wrapper.

    The wrapper preserves the kwargs-only signature of the production
    ``log_decision()`` so tests don't accidentally couple to private
    internals.
    """
    captured: list[dict[str, Any]] = []

    def _record(
        *,
        path: str,
        op: str,
        decision: str,
        cluster_id: str | None = None,
        optimization_id: str | None = None,
        duration_ms: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        captured.append({
            "path": path,
            "op": op,
            "decision": decision,
            "cluster_id": cluster_id,
            "optimization_id": optimization_id,
            "duration_ms": duration_ms,
            "context": context or {},
        })

    return captured, _record


def _patch_event_logger(captured_records: list[dict[str, Any]], record_fn):
    """Patch the singleton ``log_decision`` and the bootstrap ``get_event_logger``.

    Cycle-2 production code can call either (a) ``get_event_logger().log_decision(...)``
    or (b) attach to a freshly resolved logger inside a long-lived helper.  We
    patch both surfaces so the recorder catches every emission.
    """

    class _StubLogger:
        log_decision = staticmethod(record_fn)

    return patch(
        "app.services.taxonomy.event_logger.get_event_logger",
        return_value=_StubLogger(),
    )


# ===========================================================================
# Test 1 — peer writer SKIPs quiesced cluster via flag check
# ===========================================================================


async def test_peer_writer_skips_quiesced_cluster_via_flag_check(db_session) -> None:
    """A cluster's ``cluster_metadata["refit_in_progress_until"]`` flag set to
    a future ISO timestamp must cause ``engine.process_optimization()`` to
    treat the cluster as quiesced — peer writer SKIPs the assignment AND
    re-marks the cluster in ``engine._dirty_set`` so the warm path retries
    on the next cycle.

    Pre-Cycle-2: there is NO peer-writer SKIP integration.  The hot path
    runs ``assign_cluster()`` and stamps ``opt.cluster_id`` regardless of
    the flag.  Documented failure signal: ``opt.cluster_id IS NOT None``
    after the call → assertion fails on the SKIP precondition.
    """
    from sqlalchemy import select

    from app.models import Optimization

    nodes = await _seed_taxonomy(db_session, n_clusters=4)
    engine = _make_engine()

    # Quiesce one cluster (5 minutes into the future)
    quiesced = nodes[0]
    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    quiesced.cluster_metadata = {"refit_in_progress_until": expires_at.isoformat()}
    await db_session.commit()

    # Build an Optimization that the hot path will try to assign.  The
    # embedding_service mock returns a deterministic vector — we want to
    # steer it toward the quiesced cluster via cosine match.  Easiest way:
    # set the optimization's embedding to the quiesced cluster's centroid.
    opt = Optimization(
        raw_prompt="seed prompt for quiesce SKIP test",
        status="completed",
        embedding=quiesced.centroid_embedding,
        intent_label="general help",
        domain="general",
        task_type="general",
        overall_score=7.0,
    )
    db_session.add(opt)
    await db_session.commit()

    # Snapshot dirty_set state before the call
    pre_dirty = dict(engine._dirty_set)

    # Pre-Cycle-2: this completes and assigns opt.cluster_id.  Post-Cycle-2:
    # the hot path checks the flag, re-marks dirty_set, and returns without
    # mutating opt.cluster_id.
    await engine.process_optimization(opt.id, db_session)

    refreshed = (
        await db_session.execute(select(Optimization).where(Optimization.id == opt.id))
    ).scalar_one()

    assert refreshed.cluster_id is None, (
        "Quiesced cluster must be SKIPPED by hot path (no Optimization.cluster_id "
        f"write); got cluster_id={refreshed.cluster_id!r}"
    )

    # Dirty set must include the quiesced cluster post-SKIP — the warm path
    # uses dirty_set as the retry primitive.
    assert quiesced.id in engine._dirty_set, (
        "Peer-writer SKIP must re-mark quiesced cluster in engine._dirty_set; "
        f"pre={set(pre_dirty)!r} post={set(engine._dirty_set)!r}"
    )


# ===========================================================================
# Test 2 — _parse_quiesce_flag returns None on corrupt / expired input
# ===========================================================================


async def test_parse_quiesce_flag_returns_none_on_corrupt_input() -> None:
    """``_parse_quiesce_flag(meta)`` must return ``None`` and emit a
    ``flag_corrupt`` decision event on every malformed / expired input:

      - missing key:       ``{}`` → reason=missing
      - non-string value:  ``{"refit_in_progress_until": 42}`` → reason=non_string
      - bad ISO string:    ``{"refit_in_progress_until": "not-iso"}`` → reason=iso_parse_fail
      - expired timestamp: ``{"refit_in_progress_until": (now - 10min).isoformat()}`` → reason=expired

    Pre-Cycle-2: ``_parse_quiesce_flag`` does not exist → ``AttributeError``
    on the import line → test fails with the documented signal.
    """
    # Lazy import: AttributeError surfaces at runtime, not at collection.
    from app.services.taxonomy import cold_path as cp_mod

    assert hasattr(cp_mod, "_parse_quiesce_flag"), (
        "Cycle 2 must add _parse_quiesce_flag() to cold_path.py"
    )

    captured, recorder = _capture_log_decisions()

    cases: list[tuple[dict[str, Any], str]] = [
        ({}, "missing"),
        ({"refit_in_progress_until": 42}, "non_string"),
        ({"refit_in_progress_until": "not-iso"}, "iso_parse_fail"),
        (
            {
                "refit_in_progress_until": (
                    datetime.now(UTC) - timedelta(minutes=10)
                ).isoformat()
            },
            "expired",
        ),
    ]

    with _patch_event_logger(captured, recorder):
        for meta, expected_reason in cases:
            assert cp_mod._parse_quiesce_flag(meta) is None, (
                f"_parse_quiesce_flag({meta!r}) must return None; "
                f"reason={expected_reason}"
            )

    # Each call must emit one flag_corrupt decision event with matching reason
    flag_events = [
        e for e in captured
        if e["decision"] == "flag_corrupt"
    ]
    assert len(flag_events) == len(cases), (
        f"Expected {len(cases)} flag_corrupt events; got {len(flag_events)}: "
        f"{[e['context'] for e in flag_events]!r}"
    )
    seen_reasons = {e["context"].get("reason") for e in flag_events}
    expected_reasons = {reason for _, reason in cases}
    assert seen_reasons == expected_reasons, (
        f"flag_corrupt reasons must cover {expected_reasons}; got {seen_reasons}"
    )


# ===========================================================================
# Test 3 — expired quiesce flag does NOT block the peer writer
# ===========================================================================


async def test_expired_quiesce_flag_does_not_block_peer_writer(db_session) -> None:
    """An expired ``refit_in_progress_until`` (timestamp in the past) must be
    treated by the peer writer as "no flag set" — writes proceed normally.

    The timestamp expiration is the **authoritative recovery primitive**:
    process crash mid-refit leaves orphan flags on clusters; the next peer
    writer's parse-flag check sees ``now > expires_at`` and proceeds.

    Pre-Cycle-2: there is no flag check at all, so the writer always
    proceeds — this test PASSES vacuously today. Post-Cycle-2 (and as a
    regression guard once GREEN lands), the production code MUST honour
    expiration; if a future regression makes the SKIP unconditional on
    presence-of-key (ignoring expiry), the test will fail.
    """
    from sqlalchemy import select

    from app.models import Optimization

    nodes = await _seed_taxonomy(db_session, n_clusters=4)
    engine = _make_engine()

    target = nodes[0]
    expires_at = datetime.now(UTC) - timedelta(minutes=1)  # EXPIRED
    target.cluster_metadata = {"refit_in_progress_until": expires_at.isoformat()}
    await db_session.commit()

    opt = Optimization(
        raw_prompt="seed prompt for expired-flag passthrough test",
        status="completed",
        embedding=target.centroid_embedding,
        intent_label="general help",
        domain="general",
        task_type="general",
        overall_score=7.0,
    )
    db_session.add(opt)
    await db_session.commit()

    await engine.process_optimization(opt.id, db_session)

    refreshed = (
        await db_session.execute(select(Optimization).where(Optimization.id == opt.id))
    ).scalar_one()
    assert refreshed.cluster_id is not None, (
        "Expired refit_in_progress_until must NOT block the peer writer; "
        "Optimization.cluster_id should be assigned. "
        f"got cluster_id={refreshed.cluster_id!r}"
    )


# ===========================================================================
# Test 4 — /api/health exposes cold_path block with required fields
# ===========================================================================


async def test_health_endpoint_exposes_cold_path_block_with_required_fields(
    app_client,
) -> None:
    """``GET /api/health`` must return a top-level ``cold_path`` JSON block
    with the 9 fields documented in spec § 5.5:

      - ``last_run_at``                 : str | None (ISO timestamp)
      - ``last_run_duration_ms``        : int | None
      - ``last_run_q_delta``            : float | None
      - ``last_run_phases_committed``   : int | None
      - ``last_run_status``             : str | None ("accepted" / "rejected" / "failed")
      - ``peer_skip_count_24h``         : int
      - ``rejection_count_24h``         : int
      - ``phase_failure_count_24h``     : int
      - ``p95_phase_duration_ms``       : dict[str, int | None] with 4 phase keys

    Pre-Cycle-2: ``HealthResponse`` has no ``cold_path`` field → KeyError
    on the assert.
    """
    response = await app_client.get("/api/health?probes=false")
    assert response.status_code == 200, (
        f"/api/health must return 200; got {response.status_code}: {response.text}"
    )
    body = response.json()

    assert "cold_path" in body, (
        f"Health response must include 'cold_path' block (Cycle 2 requirement); "
        f"got keys={sorted(body.keys())!r}"
    )

    cold_path = body["cold_path"]
    required_fields = {
        "last_run_at",
        "last_run_duration_ms",
        "last_run_q_delta",
        "last_run_phases_committed",
        "last_run_status",
        "peer_skip_count_24h",
        "rejection_count_24h",
        "phase_failure_count_24h",
        "p95_phase_duration_ms",
    }
    actual_fields = set(cold_path.keys())
    missing = required_fields - actual_fields
    assert not missing, (
        f"cold_path block missing required fields: {missing!r}; "
        f"got {actual_fields!r}"
    )

    # p95 dict has 4 phase keys (one per cold-path phase)
    p95 = cold_path["p95_phase_duration_ms"]
    assert isinstance(p95, dict), (
        f"p95_phase_duration_ms must be a dict; got {type(p95).__name__}"
    )
    expected_phase_keys = {"1_reembed", "2_reassign", "3_relabel", "4_repair"}
    actual_phase_keys = set(p95.keys())
    assert actual_phase_keys == expected_phase_keys, (
        f"p95_phase_duration_ms must have keys {expected_phase_keys!r}; "
        f"got {actual_phase_keys!r}"
    )


# ===========================================================================
# Test 5 — success path emits 6 decision-event types in order
# ===========================================================================


async def test_cold_path_success_path_emits_6_event_types_in_order(
    db_session,
) -> None:
    """A clean cold-path run must emit, in order, exactly these 6 distinct
    decision-event TYPES (multiplicities documented in spec § 5.1):

      1. lock_acquired                     (×1)
      2. cold_path_started                 (×1)
      3. cold_path_phase_started           (×4 — one per phase)
      4. cold_path_phase_committed         (×4 — one per phase)
      5. cold_path_q_check decision=pass   (×2 — Phase 1 + Phase 2)
      6. cold_path_completed               (×1)

    Pre-Cycle-2: NONE of these events fire → captured list is empty → test
    fails with a clear "0 / 6 event types observed" signal.
    """
    from app.services.taxonomy.cold_path import execute_cold_path

    await _seed_taxonomy(db_session, n_clusters=10)
    engine = _make_engine()

    captured, recorder = _capture_log_decisions()

    with _patch_event_logger(captured, recorder):
        await execute_cold_path(engine, db_session)

    decisions = [e["decision"] for e in captured if e["path"] == "cold"]

    # Required event TYPES (multiplicities flatten to 6 distinct decisions)
    required_in_order = [
        "lock_acquired",
        "cold_path_started",
        "cold_path_phase_started",
        "cold_path_phase_committed",
        "cold_path_q_check",
        "cold_path_completed",
    ]
    seen_types = []
    for d in decisions:
        if d in required_in_order and d not in seen_types:
            seen_types.append(d)

    assert seen_types == required_in_order, (
        f"Success-path event types must fire in spec order; expected "
        f"{required_in_order!r}; first-seen order was {seen_types!r}; "
        f"all decisions={decisions!r}"
    )

    # Multiplicities
    n_started = decisions.count("cold_path_phase_started")
    n_committed = decisions.count("cold_path_phase_committed")
    n_q_check = decisions.count("cold_path_q_check")
    assert n_started == 4, (
        f"cold_path_phase_started must fire 4 times (one per phase); got {n_started}"
    )
    assert n_committed == 4, (
        f"cold_path_phase_committed must fire 4 times (one per phase); got {n_committed}"
    )
    assert n_q_check == 2, (
        f"cold_path_q_check must fire 2 times (Phase 1 + Phase 2); got {n_q_check}"
    )

    # All Q-check events on success path must have decision=pass via context
    q_check_events = [e for e in captured if e["decision"] == "cold_path_q_check"]
    for ev in q_check_events:
        ctx_decision = ev["context"].get("decision")
        assert ctx_decision == "pass", (
            f"Success-path cold_path_q_check must have context['decision']='pass'; "
            f"got {ctx_decision!r} in {ev!r}"
        )


# ===========================================================================
# Test 6 — Q-regression at Phase 2 emits phase_rolled_back; no completed
# ===========================================================================


async def test_cold_path_q_regression_emits_phase_rolled_back_event_no_completed(
    db_session,
) -> None:
    """When ``is_cold_path_non_regressive`` returns False at Phase 2 the
    cold path must:

      - emit a ``cold_path_q_check`` event with ``context['decision'] == 'fail'``
      - emit ``cold_path_phase_rolled_back`` with ``context['reason'] == 'q_regression'``
      - NOT emit ``cold_path_completed``

    Pre-Cycle-2: none of these events exist → captured empty → all
    assertions fail.
    """
    from app.services.taxonomy.cold_path import execute_cold_path

    await _seed_taxonomy(db_session, n_clusters=10)
    engine = _make_engine()
    engine._last_silhouette = 0.42

    def _fail_at_phase_2(q_before, q_after, **kw):
        return kw.get("phase") != 2

    captured, recorder = _capture_log_decisions()

    with _patch_event_logger(captured, recorder), \
         patch(
             "app.services.taxonomy.cold_path.is_cold_path_non_regressive",
             side_effect=_fail_at_phase_2,
         ):
        await execute_cold_path(engine, db_session)

    decisions = [e["decision"] for e in captured if e["path"] == "cold"]

    # cold_path_q_check with decision=fail must exist
    fail_q_checks = [
        e for e in captured
        if e["decision"] == "cold_path_q_check"
        and e["context"].get("decision") == "fail"
    ]
    assert fail_q_checks, (
        f"Phase-2 regression must emit cold_path_q_check decision=fail; "
        f"got decisions={decisions!r}"
    )

    rollback_events = [
        e for e in captured
        if e["decision"] == "cold_path_phase_rolled_back"
        and e["context"].get("reason") == "q_regression"
    ]
    assert rollback_events, (
        f"Phase-2 regression must emit cold_path_phase_rolled_back reason=q_regression; "
        f"got decisions={decisions!r}"
    )

    assert "cold_path_completed" not in decisions, (
        f"cold_path_completed MUST NOT fire on Q-regression failure path; "
        f"got decisions={decisions!r}"
    )


# ===========================================================================
# Test 7 — phase exception emits phase_rolled_back reason=phase_exception
# ===========================================================================


async def test_cold_path_phase_exception_emits_phase_rolled_back_with_reason_phase_exception(
    db_session,
) -> None:
    """When a phase function raises ``SQLAlchemyError`` mid-execution, the
    cold path must emit ``cold_path_phase_rolled_back`` with
    ``context['reason'] == 'phase_exception'`` (per spec § 5.1 enum +
    § 8 acceptance criterion 3).

    Pre-Cycle-2: no decision event is fired on exception → captured empty
    → assertion fails.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from app.services.taxonomy import cold_path as cp_mod
    from app.services.taxonomy.cold_path import (
        ColdPathPhaseFailure,
        execute_cold_path,
    )

    await _seed_taxonomy(db_session, n_clusters=10)
    engine = _make_engine()

    async def _explode(*args, **kwargs):
        raise SQLAlchemyError("network blip in Phase 1")

    captured, recorder = _capture_log_decisions()

    with _patch_event_logger(captured, recorder), \
         patch.object(cp_mod, "_phase_1_reembed", _explode):
        with pytest.raises(ColdPathPhaseFailure):
            await execute_cold_path(engine, db_session)

    rollback_events = [
        e for e in captured
        if e["decision"] == "cold_path_phase_rolled_back"
        and e["context"].get("reason") == "phase_exception"
    ]
    assert rollback_events, (
        f"Phase exception must emit cold_path_phase_rolled_back with "
        f"reason='phase_exception' (spec § 5.1 enum); "
        f"got captured={[(e['decision'], e['context']) for e in captured]!r}"
    )


# ===========================================================================
# Test 8 — silhouette restore event fires at engine bootstrap with all 3 fields
# ===========================================================================


async def test_silhouette_restored_from_snapshot_event_fires_at_engine_bootstrap(
    db_session,
) -> None:
    """``engine._restore_silhouette_from_snapshot(db)`` must, when an accepted
    snapshot is found and ``_last_silhouette`` is None / 0, emit a
    ``silhouette_restored_from_snapshot`` decision event with these
    context fields (spec § 5.1):

      - ``silhouette_value`` (float)
      - ``snapshot_id`` (str)
      - ``snapshot_age_seconds`` (int / float)

    Pre-Cycle-2: the event already fires (added in Cycle 1) but is missing
    ``snapshot_age_seconds`` from the payload → assertion fails on the
    age-field check. Cycle 2 GREEN adds the missing field.

    This test serves as the contract pin for the recovery event payload.
    """
    engine = _make_engine()
    engine._last_silhouette = None

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

    captured, recorder = _capture_log_decisions()

    with _patch_event_logger(captured, recorder):
        await engine._restore_silhouette_from_snapshot(db_session)

    restore_events = [
        e for e in captured
        if e["decision"] == "silhouette_restored_from_snapshot"
    ]
    assert restore_events, (
        f"engine bootstrap must emit silhouette_restored_from_snapshot when "
        f"a snapshot is hydrated; got captured={captured!r}"
    )

    payload = restore_events[0]["context"]
    required_fields = {"silhouette_value", "snapshot_id", "snapshot_age_seconds"}
    missing = required_fields - set(payload.keys())
    assert not missing, (
        f"silhouette_restored_from_snapshot payload missing fields: "
        f"{missing!r}; got payload={payload!r}"
    )


# ===========================================================================
# Test 9 — batch_progress events fire at COLD_PATH_LOG_PROGRESS_BATCH_INTERVAL
# ===========================================================================


async def test_cold_path_emits_batch_progress_event_at_threshold(
    db_session,
    monkeypatch,
) -> None:
    """When a phase processes more than
    ``COLD_PATH_LOG_PROGRESS_BATCH_INTERVAL=10`` batches the cold path must
    emit ``batch_progress`` decision events at the threshold and at the
    final batch index.

    Test plan: shrink ``COLD_PATH_REEMBED_BATCH_SIZE=2`` so a 25-cluster
    seed taxonomy produces 13 batches in Phase 1.  Expect ``batch_progress``
    at batch_index 10 AND at batch_index 13.

    Pre-Cycle-2: no per-batch progress event exists → captured list empty
    of ``batch_progress`` → assertion fails.
    """
    from app.services.taxonomy import _constants as const_mod
    from app.services.taxonomy.cold_path import execute_cold_path

    # Shrink the batch size to force multi-batch Phase 1
    monkeypatch.setattr(const_mod, "COLD_PATH_REEMBED_BATCH_SIZE", 2, raising=False)
    # Some implementations import the constant once into cold_path's module
    # namespace; mirror the override there too if present.
    from app.services.taxonomy import cold_path as cp_mod
    if hasattr(cp_mod, "COLD_PATH_REEMBED_BATCH_SIZE"):
        monkeypatch.setattr(
            cp_mod, "COLD_PATH_REEMBED_BATCH_SIZE", 2, raising=False
        )

    await _seed_taxonomy(db_session, n_clusters=25)
    engine = _make_engine()

    captured, recorder = _capture_log_decisions()

    with _patch_event_logger(captured, recorder):
        await execute_cold_path(engine, db_session)

    progress_events = [
        e for e in captured
        if e["decision"] == "batch_progress"
    ]
    assert progress_events, (
        f"Phase with > {const_mod.COLD_PATH_LOG_PROGRESS_BATCH_INTERVAL} "
        f"batches must emit batch_progress decision events; "
        f"got captured decisions="
        f"{[e['decision'] for e in captured]!r}"
    )

    batch_indices = sorted({
        ev["context"].get("batch_index")
        for ev in progress_events
        if ev["context"].get("phase") == 1
    })
    # Expect emissions at batch indices 10 (threshold) and 13 (final).
    assert 10 in batch_indices, (
        f"batch_progress at batch_index=10 (threshold) missing; "
        f"got indices={batch_indices!r}"
    )
    assert 13 in batch_indices, (
        f"batch_progress at batch_index=13 (final) missing; "
        f"got indices={batch_indices!r}"
    )


# ===========================================================================
# Test 10 — cold_path_q_check payload includes per-dimension Q breakdown
# ===========================================================================


async def test_q_check_event_payload_includes_per_dimension_breakdown(
    db_session,
) -> None:
    """The ``cold_path_q_check`` event payload must include the per-dimension
    Q breakdown (spec § 5.4):

      - ``q_coherence``
      - ``q_separation``
      - ``q_coverage``
      - ``q_dbcv``
      - ``q_stability``

    in addition to the base contract fields ``q_before``, ``q_after``,
    ``delta``, ``decision`` (already documented in spec § 5.1 row 4).

    Pre-Cycle-2: cold_path_q_check event isn't emitted at all → captured
    empty → assertion fails.
    """
    from app.services.taxonomy.cold_path import execute_cold_path

    await _seed_taxonomy(db_session, n_clusters=10)
    engine = _make_engine()

    captured, recorder = _capture_log_decisions()

    with _patch_event_logger(captured, recorder):
        await execute_cold_path(engine, db_session)

    q_check_events = [
        e for e in captured
        if e["decision"] == "cold_path_q_check"
    ]
    assert q_check_events, (
        f"cold_path_q_check decision events must fire on every refit (spec § 5.1); "
        f"got captured decisions={[e['decision'] for e in captured]!r}"
    )

    base_fields = {"q_before", "q_after", "delta", "decision"}
    breakdown_fields = {
        "q_coherence",
        "q_separation",
        "q_coverage",
        "q_dbcv",
        "q_stability",
    }
    required_fields = base_fields | breakdown_fields

    for ev in q_check_events:
        payload_keys = set(ev["context"].keys())
        missing = required_fields - payload_keys
        assert not missing, (
            f"cold_path_q_check payload missing fields: {missing!r}; "
            f"got context={ev['context']!r}"
        )
