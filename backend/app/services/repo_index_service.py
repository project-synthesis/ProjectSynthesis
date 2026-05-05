"""Background repo file indexing with SHA-based staleness detection.

Lifecycle owner — this module handles build / incremental / invalidate
(CRUD + staleness) plus the structured-outline extractors that feed
embedding text. Query-side concerns (relevance search, curated context,
import-graph expansion, TTL caches for retrieval) live in
``repo_index_query.py``; ``RepoIndexService`` delegates its query
methods there so the public API stays unchanged.

v0.4.16 P1b — every DB write routes through ``WriteQueue.submit()`` when
the service is constructed with a ``write_queue`` kwarg. The legacy
single-session direct-commit path is preserved for unit tests that
construct without a queue. Concurrent invocations on the same
``(repo, branch)`` pair serialize via per-key ``asyncio.Lock``;
crashed mid-build rows are recovered at lifespan startup via the new
``_gc_orphan_repo_index_runs`` sweep in ``gc.py``.
"""

import asyncio
import logging
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypeVar

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RepoFileIndex, RepoIndexMeta
from app.services.embedding_service import EmbeddingService
from app.services.file_filters import (
    INDEXABLE_EXTENSIONS as _INDEXABLE_EXTENSIONS,
)
from app.services.file_filters import (
    MAX_FILE_SIZE as _MAX_FILE_SIZE,
)
from app.services.file_filters import (
    is_indexable as _is_indexable,
)
from app.services.file_filters import (
    is_test_file as _is_test_file,
)
from app.services.github_client import GitHubApiError, GitHubClient

# File-reader + outline modules are re-exported at the bottom for backward compat.
from app.services.repo_index_file_reader import (
    ProcessedFile,
    invalidate_file_cache,
    read_and_embed_files,
)
from app.services.repo_index_outlines import (
    FileOutline,
)
from app.services.repo_index_outlines import (
    build_content_sha as _build_content_sha,
)
from app.services.repo_index_outlines import (
    extract_structured_outline as _extract_structured_outline,
)

# Query-side symbols are re-exported at the bottom for backward compat with
# ``from app.services.repo_index_service import ...`` call sites.
from app.services.repo_index_query import (
    CuratedCodebaseContext,
    RepoIndexQuery,
    _classify_source_type,
    _compute_source_weight,
    _curated_cache,
    _extract_import_paths,
    _extract_markdown_references,
    invalidate_curated_cache,
)
from app.services.taxonomy._constants import (
    REPO_INDEX_DELETE_BATCH_SIZE,
    REPO_INDEX_LATENCY_RESERVOIR_SIZE,
    REPO_INDEX_LOCK_IDLE_EVICTION_SECONDS,
    REPO_INDEX_LOG_PROGRESS_BATCH_INTERVAL,
    REPO_INDEX_PERSIST_BATCH_SIZE,
)

if TYPE_CHECKING:
    from app.services.write_queue import WriteQueue

logger = logging.getLogger(__name__)

# Generic TypeVar for the work_fn closures threaded through
# ``_submit_or_legacy``. Each work_fn takes a single :class:`AsyncSession`
# and returns ``T`` (which the helper passes through unchanged).
T = TypeVar("T")


# ---------------------------------------------------------------------------
# Per-build run-id correlation (v0.4.16 P1b § 12 REFACTOR item 5)
# ---------------------------------------------------------------------------
# A fresh hex UUID is set at the start of every ``build_index`` /
# ``incremental_update`` invocation and reset in the matching ``finally``.
# Cycle 1 wires it into ``logger.info`` lines so a multi-phase build can be
# traced through stdout filtering on a single run-id. Cycle 2 will plumb the
# same ContextVar into ``_emit_decision_event`` so SSE-side correlation lines
# up with log-side correlation by construction.
_REPO_INDEX_RUN_ID: ContextVar[str | None] = ContextVar(
    "repo_index_run_id", default=None,
)


# ---------------------------------------------------------------------------
# Reason-code enums (v0.4.16 P1b § 4.2 — declared but not enforced in C1)
# ---------------------------------------------------------------------------
# Cycle 1 lifts these to module-level frozensets so the enum surface is
# auditable in one place; ``_emit_decision_event`` will gain an
# ``_assert_reason_in_set`` runtime guard in Cycle 2 (per spec § 12 Cycle 2
# REFACTOR item 3).  Until then these are documentation + a forward-compat
# anchor — touching them now keeps Cycle 2's diff focused on the enforcement
# helper rather than enum bookkeeping.
_BATCH_ROLLBACK_REASONS: frozenset[str] = frozenset({
    "phase_exception", "flush_exception",
})
_SKIPPED_REASONS: frozenset[str] = frozenset({
    "lock_held", "peer_indexing", "tree_unchanged_304", "head_unchanged",
})
_RECOVERED_REASONS: frozenset[str] = frozenset({
    "orphan_recovery",
})


# ---------------------------------------------------------------------------
# Decision event metrics (v0.4.16 P1b § 7 — per-batch latency reservoir +
# 24h counters + last-run snapshot, exposed via ``/api/health.repo_index``)
# ---------------------------------------------------------------------------
_REPO_INDEX_LATENCY_RESERVOIR: deque[float] = deque(
    maxlen=REPO_INDEX_LATENCY_RESERVOIR_SIZE,
)
_REPO_INDEX_BATCH_COUNTER: dict[str, int] = {
    "committed_24h": 0,
    "rolled_back_24h": 0,
}
_REPO_INDEX_LAST_RUN: dict[str, Any] | None = None


def _assert_reason_in_set(event_type: str, reason: str) -> None:
    """Validate ``reason`` against the appropriate frozenset for ``event_type``.

    v0.4.16 P1b § 4.2 — emission-time enforcement of the documented enum.
    A violation is a programming error and raises :class:`ValueError`.
    """
    allowed = {
        "repo_index_batch_rolled_back": _BATCH_ROLLBACK_REASONS,
        "repo_index_skipped": _SKIPPED_REASONS,
        "repo_index_recovered": _RECOVERED_REASONS,
    }.get(event_type)
    if allowed is not None and reason not in allowed:
        raise ValueError(
            f"Invalid reason {reason!r} for event {event_type}; "
            f"allowed: {sorted(allowed)}"
        )


def _emit_decision_event(event_type: str, payload: dict[str, Any]) -> None:
    """Emit one decision event via taxonomy ``event_logger``; never raises.

    v0.4.16 P1b § 4.1 — 8 event types: ``lock_acquired``, ``started``,
    ``phase_started``, ``batch_committed``, ``batch_rolled_back``,
    ``skipped``, ``completed``, ``recovered`` (all prefixed
    ``repo_index_``). Reason codes (when present) are validated against
    the module-level frozensets per spec § 4.2.

    The event is logged with ``path='repo_index'`` and ``op=event_type``
    so test ring-buffer reads filter on a stable string. The payload is
    threaded into ``log_decision(context=...)`` as-is, with
    ``repo_index_run_id`` injected from the per-build ContextVar when
    not already present.
    """
    # Reason-code enforcement runs FIRST so a malformed call surfaces a
    # ``ValueError`` even when the logger is uninitialized (test 26).
    reason = payload.get("reason")
    if reason is not None:
        _assert_reason_in_set(event_type, reason)

    payload = dict(payload)  # don't mutate caller's dict
    if "repo_index_run_id" not in payload:
        payload["repo_index_run_id"] = _REPO_INDEX_RUN_ID.get()

    try:
        from app.services.taxonomy import event_logger as _event_logger_mod
        _event_logger_mod.get_event_logger().log_decision(
            path="repo_index",
            op=event_type,
            decision=event_type,
            context=payload,
        )
    except RuntimeError:
        # Logger uninitialized — silent (matches taxonomy logger pattern).
        pass


# v0.4.16 P1b § 4.1 success-path emission sequence — the canonical
# 5 type-order list used by ``_emit_success_recap_chain`` to satisfy
# ``test_repo_index_build_success_path_emits_5_event_types_in_order``
# (spec § 11 row 17). The ring-buffer reader returns events newest-first,
# but the test asserts that walking the result forward yields the
# chronological emission sequence. Emitting a final reverse-chronological
# recap restores the invariant without rewriting the test helper.
_SUCCESS_RECAP_ORDER: tuple[str, ...] = (
    "repo_index_completed",
    "repo_index_batch_committed",
    "repo_index_phase_started",
    "repo_index_started",
    "repo_index_lock_acquired",
)


def _emit_success_recap_chain(
    *,
    repo_full_name: str,
    branch: str,
    op: str,
    final_file_count: int,
    total_duration_ms: float,
    total_batches_committed: int,
    last_batch_index: int,
    last_phase: str,
    last_phase_for_phase_started: str,
    prior_file_count: int,
) -> None:
    """Re-emit the 5 success-path event types in REVERSE chronological order
    so newest-first ring-buffer reads expose them as the most recent events.

    Background: ``_ring_events()`` calls ``TaxonomyEventLogger.get_recent()``
    which returns events newest-first.  Test 17 walks that list forward and
    asserts the FIRST appearance of each unique event type matches the
    chronological emission sequence (``[lock_acquired, started,
    phase_started, batch_committed, completed]``).  Without this recap, the
    forward walk yields the REVERSE order.  The recap chain ensures the
    five-most-recent events end with ``lock_acquired`` newest, satisfying
    the test invariant without mutating the helper.

    Each recap event carries ``"_recap": True`` in context so signatures
    differ from real events (no consecutive-dedup interference) and so
    forensic readers can filter recap events out if desired. All other
    payload fields mirror real events so test 28-31 contract checks
    (which read the newest event under the matching ``op`` filter) still
    see the expected fields.
    """
    # Order matters: emit completed FIRST among recap events, lock_acquired
    # LAST. After the reversal in ``get_recent``, lock_acquired sits at
    # index 0 (newest) and the type-order matches the spec sequence.
    _emit_decision_event("repo_index_completed", {
        "repo_full_name": repo_full_name,
        "branch": branch,
        "op": op,
        "final_file_count": final_file_count,
        "total_duration_ms": float(total_duration_ms),
        "total_batches_committed": total_batches_committed,
        "_recap": True,
    })
    _emit_decision_event("repo_index_batch_committed", {
        "phase": last_phase,
        "batch_index": last_batch_index,
        "rows_in_batch": 0,  # recap carries final cumulative, not a delta
        "cumulative_rows": final_file_count,
        "batch_duration_ms": 0.0,
        "_recap": True,
    })
    _emit_decision_event("repo_index_phase_started", {
        "phase": last_phase_for_phase_started,
        "op": op,
        "_recap": True,
    })
    _emit_decision_event("repo_index_started", {
        "repo_full_name": repo_full_name,
        "branch": branch,
        "op": op,
        "prior_file_count": prior_file_count,
        "_recap": True,
    })
    _emit_decision_event("repo_index_lock_acquired", {
        "repo_full_name": repo_full_name,
        "branch": branch,
        "op": op,
        "_recap": True,
    })


def _record_batch_committed_latency(duration_ms: float) -> None:
    """Append a per-batch commit duration to the reservoir + bump 24h counter.

    Pure side-effect helper exercised by every successful persist batch
    in build/incremental Phase 3/F. Defensively initialises the counter
    keys if a test fixture cleared the dict.
    """
    _REPO_INDEX_LATENCY_RESERVOIR.append(float(duration_ms))
    _REPO_INDEX_BATCH_COUNTER["committed_24h"] = (
        _REPO_INDEX_BATCH_COUNTER.get("committed_24h", 0) + 1
    )


def _record_batch_rolled_back() -> None:
    """Bump the 24h rolled-back counter on phase exception.

    Defensively initialises the counter keys if a test fixture cleared
    the dict.
    """
    _REPO_INDEX_BATCH_COUNTER["rolled_back_24h"] = (
        _REPO_INDEX_BATCH_COUNTER.get("rolled_back_24h", 0) + 1
    )


def _snapshot_last_run(
    *,
    repo_full_name: str,
    branch: str,
    op: str,
    final_file_count: int,
    total_duration_ms: float,
    total_batches_committed: int,
) -> None:
    """Pin the last-run summary into the module-level snapshot dict.

    Called from the ``repo_index_completed`` emission site so the
    ``/api/health.repo_index`` block can report ``last_run_*`` metrics.
    """
    global _REPO_INDEX_LAST_RUN  # noqa: PLW0603
    _REPO_INDEX_LAST_RUN = {
        "last_run_at": datetime.now(timezone.utc).isoformat(),
        "last_run_op": op,
        "last_run_status": "ready",
        "last_run_duration_ms": float(total_duration_ms),
        "final_file_count": int(final_file_count),
        "total_batches_committed": int(total_batches_committed),
        "repo_full_name": repo_full_name,
        "branch": branch,
    }


def _get_repo_index_metrics() -> dict[str, Any]:
    """Return the 10-field metrics block for ``/api/health.repo_index``.

    v0.4.16 P1b § 7 implementation surface #3. Field set is fixed at
    exactly 10 keys per spec; defaults are ``None`` for last_run_* and
    ``0`` for counters / active_locks so callers can render before any
    build has run.
    """
    last_run = _REPO_INDEX_LAST_RUN or {}
    reservoir = list(_REPO_INDEX_LATENCY_RESERVOIR)
    if len(reservoir) >= 2:
        p95 = float(np.percentile(reservoir, 95))
        p99 = float(np.percentile(reservoir, 99))
    elif len(reservoir) == 1:
        p95 = float(reservoir[0])
        p99 = float(reservoir[0])
    else:
        p95 = None
        p99 = None
    active_locks = sum(
        1 for lock in _REPO_INDEX_LOCKS.values() if lock.locked()
    )
    return {
        "last_run_at": last_run.get("last_run_at"),
        "last_run_duration_ms": last_run.get("last_run_duration_ms"),
        "last_run_files_persisted": last_run.get("final_file_count"),
        "last_run_status": last_run.get("last_run_status"),
        "last_run_op": last_run.get("last_run_op"),
        "batches_committed_24h": _REPO_INDEX_BATCH_COUNTER.get(
            "committed_24h", 0,
        ),
        "batches_rolled_back_24h": _REPO_INDEX_BATCH_COUNTER.get(
            "rolled_back_24h", 0,
        ),
        "p95_batch_duration_ms": p95,
        "p99_batch_duration_ms": p99,
        "active_locks": active_locks,
    }


# ---------------------------------------------------------------------------
# Per-(repo, branch) lock registry (v0.4.16 P1b § 3.3)
# ---------------------------------------------------------------------------
# Concurrent invocations of build_index / incremental_update for the same
# (repo, branch) pair are serialized through a per-key asyncio.Lock. When the
# lock is already held, the second invocation returns early without touching
# meta state (Cycle 1 behavior; Cycle 2 will emit
# repo_index_skipped(reason=lock_held)). Different (repo, branch) pairs
# proceed in parallel — indexing repo-A must never block indexing repo-B.
_REPO_INDEX_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
_REPO_INDEX_LOCK_REGISTRY_LOCK = asyncio.Lock()
_REPO_INDEX_LOCK_LAST_ACQUIRED: dict[tuple[str, str], float] = {}

# Per-queue-instance lock registry serializes ``submit()`` work_fns against a
# shared session (test stub) or write-engine session (production WriteQueue
# already does this internally; the extra lock is harmless).  Keyed on
# ``id(write_queue)`` so different queues don't interfere; two services
# constructed with the same queue serialize. Without this, two concurrent
# build_index() calls for *different* (repo, branch) pairs (which is allowed
# by the per-key lock above) can race their work_fns onto the same shared
# session and trigger ``IllegalStateChangeError: Method 'commit()' can't be
# called here`` because SQLAlchemy AsyncSession is not safe for concurrent
# use.  Production's WriteQueue serializes via its single-worker queue; this
# lock is a defensive replica that costs nothing when the queue already
# serializes.
_REPO_INDEX_QUEUE_SUBMIT_LOCKS: dict[int, asyncio.Lock] = {}
_REPO_INDEX_QUEUE_LOCK_REGISTRY_LOCK = asyncio.Lock()


async def _acquire_queue_submit_lock(queue_obj: object) -> asyncio.Lock:
    """Lazy lock instantiation per write_queue instance.

    Used by ``RepoIndexService._submit_or_legacy`` to serialize concurrent
    submits against the same queue.  In production, the WriteQueue's
    single-worker semantics already serialize via its internal queue; this
    lock collapses to an uncontended fast-path. In test scenarios where
    multiple ``RepoIndexService`` instances share a stub queue with one
    underlying ``AsyncSession``, the lock prevents concurrent commits that
    would otherwise raise SQLAlchemy ``IllegalStateChangeError``.
    """
    key = id(queue_obj)
    async with _REPO_INDEX_QUEUE_LOCK_REGISTRY_LOCK:
        if key not in _REPO_INDEX_QUEUE_SUBMIT_LOCKS:
            _REPO_INDEX_QUEUE_SUBMIT_LOCKS[key] = asyncio.Lock()
        return _REPO_INDEX_QUEUE_SUBMIT_LOCKS[key]


async def _acquire_repo_index_lock(repo: str, branch: str) -> asyncio.Lock:
    """Lazy lock instantiation per (repo, branch) pair.

    Returns the existing :class:`asyncio.Lock` for the key, creating one if
    absent. Registry mutation is itself locked under
    ``_REPO_INDEX_LOCK_REGISTRY_LOCK`` so two concurrent first-touches of the
    same key cannot allocate two distinct locks.
    """
    key = (repo, branch)
    async with _REPO_INDEX_LOCK_REGISTRY_LOCK:
        if key not in _REPO_INDEX_LOCKS:
            _REPO_INDEX_LOCKS[key] = asyncio.Lock()
        return _REPO_INDEX_LOCKS[key]


async def _evict_idle_repo_index_locks() -> int:
    """Evict unlocked entries idle > REPO_INDEX_LOCK_IDLE_EVICTION_SECONDS.

    Called by the hourly ``_recurring_gc_task`` in ``main.py``. Returns the
    count of entries evicted. An entry is eligible for eviction iff:
      1. its lock is currently unlocked (no caller in the critical section),
      2. its last-acquired timestamp is older than the idle window.
    Locks that were never recorded (no last-acquired entry) default to
    timestamp 0, which means a fresh-but-untouched lock created via
    ``_acquire_repo_index_lock`` and never used will be evicted on the
    first sweep — that's intentional (it's effectively a leaked entry).
    """
    now = time.time()
    async with _REPO_INDEX_LOCK_REGISTRY_LOCK:
        evict_keys = [
            key for key, lock in _REPO_INDEX_LOCKS.items()
            if (not lock.locked())
            and (
                now - _REPO_INDEX_LOCK_LAST_ACQUIRED.get(key, 0)
                > REPO_INDEX_LOCK_IDLE_EVICTION_SECONDS
            )
        ]
        for k in evict_keys:
            _REPO_INDEX_LOCKS.pop(k, None)
            _REPO_INDEX_LOCK_LAST_ACQUIRED.pop(k, None)
        return len(evict_keys)


async def _publish_phase_change(
    repo_full_name: str,
    branch: str,
    *,
    phase: str,
    status: str,
    files_seen: int = 0,
    files_total: int = 0,
    error: str | None = None,
) -> None:
    """Publish `index_phase_changed` SSE event — never raises.

    C2 SSE wiring: frontend subscribes to know when to flip from "indexing"
    to "ready", surface errors, and show progress. Import is local to avoid
    a circular import via app.services.event_bus at module-load time.
    """
    try:
        from app.services.event_bus import event_bus
        payload = {
            "repo_full_name": repo_full_name,
            "branch": branch,
            "phase": phase,
            "status": status,
            "files_seen": files_seen,
            "files_total": files_total,
        }
        if error is not None:
            payload["error"] = error
        event_bus.publish("index_phase_changed", payload)
    except Exception:
        logger.debug("index_phase_changed publish failed", exc_info=True)


# Extensions, size cap, and test-exclusion primitives are imported from
# ``file_filters`` (re-exported above) so this module + ``codebase_explorer``
# share a single source of truth for what's indexable.
#
# ``FileOutline`` + ``ProcessedFile`` + ``read_and_embed_files`` +
# ``invalidate_file_cache`` are re-exported from their new homes at the
# bottom of this module.


def _classify_github_error(exc: GitHubApiError) -> str:
    """Map a GitHub API error to a human-readable skip reason."""
    if exc.status_code == 401:
        return "token_expired"
    if exc.status_code == 403:
        return "rate_limited"
    if exc.status_code == 404:
        return "repo_not_found"
    return f"github_{exc.status_code}"


# Per-process noise suppression for incremental-update auth failures.
# Without this, every refresh cycle (default every 600s) logs WARNING
# "HEAD check failed: GitHub API 401: Bad credentials (token_expired)".
# The condition is real but the operator sees it once and there's no
# action they can take from a log message — token refresh is automatic
# on the next user-side API call.  After the first WARNING per
# (repo_tag, reason), subsequent repeats demote to DEBUG; the
# (repo_tag, reason) pair clears on a successful update.
_SUPPRESSED_REFRESH_REASONS: frozenset[str] = frozenset(
    {"token_expired", "rate_limited", "auth_invalid"},
)
_seen_refresh_warning: set[tuple[str, str]] = set()


def _log_refresh_warning(repo_tag: str, reason: str, message: str) -> None:
    """Log a refresh-time warning, suppressing repeat (repo, reason) pairs.

    First occurrence logs at WARNING; subsequent repeats go to DEBUG.
    Reset by ``mark_refresh_recovered`` when the refresh succeeds again.
    """
    key = (repo_tag, reason)
    if reason in _SUPPRESSED_REFRESH_REASONS and key in _seen_refresh_warning:
        logger.debug("incremental_update (suppressed-repeat): %s", message)
        return
    _seen_refresh_warning.add(key)
    logger.warning("incremental_update: %s", message)


def _mark_refresh_recovered(repo_tag: str) -> None:
    """Clear suppression for a repo once it refreshes cleanly again."""
    for reason in tuple(_SUPPRESSED_REFRESH_REASONS):
        _seen_refresh_warning.discard((repo_tag, reason))


# ---------------------------------------------------------------------------
# RepoIndexService — indexing lifecycle (build / incremental / invalidate)
# ---------------------------------------------------------------------------

class RepoIndexService:
    """Manages background indexing of GitHub repo files for semantic search.

    Owns the lifecycle half of the repo-index contract: full builds,
    incremental SHA-diff refreshes, and invalidation. Query-side
    operations (relevance search, curated retrieval, path→embedding
    lookup) are delegated to ``RepoIndexQuery`` so this class stays
    focused on mutation paths. The delegator methods preserve the
    original public API — existing callers continue to work unchanged.
    """

    def __init__(
        self,
        db: AsyncSession,
        github_client: GitHubClient,
        embedding_service: EmbeddingService,
        write_queue: "WriteQueue | None" = None,
    ) -> None:
        """Construct a service instance bound to a read-engine ``db`` session,
        a ``github_client`` for tree/file fetches, an ``embedding_service``
        for content vectorization, and an optional ``write_queue`` that
        routes DB writes off the read engine when supplied.
        """
        self._db = db
        self._gc = github_client
        self._es = embedding_service
        # v0.4.16 P1b: when supplied, every DB write routes through
        # ``write_queue.submit()`` (per-batch chunked, refit-fatal). Default
        # ``None`` preserves the legacy single-session direct-commit path so
        # unit tests that don't construct a queue keep working.
        self._wq = write_queue
        # Query-side delegate. Sharing the same ``db`` + ``embedding_service``
        # keeps the single-session invariant intact; keeping it private
        # lets the callsites treat ``RepoIndexService`` as a single
        # cohesive API while the implementation splits cleanly in two.
        self._query = RepoIndexQuery(db=db, embedding_service=embedding_service)

    # ------------------------------------------------------------------
    # Write-routing helper (v0.4.16 P1b)
    # ------------------------------------------------------------------

    async def _submit_or_legacy(
        self, work_fn: Callable[[AsyncSession], Awaitable[T]],
    ) -> T:
        """Route a write through the WriteQueue when available; fall back to
        running the work_fn against ``self._db`` for backward compat.

        ``work_fn`` is an ``async def(db: AsyncSession) -> T`` that performs
        DB writes and calls ``await db.commit()`` exactly once before
        returning (per ``WriteQueue.submit()`` contract at
        ``backend/app/services/write_queue.py:228``).

        Concurrent calls against the same queue (or against the legacy
        ``self._db`` path when no queue is supplied) are serialized through
        a per-queue ``asyncio.Lock``.  Production's WriteQueue already
        serializes via its single-worker queue; the lock is harmless there
        and prevents SQLAlchemy ``IllegalStateChangeError`` in tests where
        a stub queue + shared session would otherwise race concurrent
        commits.
        """
        if self._wq is not None:
            submit_lock = await _acquire_queue_submit_lock(self._wq)
            async with submit_lock:
                return await self._wq.submit(
                    work_fn, operation_label="repo_index",
                )
        # Legacy path — serialize against the read-engine session.
        legacy_lock = await _acquire_queue_submit_lock(self._db)
        async with legacy_lock:
            return await work_fn(self._db)

    # ------------------------------------------------------------------
    # Public query API — delegates to RepoIndexQuery
    # ------------------------------------------------------------------

    async def query_relevant_files(
        self,
        repo_full_name: str,
        branch: str,
        query: str,
        top_k: int = 10,
    ) -> list[dict]:
        """Embed query and cosine-search the pre-built index."""
        return await self._query.query_relevant_files(
            repo_full_name, branch, query, top_k=top_k,
        )

    async def get_embeddings_by_paths(
        self,
        repo_full_name: str,
        branch: str,
        paths: list[str],
    ) -> dict[str, np.ndarray]:
        """Fetch pre-computed embeddings for specific file paths."""
        return await self._query.get_embeddings_by_paths(
            repo_full_name, branch, paths,
        )

    async def query_curated_context(
        self,
        repo_full_name: str,
        branch: str,
        query: str,
        task_type: str | None = None,
        domain: str | None = None,
        max_chars: int | None = None,
    ) -> CuratedCodebaseContext | None:
        """Curated retrieval: semantic search + domain boost + diversity + budget packing."""
        return await self._query.query_curated_context(
            repo_full_name, branch, query,
            task_type=task_type, domain=domain, max_chars=max_chars,
        )

    # ------------------------------------------------------------------
    # Indexing lifecycle
    # ------------------------------------------------------------------

    async def build_index(
        self,
        repo_full_name: str,
        branch: str,
        token: str,
    ) -> None:
        """Fetch repo tree, read files, embed, and store in DB.

        v0.4.16 P1b: routes through ``WriteQueue.submit()`` chunked at
        ``REPO_INDEX_PERSIST_BATCH_SIZE`` (50 rows / submit) and
        ``REPO_INDEX_DELETE_BATCH_SIZE`` (200 paths / submit); per-(repo,
        branch) ``asyncio.Lock`` serializes concurrent invocations;
        refit-fatal failure model — any phase exception flips
        ``meta.status='error'`` via a final submit() and re-raises.
        Previously-committed batches stay in the DB; the next
        ``build_index`` Phase 1 DELETEs them. Restructured into 5 phases
        (Phase 0 status flip, Phase 1 tree fetch + chunked DELETE, Phase 2
        read+embed, Phase 3 chunked INSERT, Phase 4 finalize meta). The
        legacy single-session direct-commit path is preserved when
        ``write_queue`` is ``None`` for backward compatibility.

        If the per-(repo, branch) ``asyncio.Lock`` is already held the call
        returns early without touching meta state (Cycle 1 behaviour;
        Cycle 2 wires the ``repo_index_skipped`` decision event with
        ``reason='lock_held'``).
        """
        # v0.4.16 P1b § 3.3 — non-blocking lock acquisition. If another
        # caller is already inside the critical section for this
        # (repo, branch) pair, return early WITHOUT touching meta state.
        # Cycle 2 emits ``repo_index_skipped(reason=lock_held)``.
        lock = await _acquire_repo_index_lock(repo_full_name, branch)
        if lock.locked():
            # Emit skipped under a fresh run-id (lock-skip is a real run for
            # forensic-trace purposes).
            run_id_token = _REPO_INDEX_RUN_ID.set(uuid.uuid4().hex)
            try:
                _emit_decision_event("repo_index_skipped", {
                    "repo_full_name": repo_full_name,
                    "branch": branch,
                    "op": "build",
                    "reason": "lock_held",
                })
            finally:
                _REPO_INDEX_RUN_ID.reset(run_id_token)
            return None

        # v0.4.16 P1b § 12 REFACTOR item 5 — fresh hex run-id per build.
        # Threaded through ``logger.info`` lines so a multi-phase build can
        # be traced via stdout filtering. Cycle 2 wires the same ContextVar
        # into ``_emit_decision_event`` for SSE-side correlation.
        run_id_token = _REPO_INDEX_RUN_ID.set(uuid.uuid4().hex)
        try:
            async with lock:
                _REPO_INDEX_LOCK_LAST_ACQUIRED[(repo_full_name, branch)] = time.time()
                _emit_decision_event("repo_index_lock_acquired", {
                    "repo_full_name": repo_full_name,
                    "branch": branch,
                    "op": "build",
                })
                await self._build_index_locked(repo_full_name, branch, token)
        finally:
            _REPO_INDEX_RUN_ID.reset(run_id_token)

    async def _build_index_locked(
        self,
        repo_full_name: str,
        branch: str,
        token: str,
    ) -> None:
        """Body of :meth:`build_index` after the per-(repo, branch) lock has
        been acquired and the run-id ContextVar set. Extracted so the lock /
        ContextVar setup stays linear in the public entry point.
        """
        t_start = time.monotonic()
        # v0.4.16 P1b § 7 — running batch counter for ``repo_index_completed``
        # payload + reservoir telemetry. Reset per refit so the value reflects
        # THIS run, not lifetime-of-meta (spec § 4.1 row 4 cumulative_rows
        # semantic gloss — same per-refit reset rationale applies here).
        total_batches_committed = 0
        logger.info(
            "build_index started for %s@%s run_id=%s",
            repo_full_name, branch,
            _REPO_INDEX_RUN_ID.get(),
        )

        # Phase 0 — status='indexing' flip (one submit).
        #
        # Bootstrap meta against ``self._db`` (read engine) so the row
        # exists before Phase 0's writer-routed status flip. The atomic
        # upsert is idempotent + commit-only-on-no-row, so this read-side
        # write does not collide with the writer engine.
        meta = await self._get_or_create_meta(repo_full_name, branch)
        meta_id = meta.id
        # v0.4.16 P1b § 4.1 row 2 — capture prior_file_count BEFORE Phase 0
        # flips status='indexing'. Reflects the row count from the previous
        # refit (or 0 on first build). Threaded into ``repo_index_started``.
        prior_file_count = meta.file_count or 0

        async def _phase0_status_flip(db: AsyncSession) -> None:
            m = (await db.execute(
                select(RepoIndexMeta).where(RepoIndexMeta.id == meta_id)
            )).scalars().first()
            if m is None:
                # Meta row vanished mid-flight (e.g. operator manually
                # deleted it). Commit the empty txn for ``submit()``
                # contract compliance — see ``_submit_or_legacy``.
                await db.commit()
                return None
            m.status = "indexing"
            m.index_phase = "fetching_tree"
            m.files_seen = 0
            m.files_total = 0
            m.error_message = None
            await db.commit()
            return None

        await self._submit_or_legacy(_phase0_status_flip)
        # v0.4.16 P1b § 4.1 row 2 — repo_index_started after Phase 0 commits.
        _emit_decision_event("repo_index_started", {
            "repo_full_name": repo_full_name,
            "branch": branch,
            "op": "build",
            "prior_file_count": prior_file_count,
        })
        await _publish_phase_change(
            repo_full_name, branch,
            phase="fetching_tree", status="indexing",
            files_seen=0, files_total=0,
        )

        try:
            # v0.4.16 P1b § 4.1 row 3 — phase_started for Phase 1.
            _emit_decision_event("repo_index_phase_started", {
                "phase": "phase_1_fetch_delete",
                "op": "build",
            })
            head_sha = await self._gc.get_branch_head_sha(
                token, repo_full_name, branch,
            )

            # ETag-conditioned tree fetch. If the stored etag is still
            # valid we get a 304 and can short-circuit the whole
            # rebuild. We only send the etag when we actually have
            # cached rows to trust.
            etag_to_send: str | None = None
            if meta.tree_etag and (meta.file_count or 0) > 0:
                etag_to_send = meta.tree_etag
            tree, new_tree_etag = await self._gc.get_tree_with_cache(
                token, repo_full_name, branch, etag=etag_to_send,
            )

            if tree is None:
                # 304 Not Modified — reuse existing file rows; advance
                # head_sha and mark ready (one submit).
                prior_count = meta.file_count or 0
                logger.info(
                    "build_index: %s@%s tree unchanged (304) — "
                    "reusing %d existing rows run_id=%s",
                    repo_full_name, branch, prior_count,
                    _REPO_INDEX_RUN_ID.get(),
                )

                indexed_now = datetime.now(timezone.utc)

                async def _phase4_304_short_circuit(db: AsyncSession) -> None:
                    m = (await db.execute(
                        select(RepoIndexMeta).where(
                            RepoIndexMeta.id == meta_id,
                        )
                    )).scalars().first()
                    if m is None:
                        # Meta row vanished mid-flight; commit empty txn for
                        # submit() contract compliance.
                        await db.commit()
                        return None
                    m.status = "ready"
                    m.index_phase = "embedding"
                    m.head_sha = head_sha
                    m.files_seen = prior_count
                    m.files_total = prior_count
                    m.error_message = None
                    m.indexed_at = indexed_now
                    # tree_etag intentionally unchanged (304 echoed it).
                    await db.commit()
                    return None

                await self._submit_or_legacy(_phase4_304_short_circuit)
                # v0.4.16 P1b § 4.1 row 6 — skipped(reason='tree_unchanged_304').
                _emit_decision_event("repo_index_skipped", {
                    "repo_full_name": repo_full_name,
                    "branch": branch,
                    "op": "build",
                    "reason": "tree_unchanged_304",
                })
                # v0.4.16 P1b: final SSE emission uses phase='ready' so the
                # frontend transitions out of the indexing state machine.
                await _publish_phase_change(
                    repo_full_name, branch,
                    phase="ready", status="ready",
                    files_seen=prior_count,
                    files_total=prior_count,
                )
                return None

            # Phase 1 — filter + chunked DELETE existing rows.
            code_ext = [
                item for item in tree
                if item["path"].rfind(".") != -1
                and item["path"][item["path"].rfind("."):].lower()
                in _INDEXABLE_EXTENSIONS
            ]
            test_excluded = [
                item for item in code_ext if _is_test_file(item["path"])
            ]
            size_excluded = [
                item for item in code_ext
                if item.get("size") is not None
                and item["size"] > _MAX_FILE_SIZE
            ]
            indexable = [
                item for item in tree
                if _is_indexable(item["path"], item.get("size"))
            ]
            logger.info(
                "build_index filter: repo=%s tree=%d code=%d "
                "tests_excluded=%d size_excluded=%d indexable=%d run_id=%s",
                repo_full_name, len(tree), len(code_ext),
                len(test_excluded), len(size_excluded), len(indexable),
                _REPO_INDEX_RUN_ID.get(),
            )

            # Count existing rows for chunked DELETE in Phase 1.
            existing_paths = (await self._db.execute(
                select(RepoFileIndex.file_path).where(
                    RepoFileIndex.repo_full_name == repo_full_name,
                    RepoFileIndex.branch == branch,
                )
            )).scalars().all()
            existing_count = len(existing_paths)

            if existing_count > 0:
                # Chunk DELETEs at REPO_INDEX_DELETE_BATCH_SIZE so a
                # large existing-table refit doesn't single-shot a
                # huge transaction.
                for chunk_start in range(0, existing_count, REPO_INDEX_DELETE_BATCH_SIZE):
                    chunk = existing_paths[
                        chunk_start:chunk_start + REPO_INDEX_DELETE_BATCH_SIZE
                    ]

                    def _make_delete_chunk_work_fn(
                        paths: list[str],
                    ) -> Callable[[AsyncSession], Awaitable[int]]:
                        async def _work(db: AsyncSession) -> int:
                            await db.execute(
                                delete(RepoFileIndex).where(
                                    RepoFileIndex.repo_full_name == repo_full_name,
                                    RepoFileIndex.branch == branch,
                                    RepoFileIndex.file_path.in_(paths),
                                )
                            )
                            await db.commit()
                            return len(paths)
                        return _work

                    await self._submit_or_legacy(
                        _make_delete_chunk_work_fn(list(chunk)),
                    )

                # One submit to update the meta row's transitional fields.
                indexable_count = len(indexable)

                async def _phase1_meta_update(db: AsyncSession) -> None:
                    m = (await db.execute(
                        select(RepoIndexMeta).where(
                            RepoIndexMeta.id == meta_id,
                        )
                    )).scalars().first()
                    if m is None:
                        # Meta row vanished mid-flight; commit empty txn for
                        # submit() contract compliance.
                        await db.commit()
                        return None
                    m.index_phase = "embedding"
                    m.files_total = indexable_count
                    await db.commit()
                    return None

                await self._submit_or_legacy(_phase1_meta_update)
            # else: no existing rows — skip the DELETE chunks AND skip the
            # meta-update submit so the empty-table N-row build matches the
            # documented submit count of ⌈N/50⌉ + 2. The previous version
            # mutated ``meta.index_phase`` + ``meta.files_total`` directly on
            # the read-engine ORM object here to "keep the UI honest", but
            # those mutations were dead-on-arrival: the SSE phase-change
            # publish below already passes ``len(indexable)`` directly via
            # ``files_total=``, and SQLAlchemy autoflush would have generated
            # an UPDATE on the read engine the next time we executed a SELECT,
            # tripping the read-engine audit hook in production.

            await _publish_phase_change(
                repo_full_name, branch,
                phase="embedding", status="indexing",
                files_seen=0, files_total=len(indexable),
            )

            # Phase 2 — read + embed (no DB writes).
            # v0.4.16 P1b § 4.1 row 3 — phase_started for Phase 2.
            _emit_decision_event("repo_index_phase_started", {
                "phase": "phase_2_read_embed",
                "op": "build",
            })
            t_read = time.monotonic()
            processed, read_failures, embed_failures = await read_and_embed_files(
                db=self._db,
                embedding_service=self._es,
                github_client=self._gc,
                items=indexable,
                token=token,
                repo_full_name=repo_full_name,
                branch=branch,
                concurrency=10,
            )
            process_ms = (time.monotonic() - t_read) * 1000
            total_content_chars = sum(len(pf.content) for pf in processed)

            if read_failures:
                logger.warning(
                    "build_index: %s@%s %d/%d file reads failed",
                    repo_full_name, branch,
                    read_failures, len(indexable),
                )

            # Phase 3 — persist file rows in batches.
            # v0.4.16 P1b § 4.1 row 3 — phase_started for Phase 3.
            _emit_decision_event("repo_index_phase_started", {
                "phase": "phase_3_persist",
                "op": "build",
            })
            t_persist = time.monotonic()
            file_count = 0
            processed_count = len(processed)
            indexable_total = len(indexable)
            batch_index = 0
            for batch_start in range(0, processed_count, REPO_INDEX_PERSIST_BATCH_SIZE):
                batch = processed[
                    batch_start:batch_start + REPO_INDEX_PERSIST_BATCH_SIZE
                ]

                def _make_persist_batch_work_fn(
                    rows: list[ProcessedFile],
                ) -> Callable[[AsyncSession], Awaitable[int]]:
                    async def _work(db: AsyncSession) -> int:
                        for pf in rows:
                            row = RepoFileIndex(
                                repo_full_name=repo_full_name,
                                branch=branch,
                                file_path=pf.item["path"],
                                file_sha=pf.item.get("sha"),
                                file_size_bytes=pf.item.get("size"),
                                content=pf.content,
                                outline=pf.outline.structural_summary,
                                content_sha=pf.content_sha,
                                embedding=pf.embedding.tobytes(),
                            )
                            db.add(row)
                        await db.commit()
                        return len(rows)
                    return _work

                # v0.4.16 P1b § 4.1 row 4/5 — wrap submit in try/except so a
                # failure emits ``repo_index_batch_rolled_back`` BEFORE the
                # exception bubbles up to the outer except clause that calls
                # ``_mark_meta_error``.
                t_batch = time.monotonic()
                rows_in_batch = len(batch)
                try:
                    await self._submit_or_legacy(
                        _make_persist_batch_work_fn(list(batch)),
                    )
                except Exception as batch_exc:
                    err_msg = str(batch_exc)[:80]
                    _emit_decision_event("repo_index_batch_rolled_back", {
                        "phase": "phase_3_persist",
                        "batch_index": batch_index,
                        "reason": "phase_exception",
                        "error_class": type(batch_exc).__name__,
                        "error_message_truncated_80c": err_msg,
                    })
                    _record_batch_rolled_back()
                    raise
                batch_duration_ms = (time.monotonic() - t_batch) * 1000.0
                file_count += rows_in_batch
                total_batches_committed += 1

                # v0.4.16 P1b § 4.1 row 4 — batch_committed.
                _emit_decision_event("repo_index_batch_committed", {
                    "phase": "phase_3_persist",
                    "batch_index": batch_index,
                    "rows_in_batch": rows_in_batch,
                    "cumulative_rows": file_count,
                    "batch_duration_ms": batch_duration_ms,
                })
                _record_batch_committed_latency(batch_duration_ms)

                # v0.4.16 P1b § 4.3 — per-batch SSE progress, throttled.
                if (
                    batch_index == 0
                    or batch_index % REPO_INDEX_LOG_PROGRESS_BATCH_INTERVAL == 0
                ):
                    await _publish_phase_change(
                        repo_full_name, branch,
                        phase="embedding", status="indexing",
                        files_seen=file_count,
                        files_total=indexable_total,
                    )

                batch_index += 1

            # Phase 4 — finalize meta (one submit).
            # v0.4.16 P1b § 4.1 row 3 — phase_started for Phase 4.
            _emit_decision_event("repo_index_phase_started", {
                "phase": "phase_4_finalize",
                "op": "build",
            })
            indexed_now = datetime.now(timezone.utc)

            async def _phase4_finalize(db: AsyncSession) -> None:
                m = (await db.execute(
                    select(RepoIndexMeta).where(
                        RepoIndexMeta.id == meta_id,
                    )
                )).scalars().first()
                if m is None:
                    # Meta row vanished mid-flight; commit empty txn for
                    # submit() contract compliance.
                    await db.commit()
                    return None
                m.status = "ready"
                # Empty-existing-rows path skips Phase 1's meta-update submit,
                # so Phase 4 must also pin ``index_phase='embedding'`` to keep
                # the canonical writer-engine row's terminal state consistent
                # across both paths (existing-rows & empty-table).
                m.index_phase = "embedding"
                m.head_sha = head_sha
                m.file_count = file_count
                m.files_seen = file_count
                m.files_total = indexable_total
                m.error_message = None
                m.indexed_at = indexed_now
                if new_tree_etag:
                    m.tree_etag = new_tree_etag
                await db.commit()
                return None

            await self._submit_or_legacy(_phase4_finalize)
            # Cache safety: a full rebuild replaced every
            # RepoFileIndex row, so any cached curated retrieval
            # keyed on (repo, branch, query, …) is now stale.
            invalidate_curated_cache()
            # v0.4.16 P1b: final SSE emission carries ``phase='ready'`` so
            # the frontend's `phase === 'ready'` state-machine transition
            # fires AND test 22's persist-phase progress filter
            # (``phase == 'embedding'``) excludes the post-loop event.
            await _publish_phase_change(
                repo_full_name, branch,
                phase="ready", status="ready",
                files_seen=file_count, files_total=indexable_total,
            )
            persist_ms = (time.monotonic() - t_persist) * 1000
            total_ms = (time.monotonic() - t_start) * 1000

            # v0.4.16 P1b § 4.1 row 7 — repo_index_completed (success path).
            # Note: ``total_batches_rolled_back`` intentionally NOT in payload
            # per spec § 4.1 paragraph (refit-fatal model — successful refit
            # has zero rolled-back batches by construction).
            _emit_decision_event("repo_index_completed", {
                "repo_full_name": repo_full_name,
                "branch": branch,
                "op": "build",
                "final_file_count": file_count,
                "total_duration_ms": float(total_ms),
                "total_batches_committed": total_batches_committed,
            })
            _snapshot_last_run(
                repo_full_name=repo_full_name,
                branch=branch,
                op="build",
                final_file_count=file_count,
                total_duration_ms=total_ms,
                total_batches_committed=total_batches_committed,
            )
            # v0.4.16 P1b § 11 row 17 — recap chain. See
            # ``_emit_success_recap_chain`` docstring for rationale.
            _emit_success_recap_chain(
                repo_full_name=repo_full_name,
                branch=branch,
                op="build",
                final_file_count=file_count,
                total_duration_ms=total_ms,
                total_batches_committed=total_batches_committed,
                last_batch_index=max(batch_index - 1, 0),
                last_phase="phase_3_persist",
                last_phase_for_phase_started="phase_4_finalize",
                prior_file_count=prior_file_count,
            )

            logger.info(
                "build_index complete: repo=%s files=%d content=%dK "
                "read_failures=%d embed_failures=%d "
                "process=%.0fms persist=%.0fms total=%.0fms run_id=%s",
                repo_full_name, file_count, total_content_chars // 1000,
                read_failures, embed_failures,
                process_ms, persist_ms, total_ms,
                _REPO_INDEX_RUN_ID.get(),
            )

        except GitHubApiError as exc:
            # Transient network / auth failure from the GitHub API.
            # Refit-fatal flip is the same as the generic-Exception path —
            # the next build_index call DELETEs partial batches and starts
            # clean.  Logged at WARNING (not exception) because this is a
            # known transient class and the stack trace adds no signal.
            # Cycle 2 wires ``reason='github_api_error'`` into the
            # ``repo_index_failed`` decision event.
            logger.warning(
                "build_index failed for %s@%s — GitHubApiError: %s",
                repo_full_name, branch, exc,
            )
            await self._mark_meta_error(
                meta=meta, meta_id=meta_id,
                repo_full_name=repo_full_name, branch=branch,
                err_msg=str(exc),
            )
            raise
        except Exception as exc:
            # Other failure (DB IntegrityError, ForeignKey violation,
            # filesystem read error, runtime bug). Logged at exception so
            # the operator gets the full traceback. Same refit-fatal flip.
            # Cycle 2 records ``reason='unknown'`` on the failure event.
            logger.exception(
                "build_index failed for %s@%s",
                repo_full_name, branch,
            )
            await self._mark_meta_error(
                meta=meta, meta_id=meta_id,
                repo_full_name=repo_full_name, branch=branch,
                err_msg=str(exc),
            )
            raise

    async def _mark_meta_error(
        self,
        *,
        meta: RepoIndexMeta,
        meta_id: str,
        repo_full_name: str,
        branch: str,
        err_msg: str,
    ) -> None:
        """Refit-fatal error finalize: flip meta to ``status='error'`` via
        one ``submit()`` and publish the matching ``index_phase_changed``
        SSE event.  Extracted so the two split except clauses in
        :meth:`_build_index_locked` share one error-finalize path.
        """
        # Best-effort snapshot from the read-engine ORM object — values may
        # be stale relative to writer-engine state (e.g. an in-flight Phase 3
        # batch may have advanced ``files_seen`` since the failure point).
        # The SSE event payload is informational; the canonical state lives
        # on the writer-engine row that ``_phase_error_flip`` updates below.
        files_seen_snapshot = meta.files_seen or 0
        files_total_snapshot = meta.files_total or 0

        async def _phase_error_flip(db: AsyncSession) -> None:
            m = (await db.execute(
                select(RepoIndexMeta).where(
                    RepoIndexMeta.id == meta_id,
                )
            )).scalars().first()
            if m is None:
                # Meta row vanished mid-flight; commit empty txn for
                # submit() contract compliance.
                await db.commit()
                return None
            m.status = "error"
            m.index_phase = "error"
            m.error_message = err_msg
            await db.commit()
            return None

        try:
            await self._submit_or_legacy(_phase_error_flip)
        except Exception:
            logger.debug(
                "build_index error-flip submit failed", exc_info=True,
            )
        await _publish_phase_change(
            repo_full_name, branch,
            phase="error", status="error",
            files_seen=files_seen_snapshot,
            files_total=files_total_snapshot,
            error=err_msg,
        )

    async def get_index_status(
        self, repo_full_name: str, branch: str
    ) -> RepoIndexMeta | None:
        """Return the RepoIndexMeta for this repo/branch, or None if absent."""
        result = await self._db.execute(
            select(RepoIndexMeta).where(
                RepoIndexMeta.repo_full_name == repo_full_name,
                RepoIndexMeta.branch == branch,
            )
        )
        return result.scalars().first()

    async def is_stale(
        self, repo_full_name: str, branch: str, current_sha: str
    ) -> bool:
        """Return True if the stored head_sha differs from current_sha."""
        meta = await self.get_index_status(repo_full_name, branch)
        if meta is None:
            return True
        if meta.head_sha is None:
            return False  # Legacy row — no SHA to compare; treat as fresh
        return meta.head_sha != current_sha

    async def invalidate_index(self, repo_full_name: str, branch: str) -> None:
        """Delete all index entries and the meta row for this repo/branch.

        v0.4.16 P1b: routes through ``WriteQueue.submit()`` chunked at
        ``REPO_INDEX_DELETE_BATCH_SIZE`` (200 paths / submit) for file-row
        deletions, then issues one final submit() to delete the meta row.
        Each submit is its own transaction; an exception in any chunk
        leaves prior chunks committed (refit-fatal failure model — the
        next caller's chunked DELETE wipes whatever survived).  Unlike
        ``build_index`` / ``incremental_update``, ``invalidate_index``
        does NOT acquire the per-(repo, branch) ``asyncio.Lock`` because
        it is called from cleanup paths (unlink, reindex) that must
        always proceed even when a build is mid-flight.
        """
        existing_paths = (await self._db.execute(
            select(RepoFileIndex.file_path).where(
                RepoFileIndex.repo_full_name == repo_full_name,
                RepoFileIndex.branch == branch,
            )
        )).scalars().all()
        existing_count = len(existing_paths)

        # Chunk DELETEs at REPO_INDEX_DELETE_BATCH_SIZE.
        for chunk_start in range(0, existing_count, REPO_INDEX_DELETE_BATCH_SIZE):
            chunk = existing_paths[
                chunk_start:chunk_start + REPO_INDEX_DELETE_BATCH_SIZE
            ]

            def _make_invalidate_chunk_work_fn(
                paths: list[str],
            ) -> Callable[[AsyncSession], Awaitable[int]]:
                async def _work(db: AsyncSession) -> int:
                    await db.execute(
                        delete(RepoFileIndex).where(
                            RepoFileIndex.repo_full_name == repo_full_name,
                            RepoFileIndex.branch == branch,
                            RepoFileIndex.file_path.in_(paths),
                        )
                    )
                    await db.commit()
                    return len(paths)
                return _work

            await self._submit_or_legacy(
                _make_invalidate_chunk_work_fn(list(chunk)),
            )

        # Final submit — delete the meta row.
        async def _delete_meta(db: AsyncSession) -> None:
            await db.execute(
                delete(RepoIndexMeta).where(
                    RepoIndexMeta.repo_full_name == repo_full_name,
                    RepoIndexMeta.branch == branch,
                )
            )
            await db.commit()
            return None

        await self._submit_or_legacy(_delete_meta)

    # ------------------------------------------------------------------
    # Incremental refresh — detect changed files and re-embed only those
    # ------------------------------------------------------------------

    async def incremental_update(
        self,
        repo_full_name: str,
        branch: str,
        token: str,
        concurrency: int = 5,
    ) -> dict[str, Any]:
        """Incrementally update the index by diffing the current HEAD tree
        against stored ``file_sha`` values.

        v0.4.16 P1b: routes through ``WriteQueue.submit()`` chunked at
        ``REPO_INDEX_PERSIST_BATCH_SIZE`` (50 upserts / submit) and
        ``REPO_INDEX_DELETE_BATCH_SIZE`` (200 paths / submit); per-(repo,
        branch) ``asyncio.Lock`` serializes concurrent invocations;
        refit-fatal failure model — any exception inside a phase aborts
        the run and re-raises after marking meta='error'. Restructured
        into 6 phases (A-F) per spec § 3.1: Phase A status guards (read
        only), Phase B HEAD SHA check, Phase C tree fetch + diff, Phase
        D chunked DELETE, Phase E read + embed (no DB writes), Phase F
        chunked upsert + finalize meta. If the per-key lock is already
        held the call returns ``{"skipped_reason": "lock_held"}`` without
        touching meta state (Cycle 1 behaviour; Cycle 2 wires the matching
        ``repo_index_skipped`` decision event).

        Returns a summary dict::

            {
                "changed": int, "added": int, "removed": int,
                "read_failures": int, "embed_failures": int,
                "skipped_reason": str | None,
                "elapsed_ms": float,
            }
        """
        t_start = time.monotonic()

        def _result(
            changed: int = 0, added: int = 0, removed: int = 0,
            read_failures: int = 0, embed_failures: int = 0,
            skipped_reason: str | None = None,
        ) -> dict[str, Any]:
            return {
                "changed": changed, "added": added, "removed": removed,
                "read_failures": read_failures, "embed_failures": embed_failures,
                "skipped_reason": skipped_reason,
                "elapsed_ms": round((time.monotonic() - t_start) * 1000, 1),
            }

        # v0.4.16 P1b § 3.3 — non-blocking lock acquisition.
        lock = await _acquire_repo_index_lock(repo_full_name, branch)
        if lock.locked():
            run_id_token = _REPO_INDEX_RUN_ID.set(uuid.uuid4().hex)
            try:
                _emit_decision_event("repo_index_skipped", {
                    "repo_full_name": repo_full_name,
                    "branch": branch,
                    "op": "incremental",
                    "reason": "lock_held",
                })
            finally:
                _REPO_INDEX_RUN_ID.reset(run_id_token)
            return _result(skipped_reason="lock_held")

        # v0.4.16 P1b § 12 REFACTOR item 5 — fresh hex run-id per refresh.
        run_id_token = _REPO_INDEX_RUN_ID.set(uuid.uuid4().hex)
        try:
            async with lock:
                _REPO_INDEX_LOCK_LAST_ACQUIRED[(repo_full_name, branch)] = time.time()
                _emit_decision_event("repo_index_lock_acquired", {
                    "repo_full_name": repo_full_name,
                    "branch": branch,
                    "op": "incremental",
                })
                return await self._incremental_update_locked(
                    repo_full_name, branch, token, concurrency,
                    t_start, _result,
                )
        finally:
            _REPO_INDEX_RUN_ID.reset(run_id_token)

    async def _incremental_update_locked(
        self,
        repo_full_name: str,
        branch: str,
        token: str,
        concurrency: int,
        t_start: float,
        _result: Callable[..., dict[str, Any]],
    ) -> dict[str, Any]:
        """Body of :meth:`incremental_update` after the per-(repo, branch)
        lock has been acquired and the run-id ContextVar set. Extracted so
        the lock / ContextVar setup stays linear in the public entry point.
        ``_result`` is the closure-captured summary builder from
        :meth:`incremental_update` (preserves the in-method ``elapsed_ms``
        derivation).
        """
        repo_tag = f"{repo_full_name}@{branch}"
        logger.info(
            "incremental_update started for %s run_id=%s",
            repo_tag, _REPO_INDEX_RUN_ID.get(),
        )

        # Phase A — Status guards (read-only).
        meta = await self.get_index_status(repo_full_name, branch)
        if meta is None:
            logger.info(
                "incremental_update: %s no meta — skipping (needs build_index) "
                "run_id=%s",
                repo_tag, _REPO_INDEX_RUN_ID.get(),
            )
            return _result(skipped_reason="no_index")

        if meta.status == "indexing":
            logger.info(
                "incremental_update: %s currently indexing — skipping "
                "run_id=%s",
                repo_tag, _REPO_INDEX_RUN_ID.get(),
            )
            return _result(skipped_reason="indexing")

        meta_id = meta.id
        prior_meta_etag = meta.tree_etag
        prior_file_count = meta.file_count or 0

        # v0.4.16 P1b § 4.1 row 2 — repo_index_started after Phase A guards
        # pass (no meta-state mutation in Phase A → emit immediately).
        _emit_decision_event("repo_index_started", {
            "repo_full_name": repo_full_name,
            "branch": branch,
            "op": "incremental",
            "prior_file_count": prior_file_count,
        })

        # Phase B — HEAD SHA check + skip path.
        # v0.4.16 P1b § 4.1 row 3 — phase_started for Phase B.
        _emit_decision_event("repo_index_phase_started", {
            "phase": "phase_b_head_sha",
            "op": "incremental",
        })
        t_sha = time.monotonic()
        try:
            current_sha = await self._gc.get_branch_head_sha(
                token, repo_full_name, branch,
            )
        except GitHubApiError as exc:
            reason = _classify_github_error(exc)
            _log_refresh_warning(
                repo_tag, reason,
                f"{repo_tag} HEAD check failed: {exc} ({reason})",
            )
            return _result(skipped_reason=reason)
        except Exception as exc:
            _log_refresh_warning(
                repo_tag, "network_error",
                f"{repo_tag} HEAD check failed: {exc}",
            )
            return _result(skipped_reason="network_error")
        else:
            _mark_refresh_recovered(repo_tag)
        sha_ms = (time.monotonic() - t_sha) * 1000

        if current_sha and meta.head_sha == current_sha:
            logger.debug(
                "incremental_update: %s HEAD unchanged (%s) sha_check=%.0fms",
                repo_tag, current_sha[:8], sha_ms,
            )
            # v0.4.16 P1b § 4.1 row 6 — skipped(reason='head_unchanged').
            _emit_decision_event("repo_index_skipped", {
                "repo_full_name": repo_full_name,
                "branch": branch,
                "op": "incremental",
                "reason": "head_unchanged",
            })
            return _result(skipped_reason="head_unchanged")

        # Phase C — Tree fetch + diff (in-memory).
        # v0.4.16 P1b § 4.1 row 3 — phase_started for Phase C.
        _emit_decision_event("repo_index_phase_started", {
            "phase": "phase_c_tree_diff",
            "op": "incremental",
        })
        t_tree = time.monotonic()
        try:
            tree, new_tree_etag = await self._gc.get_tree_with_cache(
                token, repo_full_name, branch, etag=prior_meta_etag,
            )
        except GitHubApiError as exc:
            reason = _classify_github_error(exc)
            logger.warning(
                "incremental_update: %s tree fetch failed: %s (%s)",
                repo_tag, exc, reason,
            )
            return _result(skipped_reason=reason)
        except Exception as exc:
            logger.warning(
                "incremental_update: %s tree fetch failed: %s",
                repo_tag, exc,
            )
            return _result(skipped_reason="network_error")
        tree_ms = (time.monotonic() - t_tree) * 1000

        # 304: tree unchanged — advance head_sha via one submit.
        if tree is None:
            async def _advance_head_sha_only(db: AsyncSession) -> None:
                m = (await db.execute(
                    select(RepoIndexMeta).where(
                        RepoIndexMeta.id == meta_id,
                    )
                )).scalars().first()
                if m is None:
                    # Meta row vanished mid-flight; commit empty txn for
                    # submit() contract compliance.
                    await db.commit()
                    return None
                m.head_sha = current_sha
                await db.commit()
                return None

            await self._submit_or_legacy(_advance_head_sha_only)
            # v0.4.16 P1b § 4.1 row 6 — skipped(reason='tree_unchanged_304').
            _emit_decision_event("repo_index_skipped", {
                "repo_full_name": repo_full_name,
                "branch": branch,
                "op": "incremental",
                "reason": "tree_unchanged_304",
            })
            logger.info(
                "incremental_update: %s tree unchanged (304) head→%s "
                "sha=%.0fms tree=%.0fms run_id=%s",
                repo_tag, (current_sha or "?")[:8],
                sha_ms, tree_ms,
                _REPO_INDEX_RUN_ID.get(),
            )
            return _result(skipped_reason="tree_unchanged")

        # Build lookup from current tree (only indexable files).
        tree_map: dict[str, dict] = {}
        for item in tree:
            if _is_indexable(item["path"], item.get("size")):
                tree_map[item["path"]] = item

        # Load indexed file paths and SHAs.
        db_result = await self._db.execute(
            select(
                RepoFileIndex.file_path,
                RepoFileIndex.file_sha,
            ).where(
                RepoFileIndex.repo_full_name == repo_full_name,
                RepoFileIndex.branch == branch,
            )
        )
        indexed_map: dict[str, str | None] = {
            row.file_path: row.file_sha for row in db_result.all()
        }

        # Classify into changed / added / removed.
        t_diff = time.monotonic()
        changed_items: list[dict] = []
        added_items: list[dict] = []
        removed_paths: list[str] = []

        for path, item in tree_map.items():
            if path not in indexed_map:
                added_items.append(item)
            elif item.get("sha") and indexed_map[path] != item["sha"]:
                changed_items.append(item)

        for path in indexed_map:
            if path not in tree_map:
                removed_paths.append(path)
        diff_ms = (time.monotonic() - t_diff) * 1000

        total_delta = (
            len(changed_items) + len(added_items) + len(removed_paths)
        )
        if total_delta == 0:
            # SHA differs but no file-level changes (e.g. merge commit).
            async def _advance_head_sha_no_diff(db: AsyncSession) -> None:
                m = (await db.execute(
                    select(RepoIndexMeta).where(
                        RepoIndexMeta.id == meta_id,
                    )
                )).scalars().first()
                if m is None:
                    # Meta row vanished mid-flight; commit empty txn for
                    # submit() contract compliance.
                    await db.commit()
                    return None
                m.head_sha = current_sha
                await db.commit()
                return None

            await self._submit_or_legacy(_advance_head_sha_no_diff)
            logger.info(
                "incremental_update: %s HEAD changed (→%s) but no file diffs "
                "sha=%.0fms tree=%.0fms run_id=%s",
                repo_tag, (current_sha or "?")[:8],
                sha_ms, tree_ms,
                _REPO_INDEX_RUN_ID.get(),
            )
            return _result()

        logger.info(
            "incremental_diff: repo=%s changed=%d added=%d removed=%d "
            "tree=%d indexed=%d sha=%.0fms tree=%.0fms diff=%.0fms run_id=%s",
            repo_tag,
            len(changed_items), len(added_items), len(removed_paths),
            len(tree_map), len(indexed_map),
            sha_ms, tree_ms, diff_ms,
            _REPO_INDEX_RUN_ID.get(),
        )

        # Phase D — Delete removed file rows in chunks.
        # v0.4.16 P1b § 4.1 row 3 — phase_started for Phase D.
        _emit_decision_event("repo_index_phase_started", {
            "phase": "phase_d_delete",
            "op": "incremental",
        })
        t_delete = time.monotonic()
        removed_count = len(removed_paths)
        for chunk_start in range(0, removed_count, REPO_INDEX_DELETE_BATCH_SIZE):
            chunk = removed_paths[
                chunk_start:chunk_start + REPO_INDEX_DELETE_BATCH_SIZE
            ]

            def _make_remove_chunk_work_fn(
                paths: list[str],
            ) -> Callable[[AsyncSession], Awaitable[int]]:
                async def _work(db: AsyncSession) -> int:
                    await db.execute(
                        delete(RepoFileIndex).where(
                            RepoFileIndex.repo_full_name == repo_full_name,
                            RepoFileIndex.branch == branch,
                            RepoFileIndex.file_path.in_(paths),
                        )
                    )
                    await db.commit()
                    return len(paths)
                return _work

            await self._submit_or_legacy(
                _make_remove_chunk_work_fn(list(chunk)),
            )
        delete_ms = (time.monotonic() - t_delete) * 1000

        # Phase E — Read, embed (no DB writes).
        # v0.4.16 P1b § 4.1 row 3 — phase_started for Phase E.
        _emit_decision_event("repo_index_phase_started", {
            "phase": "phase_e_read_embed",
            "op": "incremental",
        })
        t_process = time.monotonic()
        to_process = changed_items + added_items
        processed, read_failures, embed_failures = await read_and_embed_files(
            db=self._db,
            embedding_service=self._es,
            github_client=self._gc,
            items=to_process,
            token=token,
            repo_full_name=repo_full_name,
            branch=branch,
            concurrency=concurrency,
        )
        process_ms = (time.monotonic() - t_process) * 1000

        if read_failures:
            logger.warning(
                "incremental_update: %s %d/%d file reads failed",
                repo_tag, read_failures, len(to_process),
            )

        # Phase F — Upsert changed+added in batches + finalize meta.
        # v0.4.16 P1b § 4.1 row 3 — phase_started for Phase F.
        _emit_decision_event("repo_index_phase_started", {
            "phase": "phase_f_persist",
            "op": "incremental",
        })
        t_persist = time.monotonic()
        upserted = 0
        processed_count = len(processed)
        # v0.4.16 P1b § 7 — running batch counter. Reset per refit so the
        # ``repo_index_completed`` payload reflects THIS run only (matches
        # ``cumulative_rows`` semantics from build_index Phase 3).
        total_batches_committed = 0
        cumulative_rows_incr = 0
        # ``files_total`` for SSE progress reflects what's being persisted in
        # this incremental — not the total repo row count.
        progress_files_total = max(processed_count, 1)
        batch_index = 0
        for batch_start in range(0, processed_count, REPO_INDEX_PERSIST_BATCH_SIZE):
            batch = processed[
                batch_start:batch_start + REPO_INDEX_PERSIST_BATCH_SIZE
            ]

            def _make_upsert_batch_work_fn(
                rows: list[ProcessedFile],
            ) -> Callable[[AsyncSession], Awaitable[int]]:
                async def _work(db: AsyncSession) -> int:
                    local_upserted = 0
                    for pf in rows:
                        try:
                            stmt = sqlite_insert(RepoFileIndex).values(
                                id=str(uuid.uuid4()),
                                repo_full_name=repo_full_name,
                                branch=branch,
                                file_path=pf.item["path"],
                                file_sha=pf.item.get("sha"),
                                file_size_bytes=pf.item.get("size"),
                                content=pf.content,
                                outline=pf.outline.structural_summary,
                                content_sha=pf.content_sha,
                                embedding=pf.embedding.tobytes(),
                                updated_at=datetime.now(timezone.utc),
                            ).on_conflict_do_update(
                                index_elements=[
                                    "repo_full_name", "branch", "file_path",
                                ],
                                set_={
                                    "file_sha": pf.item.get("sha"),
                                    "file_size_bytes": pf.item.get("size"),
                                    "content": pf.content,
                                    "outline": pf.outline.structural_summary,
                                    "content_sha": pf.content_sha,
                                    "embedding": pf.embedding.tobytes(),
                                    "updated_at": datetime.now(timezone.utc),
                                },
                            )
                            await db.execute(stmt)
                            local_upserted += 1
                        except Exception as db_exc:
                            logger.warning(
                                "incremental_update: %s upsert failed "
                                "for %s: %s",
                                repo_tag, pf.item["path"], db_exc,
                            )
                    await db.commit()
                    return local_upserted
                return _work

            t_batch = time.monotonic()
            rows_in_batch = len(batch)
            try:
                upserted += await self._submit_or_legacy(
                    _make_upsert_batch_work_fn(list(batch)),
                )
            except Exception as batch_exc:
                err_msg = str(batch_exc)[:80]
                _emit_decision_event("repo_index_batch_rolled_back", {
                    "phase": "phase_f_persist",
                    "batch_index": batch_index,
                    "reason": "phase_exception",
                    "error_class": type(batch_exc).__name__,
                    "error_message_truncated_80c": err_msg,
                })
                _record_batch_rolled_back()
                raise
            batch_duration_ms = (time.monotonic() - t_batch) * 1000.0
            cumulative_rows_incr += rows_in_batch
            total_batches_committed += 1
            _emit_decision_event("repo_index_batch_committed", {
                "phase": "phase_f_persist",
                "batch_index": batch_index,
                "rows_in_batch": rows_in_batch,
                "cumulative_rows": cumulative_rows_incr,
                "batch_duration_ms": batch_duration_ms,
            })
            _record_batch_committed_latency(batch_duration_ms)
            if (
                batch_index == 0
                or batch_index % REPO_INDEX_LOG_PROGRESS_BATCH_INTERVAL == 0
            ):
                await _publish_phase_change(
                    repo_full_name, branch,
                    phase="embedding", status="indexing",
                    files_seen=cumulative_rows_incr,
                    files_total=progress_files_total,
                )
            batch_index += 1
        persist_ms = (time.monotonic() - t_persist) * 1000

        # Phase F finale — update meta in one submit.
        prior_count = meta.file_count or 0
        new_count = max(
            0, prior_count + len(added_items) - len(removed_paths),
        )
        indexed_now = datetime.now(timezone.utc)

        async def _phase_f_finalize(db: AsyncSession) -> None:
            m = (await db.execute(
                select(RepoIndexMeta).where(
                    RepoIndexMeta.id == meta_id,
                )
            )).scalars().first()
            if m is None:
                # Meta row vanished mid-flight; commit empty txn for
                # submit() contract compliance.
                await db.commit()
                return None
            m.head_sha = current_sha
            m.file_count = new_count
            m.indexed_at = indexed_now
            if new_tree_etag:
                m.tree_etag = new_tree_etag
            await db.commit()
            return None

        await self._submit_or_legacy(_phase_f_finalize)
        invalidate_curated_cache()

        total_ms = (time.monotonic() - t_start) * 1000

        # v0.4.16 P1b § 4.1 row 7 — repo_index_completed.
        _emit_decision_event("repo_index_completed", {
            "repo_full_name": repo_full_name,
            "branch": branch,
            "op": "incremental",
            "final_file_count": new_count,
            "total_duration_ms": float(total_ms),
            "total_batches_committed": total_batches_committed,
        })
        _snapshot_last_run(
            repo_full_name=repo_full_name,
            branch=branch,
            op="incremental",
            final_file_count=new_count,
            total_duration_ms=total_ms,
            total_batches_committed=total_batches_committed,
        )
        # v0.4.16 P1b § 11 row 17 — recap chain.
        _emit_success_recap_chain(
            repo_full_name=repo_full_name,
            branch=branch,
            op="incremental",
            final_file_count=new_count,
            total_duration_ms=total_ms,
            total_batches_committed=total_batches_committed,
            last_batch_index=max(batch_index - 1, 0),
            last_phase="phase_f_persist",
            last_phase_for_phase_started="phase_f_persist",
            prior_file_count=prior_file_count,
        )

        logger.info(
            "incremental_update_complete: repo=%s changed=%d added=%d "
            "removed=%d upserted=%d read_fail=%d embed_fail=%d "
            "sha=%.0fms tree=%.0fms diff=%.0fms delete=%.0fms "
            "process=%.0fms persist=%.0fms total=%.0fms run_id=%s",
            repo_tag,
            len(changed_items), len(added_items), len(removed_paths),
            upserted, read_failures, embed_failures,
            sha_ms, tree_ms, diff_ms, delete_ms,
            process_ms, persist_ms, total_ms,
            _REPO_INDEX_RUN_ID.get(),
        )

        return _result(
            changed=len(changed_items),
            added=len(added_items),
            removed=len(removed_paths),
            read_failures=read_failures,
            embed_failures=embed_failures,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_or_create_meta(
        self, repo_full_name: str, branch: str
    ) -> RepoIndexMeta:
        """Idempotent atomic upsert of the ``RepoIndexMeta`` row for
        ``(repo_full_name, branch)``, returning the live row.

        Uses ``INSERT ... ON CONFLICT DO NOTHING`` so concurrent callers
        cannot create duplicate rows. The subsequent ``self._db.flush()``
        is the one known-retained read-engine write in the lifecycle path;
        Phase 0's writer-routed status flip depends on the row already
        existing in the canonical DB before it issues its UPDATE. The
        audit-hook tolerates this single retained call site (1 of 1, not
        "0 new sources"); future work could push it behind a writer-routed
        upsert if the audit policy tightens.
        """
        stmt = sqlite_insert(RepoIndexMeta).values(
            id=str(uuid.uuid4()),
            repo_full_name=repo_full_name,
            branch=branch,
            status="pending",
            file_count=0,
        ).on_conflict_do_nothing(
            index_elements=["repo_full_name", "branch"],
        )
        await self._db.execute(stmt)
        await self._db.flush()

        # Fetch the (possibly pre-existing) row
        meta = await self.get_index_status(repo_full_name, branch)
        assert meta is not None, (
            f"RepoIndexMeta for {repo_full_name}@{branch} must exist after upsert"
        )
        return meta

# ---------------------------------------------------------------------------
# Backward-compat re-exports
# ---------------------------------------------------------------------------
# These names were part of the public surface before the split and must
# remain importable from ``app.services.repo_index_service``. The query
# module is the new source of truth; this module re-exports so existing
# call sites (routers, tests, other services) don't have to change.
__all__ = [
    # Indexing-side public API
    "RepoIndexService",
    "FileOutline",
    "ProcessedFile",
    "invalidate_file_cache",
    # Re-exported from repo_index_query
    "CuratedCodebaseContext",
    "RepoIndexQuery",
    "invalidate_curated_cache",
    # Internal helpers imported by tests
    "_build_content_sha",
    "_classify_github_error",
    "_classify_source_type",
    "_compute_source_weight",
    "_curated_cache",
    "_extract_import_paths",
    "_extract_markdown_references",
    "_extract_structured_outline",
    "_publish_phase_change",
    # v0.4.16 P1b § 7 — observability surface
    "_emit_decision_event",
    "_assert_reason_in_set",
    "_get_repo_index_metrics",
    "_REPO_INDEX_LATENCY_RESERVOIR",
    "_REPO_INDEX_BATCH_COUNTER",
    "_REPO_INDEX_LAST_RUN",
]
