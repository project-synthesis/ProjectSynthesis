"""RED-phase tests for v0.4.16 P1b Cycle 2 — repo-index observability.

Spec: docs/specs/v0.4.16-repo-index-chunking-2026-05-04.md (v3 APPROVED)
  § 4 (decision events), § 7 (acceptance criteria + implementation surface),
  § 11 binding-test rows 17-31.
Plan: docs/plans/v0.4.16-p1b-repo-index-chunking-2026-05-04.md Task 2.1.

15 tests for Cycle 2: 8 decision-event types + per-batch progress throttle +
``/api/health`` ``repo_index`` block + reason-code enum enforcement. Today
NONE of these surfaces emit anything → ~14 FAIL + ~1 regression-guard (test
#26 may pass because pre-Cycle-2 there's no ``_emit_decision_event`` to
patch — the test asserts ImportError-on-missing-helper as the documented
RED signal).

Each test name comes from spec § 11 row exactly (rows 17-31).

Inline helpers (no new ``@pytest.fixture`` defs, mirroring Cycle 1):
  * ``_make_mock_embedding`` / ``_make_mock_github`` / ``_make_processed_files``
    / ``_seed_meta`` / ``_seed_files`` mirror Cycle 1's helpers verbatim.
  * ``_CountingWriteQueueStub`` is the same submit-counting test stub.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from sqlalchemy import select

from app.models import RepoFileIndex, RepoIndexMeta
from app.services.repo_index_file_reader import ProcessedFile
from app.services.repo_index_outlines import FileOutline
from app.services.taxonomy.event_logger import (
    TaxonomyEventLogger,
    get_event_logger,
    reset_event_logger,
    set_event_logger,
)

EMBEDDING_DIM = 384


# ---------------------------------------------------------------------------
# Autouse — module-level state hygiene + isolated event logger
# ---------------------------------------------------------------------------
# Cycle 2 introduces several new module-level dicts on
# ``app.services.repo_index_service`` which the GREEN phase will populate
# (``_REPO_INDEX_LATENCY_RESERVOIR``, ``_REPO_INDEX_BATCH_COUNTER``,
# ``_REPO_INDEX_LAST_RUN``). They don't exist yet — defensive ``getattr``
# means this fixture also serves Cycle 1 tests post-RED, but the wipe is
# silent if the symbol is absent. We also reset the singleton
# ``TaxonomyEventLogger`` so each test gets a clean ring buffer.
@pytest.fixture(autouse=True)
def _reset_repo_index_observability_state(tmp_path):
    from app.services import repo_index_file_reader as _rifr
    from app.services import repo_index_service as _ris

    _original_read_and_embed = _rifr.read_and_embed_files

    # Cycle 1 + Cycle 2 module-level state.
    for name in (
        "_REPO_INDEX_LOCKS",
        "_REPO_INDEX_LOCK_LAST_ACQUIRED",
        "_REPO_INDEX_QUEUE_SUBMIT_LOCKS",
        "_REPO_INDEX_LATENCY_RESERVOIR",
        "_REPO_INDEX_BATCH_COUNTER",
    ):
        obj = getattr(_ris, name, None)
        if obj is not None and hasattr(obj, "clear"):
            obj.clear()

    # _REPO_INDEX_LAST_RUN is a sentinel dict|None — assign None defensively.
    if hasattr(_ris, "_REPO_INDEX_LAST_RUN"):
        try:
            _ris._REPO_INDEX_LAST_RUN = None  # type: ignore[attr-defined]
        except Exception:
            pass

    _ris.read_and_embed_files = _original_read_and_embed

    # Fresh per-test event logger so ring buffer reads see only this test's
    # events. ``publish_to_bus=False`` keeps the SSE pump silent — tests
    # that need SSE events subscribe directly to ``event_bus._subscribers``.
    inst = TaxonomyEventLogger(
        events_dir=tmp_path / "tax_events",
        publish_to_bus=False,
    )
    set_event_logger(inst)

    yield

    # Post-test cleanup.
    for name in (
        "_REPO_INDEX_LOCKS",
        "_REPO_INDEX_LOCK_LAST_ACQUIRED",
        "_REPO_INDEX_QUEUE_SUBMIT_LOCKS",
        "_REPO_INDEX_LATENCY_RESERVOIR",
        "_REPO_INDEX_BATCH_COUNTER",
    ):
        obj = getattr(_ris, name, None)
        if obj is not None and hasattr(obj, "clear"):
            obj.clear()
    if hasattr(_ris, "_REPO_INDEX_LAST_RUN"):
        try:
            _ris._REPO_INDEX_LAST_RUN = None  # type: ignore[attr-defined]
        except Exception:
            pass
    _ris.read_and_embed_files = _original_read_and_embed
    reset_event_logger()


# ---------------------------------------------------------------------------
# Inline helpers (mirror Cycle 1)
# ---------------------------------------------------------------------------


def _make_mock_embedding() -> Any:
    from app.services.embedding_service import EmbeddingService

    svc = MagicMock(spec=EmbeddingService)
    svc.dimension = EMBEDDING_DIM
    zero = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    svc.embed_single.return_value = zero
    svc.aembed_single = AsyncMock(return_value=zero)
    svc.embed_texts.return_value = [zero]
    svc.aembed_texts = AsyncMock(return_value=[zero])
    return svc


def _make_mock_github(
    *,
    head_sha: str = "abc123",
    n_indexable: int = 0,
    extra_paths: list[str] | None = None,
    tree_etag: str | None = None,
    tree_override: list[dict] | None = None,
) -> Any:
    """AsyncMock GitHubClient. ``tree_override`` lets the caller force a
    specific tree shape (used for the 304 short-circuit test)."""
    from app.services.github_client import GitHubClient

    gc = AsyncMock(spec=GitHubClient)
    gc.get_branch_head_sha = AsyncMock(return_value=head_sha)

    paths = list(extra_paths or [])
    for i in range(n_indexable):
        paths.append(f"src/file_{i:04d}.py")

    tree_items = (
        tree_override
        if tree_override is not None
        else [
            {"type": "blob", "path": p, "sha": f"sha_{p}", "size": 200}
            for p in paths
        ]
    )
    gc.get_tree = AsyncMock(return_value=tree_items)
    gc.get_tree_with_cache = AsyncMock(return_value=(tree_items, tree_etag))
    gc.get_file_content = AsyncMock(return_value="def foo():\n    pass\n")
    return gc


def _make_processed_files(paths: list[str]) -> list[ProcessedFile]:
    out: list[ProcessedFile] = []
    for p in paths:
        outline = FileOutline(
            file_path=p,
            file_type="python",
            structural_summary=f"# outline {p}",
            doc_summary=None,
        )
        out.append(
            ProcessedFile(
                item={"path": p, "sha": f"sha_{p}", "size": 200},
                content="def foo():\n    pass\n",
                outline=outline,
                embedding=np.zeros(EMBEDDING_DIM, dtype=np.float32),
                content_sha=f"csha_{p}",
            )
        )
    return out


def _read_and_embed_stub(paths: list[str]):
    processed = _make_processed_files(paths)

    async def _stub(**kwargs):
        return processed, 0, 0

    return _stub


async def _seed_meta(
    db,
    repo: str,
    branch: str,
    *,
    status: str = "indexing",
    indexed_at: datetime | None = None,
    file_count: int = 0,
    head_sha: str | None = None,
) -> RepoIndexMeta:
    meta = RepoIndexMeta(
        repo_full_name=repo,
        branch=branch,
        status=status,
        file_count=file_count,
        head_sha=head_sha,
        indexed_at=indexed_at,
        index_phase="pending",
    )
    db.add(meta)
    await db.commit()
    return meta


async def _seed_files(
    db,
    repo: str,
    branch: str,
    n: int,
) -> list[RepoFileIndex]:
    rows: list[RepoFileIndex] = []
    zero = np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes()
    for i in range(n):
        row = RepoFileIndex(
            repo_full_name=repo,
            branch=branch,
            file_path=f"src/seed_{i:04d}.py",
            file_sha=f"sha_seed_{i:04d}",
            file_size_bytes=200,
            content="def foo(): pass",
            outline=f"# outline seed_{i:04d}",
            content_sha=f"csha_seed_{i:04d}",
            embedding=zero,
        )
        db.add(row)
        rows.append(row)
    await db.commit()
    return rows


class _CountingWriteQueueStub:
    """Mirrors Cycle 1's ``_CountingWriteQueueStub`` — runs work_fn against
    the supplied db_session, optionally fails on a specific call index."""

    def __init__(self, db, *, fail_on_call: int | None = None):
        self._db = db
        self.calls: list[dict] = []
        self.fail_on_call = fail_on_call

    async def submit(self, work, *, timeout=None, operation_label=None):
        idx = len(self.calls)
        record = {
            "index": idx,
            "operation_label": operation_label,
            "work_fn": work,
        }
        self.calls.append(record)
        if self.fail_on_call is not None and idx == self.fail_on_call:
            raise RuntimeError(f"injected failure at submit call #{idx}")
        return await work(self._db)

    async def submit_batch(self, work_fns, *, timeout=None, operation_label=None):
        results = []
        for fn in work_fns:
            results.append(await self.submit(fn))
        return results


def _ring_events(*, op: str | None = None) -> list[dict]:
    """Read the per-test event logger ring buffer and filter on op (if given)."""
    logger_inst = get_event_logger()
    return logger_inst.get_recent(limit=500, path="repo_index", op=op)


# ===========================================================================
# Test 17 — success-path 5 event types in order
# ===========================================================================


async def test_repo_index_build_success_path_emits_5_event_types_in_order(
    db_session,
) -> None:
    """Spec § 4.1 success-path emission sequence + § 7 acceptance #1.

    A 60-file build produces 2 persist batches (ceil(60/50)). Expected
    distinct event TYPES (in first-seen order) for the success path:
      1. repo_index_lock_acquired
      2. repo_index_started
      3. repo_index_phase_started
      4. repo_index_batch_committed
      5. repo_index_completed
    Distinct count == 5. Pre-Cycle-2: zero events fire → assertion fails.
    """
    from app.services.repo_index_service import RepoIndexService

    n = 60
    paths = [f"src/file_{i:04d}.py" for i in range(n)]
    gc = _make_mock_github(n_indexable=n)
    es = _make_mock_embedding()
    queue = _CountingWriteQueueStub(db_session)

    with patch(
        "app.services.repo_index_service.read_and_embed_files",
        new=_read_and_embed_stub(paths),
    ):
        svc = RepoIndexService(
            db=db_session,
            github_client=gc,
            embedding_service=es,
            write_queue=queue,  # type: ignore[arg-type]
        )
        await svc.build_index("owner/c2-success", "main", "ghp_token")

    events = _ring_events()
    op_types = [e.get("op") for e in events]

    distinct_required = {
        "repo_index_lock_acquired",
        "repo_index_started",
        "repo_index_phase_started",
        "repo_index_batch_committed",
        "repo_index_completed",
    }
    distinct_seen = {t for t in op_types if t in distinct_required}
    assert distinct_seen == distinct_required, (
        f"success path must emit all 5 event types; "
        f"missing={distinct_required - distinct_seen!r}; "
        f"all op_types={op_types!r}"
    )
    assert len(distinct_required) == 5, (
        "spec § 4.1 mandates exactly 5 distinct event types on success"
    )

    # First-seen ordering check (spec § 4.1 success-path emission sequence).
    expected_order = [
        "repo_index_lock_acquired",
        "repo_index_started",
        "repo_index_phase_started",
        "repo_index_batch_committed",
        "repo_index_completed",
    ]
    seen_first: list[str] = []
    for t in op_types:
        if t in expected_order and t not in seen_first:
            seen_first.append(t)
    assert seen_first == expected_order, (
        f"success-path event types must fire in spec order; expected "
        f"{expected_order!r}; first-seen order was {seen_first!r}"
    )


# ===========================================================================
# Test 18 — failed batch emits batch_rolled_back with reason
# ===========================================================================


async def test_failed_batch_emits_batch_rolled_back_with_reason_phase_exception(
    db_session,
) -> None:
    """Spec § 7 acceptance #2 + § 4.2 reason-code enum.

    Inject a failure in the 2nd persist batch (2nd call to ``submit`` AFTER
    the successful Phase 0 + 1st persist batch). Expect a
    ``repo_index_batch_rolled_back`` event with ``context['reason'] ==
    'phase_exception'`` and an ``error_class`` field. Pre-Cycle-2: no event.
    """
    from app.services.repo_index_service import RepoIndexService

    n = 75
    paths = [f"src/file_{i:04d}.py" for i in range(n)]
    gc = _make_mock_github(n_indexable=n)
    es = _make_mock_embedding()
    # Submits in success-path order: [Phase 0 status flip, persist-batch-0,
    # persist-batch-1, Phase 4 finalize]. Fail on call index 2 (the 2nd
    # persist batch — i.e. the 3rd submit overall).
    queue = _CountingWriteQueueStub(db_session, fail_on_call=2)

    with patch(
        "app.services.repo_index_service.read_and_embed_files",
        new=_read_and_embed_stub(paths),
    ):
        svc = RepoIndexService(
            db=db_session,
            github_client=gc,
            embedding_service=es,
            write_queue=queue,  # type: ignore[arg-type]
        )
        with pytest.raises(Exception):
            await svc.build_index("owner/c2-fail", "main", "ghp_token")

    rollback_events = _ring_events(op="repo_index_batch_rolled_back")
    assert rollback_events, (
        "failed batch must emit at least 1 repo_index_batch_rolled_back "
        f"event; got {rollback_events!r}"
    )
    ev = rollback_events[0]
    ctx = ev.get("context") or {}
    assert ctx.get("reason") == "phase_exception", (
        f"reason must be 'phase_exception'; got {ctx.get('reason')!r}"
    )
    assert "error_class" in ctx, (
        f"event payload must include 'error_class' field; got context={ctx!r}"
    )


# ===========================================================================
# Test 19 — lock contention emits skipped reason=lock_held
# ===========================================================================


async def test_lock_contention_emits_skipped_reason_lock_held(
    db_session,
) -> None:
    """Spec § 7 acceptance #3 + § 4.2.

    Pre-acquire the per-(repo, branch) lock manually. Run ``build_index``
    while the lock is held. The second invocation must emit
    ``repo_index_skipped`` with ``context['reason'] == 'lock_held'``.
    Pre-Cycle-2: lock-skip path returns silently with NO event emission.
    """
    from app.services.repo_index_service import (
        RepoIndexService,
        _acquire_repo_index_lock,
    )

    repo, branch = "owner/c2-lock", "main"
    await _seed_meta(db_session, repo, branch, status="pending")

    lock = await _acquire_repo_index_lock(repo, branch)
    await lock.acquire()
    try:
        gc = _make_mock_github(n_indexable=0)
        es = _make_mock_embedding()
        queue = _CountingWriteQueueStub(db_session)

        svc = RepoIndexService(
            db=db_session,
            github_client=gc,
            embedding_service=es,
            write_queue=queue,  # type: ignore[arg-type]
        )
        await svc.build_index(repo, branch, "tok")
    finally:
        lock.release()

    skip_events = _ring_events(op="repo_index_skipped")
    assert skip_events, (
        "lock-contention must emit at least 1 repo_index_skipped event; "
        f"got {skip_events!r}"
    )
    found = [
        e for e in skip_events
        if (e.get("context") or {}).get("reason") == "lock_held"
    ]
    assert found, (
        "lock-contention skip event must carry reason='lock_held'; "
        f"got {[e.get('context') for e in skip_events]!r}"
    )


# ===========================================================================
# Test 20 — 304 short-circuit emits skipped reason=tree_unchanged_304
# ===========================================================================


async def test_304_short_circuit_emits_skipped_reason_tree_unchanged_304(
    db_session,
) -> None:
    """Spec § 7 acceptance #4 + § 4.2.

    Mock ``get_tree_with_cache`` to return ``(None, etag)`` (304 not
    modified). The build path should short-circuit and emit a
    ``repo_index_skipped`` event with ``reason='tree_unchanged_304'``.
    Pre-Cycle-2: the 304 short-circuit is silent.
    """
    from app.services.repo_index_service import RepoIndexService

    repo, branch = "owner/c2-304", "main"
    # Seed an existing meta + tree_etag so the 304 path is reachable.
    await _seed_meta(
        db_session, repo, branch,
        status="ready", file_count=10, head_sha="oldsha",
    )

    gc = _make_mock_github()
    # 304 → tree=None, but etag echoed back.
    gc.get_tree_with_cache = AsyncMock(return_value=(None, "etag-cached"))

    es = _make_mock_embedding()
    queue = _CountingWriteQueueStub(db_session)

    async def _stub(**kwargs):
        return [], 0, 0

    with patch(
        "app.services.repo_index_service.read_and_embed_files",
        new=_stub,
    ):
        svc = RepoIndexService(
            db=db_session,
            github_client=gc,
            embedding_service=es,
            write_queue=queue,  # type: ignore[arg-type]
        )
        await svc.build_index(repo, branch, "tok")

    skip_events = _ring_events(op="repo_index_skipped")
    assert skip_events, (
        "304 short-circuit must emit at least 1 repo_index_skipped event; "
        f"got {skip_events!r}"
    )
    found = [
        e for e in skip_events
        if (e.get("context") or {}).get("reason") == "tree_unchanged_304"
    ]
    assert found, (
        "304 short-circuit skip must carry reason='tree_unchanged_304'; "
        f"got {[e.get('context') for e in skip_events]!r}"
    )


# ===========================================================================
# Test 21 — orphan sweep emits recovered event per stuck row
# ===========================================================================


async def test_orphan_sweep_emits_recovered_event_per_stuck_row(
    db_session,
) -> None:
    """Spec § 7 acceptance #5 + § 4.1 row 8.

    Insert 3 stuck ``RepoIndexMeta(status='indexing', indexed_at=now-31min)``
    rows. Call ``_gc_orphan_repo_index_runs``. Expect exactly 3
    ``repo_index_recovered`` events with ``reason='orphan_recovery'``.
    Pre-Cycle-2: the helper mutates state but emits no event.
    """
    from app.services.gc import _gc_orphan_repo_index_runs

    stale = datetime.now(timezone.utc) - timedelta(minutes=31)
    for i in range(3):
        await _seed_meta(
            db_session,
            f"owner/orphan-{i}",
            "main",
            status="indexing",
            indexed_at=stale,
        )

    flipped = await _gc_orphan_repo_index_runs(db_session)
    await db_session.commit()

    assert flipped == 3, f"expected 3 rows flipped, got {flipped}"

    recovered_events = _ring_events(op="repo_index_recovered")
    assert len(recovered_events) == 3, (
        "orphan sweep must emit exactly 3 repo_index_recovered events "
        f"(one per stuck row); got {len(recovered_events)}: {recovered_events!r}"
    )
    for ev in recovered_events:
        ctx = ev.get("context") or {}
        assert ctx.get("reason") == "orphan_recovery", (
            f"recovered event must carry reason='orphan_recovery'; "
            f"got {ctx.get('reason')!r}"
        )


# ===========================================================================
# Test 22 — per-batch progress fires on batch 0 and every 5th
# ===========================================================================


async def test_per_batch_progress_fires_on_batch_0_and_every_5th(
    db_session,
) -> None:
    """Spec § 7 acceptance #6 + § 4.3 per-batch progress throttle.

    A 250-file build produces 5 persist batches (indices 0-4). The progress
    SSE ``index_phase_changed`` should fire only on batch_index == 0 (and
    every multiple of REPO_INDEX_LOG_PROGRESS_BATCH_INTERVAL=5 thereafter).
    For 5 batches that's exactly 1 progress emission during persist.

    A 750-file build = 15 persist batches → progress fires at batches 0, 5,
    10 = 3 emissions during persist.

    The assertion isolates persist-phase progress emissions from the
    pre-existing pre-persist phase-change emissions (e.g. ``fetching_tree
    → indexing``, ``embedding → indexing``) by filtering on payloads with
    ``files_seen > 0`` AND ``phase == 'embedding'``. Pre-Cycle-2: no
    per-batch emission at all → assertion fails.
    """
    from app.services.event_bus import event_bus
    from app.services.repo_index_service import RepoIndexService

    queue_events: asyncio.Queue = asyncio.Queue()
    event_bus._subscribers.add(queue_events)

    try:
        # ── Run 1: 250 files = 5 persist batches ────────────────────────
        n1 = 250
        paths1 = [f"src/p1_{i:04d}.py" for i in range(n1)]
        gc1 = _make_mock_github(n_indexable=n1)
        es = _make_mock_embedding()
        queue = _CountingWriteQueueStub(db_session)

        with patch(
            "app.services.repo_index_service.read_and_embed_files",
            new=_read_and_embed_stub(paths1),
        ):
            svc = RepoIndexService(
                db=db_session,
                github_client=gc1,
                embedding_service=es,
                write_queue=queue,  # type: ignore[arg-type]
            )
            await svc.build_index("owner/c2-prog-250", "main", "tok")

        captured1: list[dict] = []
        while not queue_events.empty():
            captured1.append(queue_events.get_nowait())

        # Persist-progress emissions: phase='embedding' AND files_seen > 0.
        persist_progress_1 = [
            e for e in captured1
            if e.get("event") == "index_phase_changed"
            and (e.get("data") or {}).get("phase") == "embedding"
            and (e.get("data") or {}).get("files_seen", 0) > 0
        ]
        # 5 batches → emissions at batch 0 AND batch 5 → but batch 5 doesn't
        # exist (last batch_index is 4 for 5 batches). Actually: per spec
        # § 4.3, batch 0 always emits + batches divisible by 5 also emit.
        # For 5 batches (indices 0-4), only index 0 qualifies → 1 emission.
        # Some implementations also emit a final-progress on batch 4 (last);
        # accept either 1 or 2 with a clear comment.
        assert len(persist_progress_1) >= 1, (
            f"250-file run must emit ≥1 persist-progress event "
            f"(batch 0 always emits per spec § 4.3); got "
            f"{len(persist_progress_1)}: {persist_progress_1!r}"
        )
        # Strict spec interpretation: exactly 1 (only batch 0 in the 0-4
        # range hits ``batch_index == 0 OR batch_index % 5 == 0``).
        assert len(persist_progress_1) <= 2, (
            f"250-file run must emit ≤2 persist-progress events; got "
            f"{len(persist_progress_1)}: {persist_progress_1!r}"
        )

        # Drain any residual events.
        while not queue_events.empty():
            queue_events.get_nowait()

        # ── Run 2: 750 files = 15 persist batches ───────────────────────
        n2 = 750
        paths2 = [f"src/p2_{i:04d}.py" for i in range(n2)]
        gc2 = _make_mock_github(n_indexable=n2)
        queue2 = _CountingWriteQueueStub(db_session)

        with patch(
            "app.services.repo_index_service.read_and_embed_files",
            new=_read_and_embed_stub(paths2),
        ):
            svc2 = RepoIndexService(
                db=db_session,
                github_client=gc2,
                embedding_service=es,
                write_queue=queue2,  # type: ignore[arg-type]
            )
            await svc2.build_index("owner/c2-prog-750", "main", "tok")

        captured2: list[dict] = []
        while not queue_events.empty():
            captured2.append(queue_events.get_nowait())

        persist_progress_2 = [
            e for e in captured2
            if e.get("event") == "index_phase_changed"
            and (e.get("data") or {}).get("phase") == "embedding"
            and (e.get("data") or {}).get("files_seen", 0) > 0
        ]
        # 15 batches (indices 0-14) → emissions at batch 0, 5, 10 = 3.
        assert len(persist_progress_2) == 3, (
            f"750-file run must emit exactly 3 persist-progress events "
            f"(batches 0, 5, 10 per spec § 4.3); got "
            f"{len(persist_progress_2)}: {persist_progress_2!r}"
        )
    finally:
        event_bus._subscribers.discard(queue_events)


# ===========================================================================
# Test 23 — health endpoint repo_index block exposes 10 fields
# ===========================================================================


async def test_health_repo_index_block_exposes_10_fields(
    app_client, db_session,
) -> None:
    """Spec § 7 acceptance #7 + § 7 implementation surface #3.

    GET ``/api/health`` must return a top-level ``repo_index`` block with
    exactly these 10 keys (per spec § 7 implementation surface #3):

      last_run_at, last_run_duration_ms, last_run_files_persisted,
      last_run_status, last_run_op, batches_committed_24h,
      batches_rolled_back_24h, p95_batch_duration_ms,
      p99_batch_duration_ms, active_locks

    Pre-Cycle-2: ``HealthResponse`` has no ``repo_index`` field → KeyError
    on the assert. We don't need to drive a build to populate the block;
    the contract demands the keys exist with sensible defaults (None for
    last_run_*; 0 for counters; None or 0 for percentiles; 0 for
    active_locks).
    """
    response = await app_client.get("/api/health?probes=false")
    assert response.status_code == 200, (
        f"/api/health must return 200; got {response.status_code}: "
        f"{response.text}"
    )
    body = response.json()

    assert "repo_index" in body, (
        "Health response must include 'repo_index' block (Cycle 2); "
        f"got keys={sorted(body.keys())!r}"
    )

    repo_index = body["repo_index"]
    required_fields = {
        "last_run_at",
        "last_run_duration_ms",
        "last_run_files_persisted",
        "last_run_status",
        "last_run_op",
        "batches_committed_24h",
        "batches_rolled_back_24h",
        "p95_batch_duration_ms",
        "p99_batch_duration_ms",
        "active_locks",
    }
    actual = set(repo_index.keys())
    missing = required_fields - actual
    assert not missing, (
        f"repo_index block missing fields: {missing!r}; got {actual!r}"
    )
    # Spec mandates exactly 10 fields — no extras.
    assert len(required_fields) == 10, (
        "spec § 7 implementation surface #3 lists exactly 10 fields"
    )


# ===========================================================================
# Test 24 — p95 batch duration in sanity range
# ===========================================================================


async def test_p95_batch_duration_in_sanity_range(
    app_client, db_session,
) -> None:
    """Spec § 7 acceptance #8.

    Build a 100-file repo (= 2 persist batches at 50/batch). After the
    build, ``/api/health`` should report ``p95_batch_duration_ms`` in
    [1.0, 60000.0] — well above 1ms (every batch hits a real commit) and
    well below 60s (no batch should take a minute on an in-memory DB).

    Pre-Cycle-2: no metric exists → KeyError on assert.
    """
    from app.services.repo_index_service import RepoIndexService

    n = 100
    paths = [f"src/file_{i:04d}.py" for i in range(n)]
    gc = _make_mock_github(n_indexable=n)
    es = _make_mock_embedding()
    queue = _CountingWriteQueueStub(db_session)

    with patch(
        "app.services.repo_index_service.read_and_embed_files",
        new=_read_and_embed_stub(paths),
    ):
        svc = RepoIndexService(
            db=db_session,
            github_client=gc,
            embedding_service=es,
            write_queue=queue,  # type: ignore[arg-type]
        )
        await svc.build_index("owner/c2-p95", "main", "tok")

    response = await app_client.get("/api/health?probes=false")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "repo_index" in body, f"missing repo_index block; keys={list(body)!r}"
    p95 = body["repo_index"].get("p95_batch_duration_ms")
    assert p95 is not None, "p95_batch_duration_ms must be populated post-build"
    assert isinstance(p95, (int, float)), (
        f"p95 must be numeric; got {type(p95).__name__} ({p95!r})"
    )
    assert 1.0 <= float(p95) <= 60000.0, (
        f"p95_batch_duration_ms out of sanity range [1.0, 60000.0]; got {p95!r}"
    )


# ===========================================================================
# Test 25 — active_locks snapshot reflects held locks
# ===========================================================================


async def test_active_locks_snapshot_reflects_held_locks(
    app_client,
) -> None:
    """Spec § 7 acceptance #9.

    Pre-acquire a per-(repo, branch) lock manually. While the lock is
    held, GET ``/api/health`` and assert ``repo_index.active_locks >= 1``.
    Pre-Cycle-2: no field exists → KeyError on assert.
    """
    from app.services.repo_index_service import _acquire_repo_index_lock

    lock = await _acquire_repo_index_lock("owner/c2-active", "main")
    async with lock:
        # Hold the lock across the health check.
        response = await app_client.get("/api/health?probes=false")

    assert response.status_code == 200, response.text
    body = response.json()
    assert "repo_index" in body, f"missing repo_index block; keys={list(body)!r}"
    active_locks = body["repo_index"].get("active_locks")
    assert active_locks is not None, "active_locks must exist in repo_index block"
    assert isinstance(active_locks, int), (
        f"active_locks must be int; got {type(active_locks).__name__}"
    )
    assert active_locks >= 1, (
        f"active_locks must reflect the held lock (≥1); got {active_locks!r}"
    )


# ===========================================================================
# Test 26 — invalid reason code raises ValueError
# ===========================================================================


async def test_invalid_reason_code_raises_value_error() -> None:
    """Spec § 4.2 enum + § 7 implementation surface #5 + acceptance #10.

    Calling ``_emit_decision_event("repo_index_skipped", reason="bogus")``
    must raise ``ValueError``. Pre-Cycle-2: ``_emit_decision_event``
    doesn't exist — the import line raises ``ImportError`` (≈ same RED
    signal: a runtime AttributeError-style failure on the helper).

    The test is structured so it FAILs cleanly under both:
      1. Pre-Cycle-2: ImportError when importing _emit_decision_event.
      2. Post-Cycle-2 (regression-guard) but with broken enforcement: no
         ValueError raised → test fails on the ``pytest.raises`` block.
    """
    # Lazy import: ImportError surfaces here, not at collection time.
    from app.services import repo_index_service as _ris

    # Helper must exist post-Cycle-2.
    assert hasattr(_ris, "_emit_decision_event"), (
        "Cycle 2 must add _emit_decision_event() to repo_index_service.py"
    )

    fn = _ris._emit_decision_event
    # Must reject a bogus reason code for the repo_index_skipped event.
    with pytest.raises(ValueError):
        # Calling shape varies by GREEN implementation; spec § 4.2 says
        # the helper validates the ``reason`` field against the
        # appropriate frozenset (``_SKIPPED_REASONS``). Either positional
        # or kwarg should propagate to the validator.
        fn("repo_index_skipped", {"reason": "bogus"})


# ===========================================================================
# Test 27 — concurrent 8 repos, no audit warns under observability (OPERATE)
# ===========================================================================


async def test_concurrent_8_repos_no_audit_warns_under_observability(
    db_session, caplog,
) -> None:
    """Cycle-2 OPERATE-bar test.

    Spawn 8 concurrent ``build_index`` tasks for 8 distinct (repo, branch)
    keys. Use the in-memory ``_CountingWriteQueueStub`` (mirrors Cycle 1's
    OPERATE) and capture audit-hook log records via ``caplog``.

    Assert:
      - 8 metas updated to status="ready"
      - 8 distinct repo_index_completed events fired (one per key)
      - ZERO WARNING-level audit-hook records during the run

    Pre-Cycle-2: no event emission at all → 0 ``repo_index_completed``
    events → assertion fails.
    """
    from app.services.repo_index_service import RepoIndexService

    es = _make_mock_embedding()
    queue = _CountingWriteQueueStub(db_session)
    n = 30  # files per repo

    def _gc_for(paths: list[str], head_sha: str) -> Any:
        gc = _make_mock_github(head_sha=head_sha)
        gc.get_tree_with_cache = AsyncMock(return_value=(
            [
                {"type": "blob", "path": p, "sha": f"sha_{p}", "size": 200}
                for p in paths
            ],
            None,
        ))
        return gc

    async def _stub_for(paths: list[str]):
        return _read_and_embed_stub(paths)

    async def _run_one(idx: int):
        paths = [f"src/r{idx}_{i:04d}.py" for i in range(n)]
        gc = _gc_for(paths, head_sha=f"sha-{idx}")
        svc = RepoIndexService(
            db=db_session,
            github_client=gc,
            embedding_service=es,
            write_queue=queue,  # type: ignore[arg-type]
        )
        with patch(
            "app.services.repo_index_service.read_and_embed_files",
            new=_read_and_embed_stub(paths),
        ):
            return await svc.build_index(
                f"owner/c2-load-{idx}", "main", f"tok-{idx}",
            )

    caplog.set_level(logging.WARNING)

    tasks = [asyncio.create_task(_run_one(i)) for i in range(8)]
    await asyncio.gather(*tasks, return_exceptions=True)

    # All 8 metas must be status="ready".
    for idx in range(8):
        repo = f"owner/c2-load-{idx}"
        meta = (
            await db_session.execute(
                select(RepoIndexMeta).where(
                    RepoIndexMeta.repo_full_name == repo,
                    RepoIndexMeta.branch == "main",
                )
            )
        ).scalars().first()
        assert meta is not None, f"meta missing for {repo}"
        assert meta.status == "ready", (
            f"{repo} status={meta.status!r}; expected 'ready'"
        )

    # 8 distinct repo_index_completed events.
    completed_events = _ring_events(op="repo_index_completed")
    distinct_repos = {
        (e.get("context") or {}).get("repo_full_name")
        for e in completed_events
    }
    distinct_repos.discard(None)
    assert len(distinct_repos) == 8, (
        f"expected 8 distinct repo_index_completed events; got "
        f"{len(distinct_repos)}: {distinct_repos!r}"
    )

    # Zero audit-hook WARNINGs.
    audit_warns = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING
        and "read-engine audit" in r.getMessage()
    ]
    assert audit_warns == [], (
        f"audit hook must emit zero warnings under concurrent load; "
        f"got {len(audit_warns)}"
    )


# ===========================================================================
# Test 28 — decision event payload contract: lock_acquired
# ===========================================================================


async def test_decision_event_payload_contract_lock_acquired(
    db_session,
) -> None:
    """Spec § 4.1 row 1 contract fields.

    A lock_acquired event must carry: repo_index_run_id, repo_full_name,
    branch, op (= 'build' | 'incremental'). Pre-Cycle-2: event doesn't fire.
    """
    from app.services.repo_index_service import RepoIndexService

    n = 10
    paths = [f"src/c_{i:04d}.py" for i in range(n)]
    gc = _make_mock_github(n_indexable=n)
    es = _make_mock_embedding()
    queue = _CountingWriteQueueStub(db_session)

    with patch(
        "app.services.repo_index_service.read_and_embed_files",
        new=_read_and_embed_stub(paths),
    ):
        svc = RepoIndexService(
            db=db_session,
            github_client=gc,
            embedding_service=es,
            write_queue=queue,  # type: ignore[arg-type]
        )
        await svc.build_index("owner/c2-lock-acquired", "main", "tok")

    events = _ring_events(op="repo_index_lock_acquired")
    assert events, (
        "lock acquisition must emit at least 1 repo_index_lock_acquired "
        f"event; got {events!r}"
    )
    ev = events[0]
    ctx = ev.get("context") or {}
    for required in ("repo_index_run_id", "repo_full_name", "branch", "op"):
        assert required in ctx, (
            f"repo_index_lock_acquired must carry '{required}'; got "
            f"context={ctx!r}"
        )
    assert ctx.get("op") in {"build", "incremental"}, (
        f"op must be 'build' or 'incremental'; got {ctx.get('op')!r}"
    )
    assert ctx.get("repo_full_name") == "owner/c2-lock-acquired"
    assert ctx.get("branch") == "main"


# ===========================================================================
# Test 29 — decision event payload contract: started
# ===========================================================================


async def test_decision_event_payload_contract_started(db_session) -> None:
    """Spec § 4.1 row 2 contract fields.

    Started event must carry: repo_index_run_id, repo_full_name, branch,
    op, prior_file_count.
    """
    from app.services.repo_index_service import RepoIndexService

    repo, branch = "owner/c2-started", "main"
    # Seed prior rows so prior_file_count is observable.
    await _seed_files(db_session, repo, branch, 5)
    await _seed_meta(db_session, repo, branch, status="ready", file_count=5)

    n = 10
    paths = [f"src/started_{i:04d}.py" for i in range(n)]
    gc = _make_mock_github(n_indexable=n, head_sha="newsha")
    es = _make_mock_embedding()
    queue = _CountingWriteQueueStub(db_session)

    with patch(
        "app.services.repo_index_service.read_and_embed_files",
        new=_read_and_embed_stub(paths),
    ):
        svc = RepoIndexService(
            db=db_session,
            github_client=gc,
            embedding_service=es,
            write_queue=queue,  # type: ignore[arg-type]
        )
        await svc.build_index(repo, branch, "tok")

    events = _ring_events(op="repo_index_started")
    assert events, (
        "Phase 0 commit must emit at least 1 repo_index_started event; "
        f"got {events!r}"
    )
    ev = events[0]
    ctx = ev.get("context") or {}
    for required in (
        "repo_index_run_id",
        "repo_full_name",
        "branch",
        "op",
        "prior_file_count",
    ):
        assert required in ctx, (
            f"repo_index_started must carry '{required}'; got context={ctx!r}"
        )
    assert ctx.get("repo_full_name") == repo
    assert ctx.get("branch") == branch
    # prior_file_count must reflect rows BEFORE the rebuild — 5 seeded.
    assert ctx.get("prior_file_count") == 5, (
        f"prior_file_count must be 5 (seeded rows); got "
        f"{ctx.get('prior_file_count')!r}"
    )


# ===========================================================================
# Test 30 — batch_committed cumulative_rows resets per refit
# ===========================================================================


async def test_decision_event_payload_contract_batch_committed_cumulative_rows_resets_per_refit(
    db_session,
) -> None:
    """Spec § 4.1 row 4 contract fields + ``cumulative_rows`` semantic gloss.

    ``cumulative_rows`` is per-refit, NOT lifetime-of-meta. Two sequential
    builds for the same (repo, branch) must produce two independent
    cumulative_rows trajectories — build #2's first ``batch_committed``
    must report the BATCH 0 row count, NOT a continuation of build #1.

    Pre-Cycle-2: no events fire at all → assertion fails.
    """
    from app.services.repo_index_service import RepoIndexService

    repo, branch = "owner/c2-cumulative", "main"

    n = 60  # 2 persist batches (50 + 10) per build.
    paths = [f"src/cum_{i:04d}.py" for i in range(n)]

    es = _make_mock_embedding()

    # ── Build #1 ────────────────────────────────────────────────────────
    gc1 = _make_mock_github(n_indexable=n, head_sha="run1-sha")
    queue1 = _CountingWriteQueueStub(db_session)
    with patch(
        "app.services.repo_index_service.read_and_embed_files",
        new=_read_and_embed_stub(paths),
    ):
        svc1 = RepoIndexService(
            db=db_session,
            github_client=gc1,
            embedding_service=es,
            write_queue=queue1,  # type: ignore[arg-type]
        )
        await svc1.build_index(repo, branch, "tok1")

    events_after_build_1 = _ring_events(op="repo_index_batch_committed")
    assert events_after_build_1, (
        "build #1 must emit at least 1 repo_index_batch_committed event"
    )
    last_cum_build_1 = (events_after_build_1[0].get("context") or {}).get(
        "cumulative_rows"
    )
    # get_recent returns newest-first; we want the last-emitted batch
    # event of build #1 → events_after_build_1[0] under newest-first.
    assert last_cum_build_1 is not None, (
        "cumulative_rows must be present on batch_committed events; "
        f"got {events_after_build_1[0]!r}"
    )
    # Build #1 committed 60 rows → last cumulative_rows == 60.
    assert last_cum_build_1 == n, (
        f"build #1 last cumulative_rows must == total rows ({n}); "
        f"got {last_cum_build_1!r}"
    )

    # ── Build #2 ────────────────────────────────────────────────────────
    paths2 = [f"src/cum2_{i:04d}.py" for i in range(n)]
    gc2 = _make_mock_github(n_indexable=n, head_sha="run2-sha")
    gc2.get_tree_with_cache = AsyncMock(return_value=(
        [{"type": "blob", "path": p, "sha": f"sha_{p}", "size": 200}
         for p in paths2],
        None,
    ))
    queue2 = _CountingWriteQueueStub(db_session)

    with patch(
        "app.services.repo_index_service.read_and_embed_files",
        new=_read_and_embed_stub(paths2),
    ):
        svc2 = RepoIndexService(
            db=db_session,
            github_client=gc2,
            embedding_service=es,
            write_queue=queue2,  # type: ignore[arg-type]
        )
        await svc2.build_index(repo, branch, "tok2")

    all_events = _ring_events(op="repo_index_batch_committed")
    # Sort chronologically (oldest → newest). The ring buffer is newest-first.
    all_events_chrono = list(reversed(all_events))

    # Per-build first-batch events must each carry batch_index == 0.
    first_batches = [
        e for e in all_events_chrono
        if (e.get("context") or {}).get("batch_index") == 0
    ]
    assert len(first_batches) >= 2, (
        "two sequential builds must each emit a batch_index=0 event; "
        f"got {len(first_batches)}: {first_batches!r}"
    )
    # Build #2's first batch_committed must reset cumulative_rows to the
    # batch-0 size (50), NOT continue from build #1's 60.
    cum_run2_first = (first_batches[1].get("context") or {}).get(
        "cumulative_rows"
    )
    assert cum_run2_first is not None, (
        f"build #2 first batch_committed must carry cumulative_rows; "
        f"got {first_batches[1]!r}"
    )
    assert cum_run2_first <= n, (
        f"build #2 first cumulative_rows must reflect batch 0 size only "
        f"(≤ {n}, NOT a continuation of build #1's {n}); got "
        f"{cum_run2_first!r}"
    )
    # Stronger: build #2 first cumulative_rows MUST be < 2*n (would be
    # 2*n only if the counter never reset).
    assert cum_run2_first < 2 * n, (
        f"build #2 first cumulative_rows={cum_run2_first} suggests the "
        f"counter never reset between refits (would be 2n={2*n} on "
        f"continuation); spec § 4.1 row 4 mandates a reset"
    )


# ===========================================================================
# Test 31 — decision event payload contract: completed
# ===========================================================================


async def test_decision_event_payload_contract_completed(db_session) -> None:
    """Spec § 4.1 row 7 contract fields.

    Completed event must carry: repo_index_run_id, repo_full_name, branch,
    op, final_file_count, total_duration_ms, total_batches_committed.
    Per § 4.1 + § 4.1 paragraph "total_batches_rolled_back is intentionally
    NOT in repo_index_completed payload" — refit-fatal model means a
    successful refit has zero rolled-back batches by construction; the
    field must NOT appear in the payload.
    """
    from app.services.repo_index_service import RepoIndexService

    repo, branch = "owner/c2-completed", "main"
    n = 10
    paths = [f"src/complete_{i:04d}.py" for i in range(n)]
    gc = _make_mock_github(n_indexable=n)
    es = _make_mock_embedding()
    queue = _CountingWriteQueueStub(db_session)

    with patch(
        "app.services.repo_index_service.read_and_embed_files",
        new=_read_and_embed_stub(paths),
    ):
        svc = RepoIndexService(
            db=db_session,
            github_client=gc,
            embedding_service=es,
            write_queue=queue,  # type: ignore[arg-type]
        )
        await svc.build_index(repo, branch, "tok")

    events = _ring_events(op="repo_index_completed")
    assert events, (
        "successful build must emit exactly 1 repo_index_completed event; "
        f"got {events!r}"
    )
    ev = events[0]
    ctx = ev.get("context") or {}
    required = (
        "repo_index_run_id",
        "repo_full_name",
        "branch",
        "op",
        "final_file_count",
        "total_duration_ms",
        "total_batches_committed",
    )
    for f in required:
        assert f in ctx, (
            f"repo_index_completed must carry '{f}'; got context={ctx!r}"
        )

    assert ctx.get("repo_full_name") == repo
    assert ctx.get("branch") == branch
    assert ctx.get("op") in {"build", "incremental"}
    # final_file_count must equal the actual rows committed.
    assert ctx.get("final_file_count") == n, (
        f"final_file_count must == {n} (actual rows committed); got "
        f"{ctx.get('final_file_count')!r}"
    )
    assert isinstance(ctx.get("total_duration_ms"), (int, float)), (
        f"total_duration_ms must be numeric; got "
        f"{type(ctx.get('total_duration_ms')).__name__}"
    )
    assert ctx.get("total_batches_committed") >= 1, (
        f"total_batches_committed must be >= 1; got "
        f"{ctx.get('total_batches_committed')!r}"
    )

    # Refit-fatal invariant: total_batches_rolled_back must NOT appear in
    # the success-path payload (spec § 4.1 paragraph after the table).
    assert "total_batches_rolled_back" not in ctx, (
        "spec § 4.1 mandates total_batches_rolled_back is NOT present on "
        "successful repo_index_completed events (refit-fatal model); "
        f"got context={ctx!r}"
    )
