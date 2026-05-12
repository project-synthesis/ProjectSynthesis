"""ValidationSuiteService — T2 immutable suite snapshots.

Spec: ``docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md`` §5.

Detached-ORM-safe per Foundation P4 contract:
* Read step opens a SHORT read session via the canonical
  ``app.database.async_session_factory``; the session closes BEFORE any
  ``WriteQueue.submit()`` invocation.
* Persist step crosses the write-queue boundary as a frozen
  ``SuiteSnapshotInputs`` dataclass — no ORM references leak across the
  session boundary.
* Event emission + JSONL trace fire AFTER ``write_queue.submit()`` resolves
  successfully — failed writes do NOT publish events.

Cycle 2 ships ``create_from_run`` only. Cycle 3 adds ``retire`` / ``get`` /
``list`` / ``list_replays``. Cycle 4 adds ``compute_regression_alarm``.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Select, func, select, update

from app import config as _config_mod
from app import database as _database_mod
from app.models import RunRow, ValidationSuite
from app.schemas.runs import RunListResponse, RunSummary
from app.schemas.validation_suite import (
    RegressionAlarmBlock,
    RegressionAlarmEntry,
    ValidationSuiteListItem,
    ValidationSuiteListResponse,
    ValidationSuiteOut,
)
from app.services.event_bus import event_bus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.write_queue import WriteQueue


logger = logging.getLogger(__name__)


# Canonical ``RunRow.aggregate`` key set the snapshot extracts. Single source
# of truth referenced by both ``_build_baseline_scores`` (the dict producer)
# and the spec body §3 baseline-scores shape definition. Used solely for
# documentation + a defensive ``__all__``-style witness — runtime extraction
# still uses literal keys because the call sites need typed defaults per key
# (floats default to ``0.0``, ``per_prompt`` to ``[]``,
# ``task_type_distribution`` to ``{}``).
_BASELINE_SCORES_KEYS: tuple[str, ...] = (
    "mean_overall",
    "p5_overall",
    "p50_overall",
    "p95_overall",
    "per_prompt",
    "task_type_distribution",
)


# Regression-alarm TTL cache window in seconds — matches the
# ``taxonomy/sub_domain_readiness.py`` 30s cadence pattern (see
# ``_CACHE_TTL_SECONDS`` there). Two consecutive
# ``compute_regression_alarm()`` calls inside this window short-circuit on
# the cached :class:`RegressionAlarmBlock` and emit ZERO transition events.
_ALARM_CACHE_TTL_SECONDS: float = 30.0


# ---------------------------------------------------------------------------
# Frozen snapshot — crosses the WriteQueue boundary detached from any ORM
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SuiteSnapshotInputs:
    """Pure data crossing the write-queue boundary.

    Frozen + slotted so the dataclass cannot accidentally pick up ORM
    attribute references. All fields are plain scalars / lists of dicts —
    safe to pass into the writer-engine session callback without re-loading
    the source RunRow.

    Foundation P4 invariant: ``_build_snapshot_inputs`` is the ONLY producer;
    callers downstream of the read session must NEVER re-touch the source
    ``RunRow`` instance to populate snapshot fields (would trigger
    ``MissingGreenlet`` on a detached ORM lazy-load).
    """

    suite_id: str
    source_run_id: str | None
    label: str
    tolerance_abs: float
    project_id: str | None
    repo_full_name: str | None
    prompts_snapshot: list[dict[str, Any]]
    baseline_scores: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SuiteRetireInputs:
    """Pure retire-write payload crossing the WriteQueue boundary.

    Spec §5: ``ValidationSuiteService.retire`` builds this dataclass inside
    its short read session, EXITS the session, then hands the frozen
    snapshot to ``_persist_suite_retire`` via ``WriteQueue.submit()``. No
    ORM references leak across the session boundary — Foundation P4
    detached-ORM-safe contract.

    Only the three state-transition columns are carried (``suite_id`` for
    the WHERE clause, ``retired_at`` + ``retired_reason`` for the SET
    clause). Every other column on ``ValidationSuite`` stays untouched —
    pinned by ``test_retire_does_not_mutate_other_columns``.

    Attributes
    ----------
    suite_id : str
        Target ValidationSuite.id (drives the UPDATE WHERE clause).
    retired_at : datetime
        Timezone-aware UTC datetime (``datetime.now(UTC)`` at retire time).
        Matches the ``DateTime`` column convention in ``app/models.py`` —
        SQLAlchemy stores the aware value and reads it back transparently
        for SQLite + PostgreSQL.
    retired_reason : str
        Caller-supplied reason, already ``.strip()``-ed by the service so
        the writer-engine UPDATE writes a normalized value.
    """

    suite_id: str
    retired_at: datetime
    retired_reason: str


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _build_prompts_snapshot(
    prompt_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project per-prompt ``RunRow.prompt_results`` rows into the snapshot shape.

    Position-correspondence invariant (§3 key invariant 2): the output order
    mirrors ``prompt_results`` exactly, so consumers can pair
    ``prompts_snapshot[i] <-> baseline_scores.per_prompt[i]`` by index.

    Each ``PromptSnapshotItem`` field is read tolerantly via ``dict.get`` —
    different generators (topic_probe vs future seed_agent forks) emit
    slightly different per-prompt shapes. Only ``raw_prompt`` is a hard
    requirement; the rest gracefully default to ``None``.
    """
    return [
        {
            "raw_prompt": r.get("raw_prompt", ""),
            "intent_label": r.get("intent_label"),
            "original_optimization_id": r.get("optimization_id"),
        }
        for r in prompt_results
    ]


def _derive_per_prompt_from_results(
    prompt_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fallback per-prompt builder when ``aggregate['per_prompt']`` is absent.

    Older RunRow shapes (pre-topic_probe canonical aggregate) and future
    generator forks may not populate ``aggregate.per_prompt`` — derive it
    from ``prompt_results`` per row instead so the snapshot's
    position-correspondence invariant holds either way.
    """
    return [
        {
            "raw_prompt_idx": i,
            "overall": float(r.get("overall_score") or 0.0),
            "dimensions": {
                k: float(v) for k, v in (r.get("dimensions") or {}).items()
                if isinstance(v, int | float)
            },
        }
        for i, r in enumerate(prompt_results)
    ]


def _build_baseline_scores(
    aggregate: dict[str, Any],
    prompt_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the canonical ``baseline_scores`` payload dict.

    Spec §3 baseline-scores shape: exactly the keys in
    :data:`_BASELINE_SCORES_KEYS`. Numeric fields default to ``0.0`` when
    absent so the resulting dict always validates against
    :class:`BaselineScoresPayload` — partial-key ``RunRow.aggregate`` rows
    (legacy / corrupted) do not crash the snapshot build.
    """
    raw_per_prompt: list[dict[str, Any]]
    if isinstance(aggregate.get("per_prompt"), list):
        raw_per_prompt = list(aggregate["per_prompt"])
    else:
        raw_per_prompt = _derive_per_prompt_from_results(prompt_results)

    return {
        "mean_overall": float(aggregate.get("mean_overall") or 0.0),
        "p5_overall": float(aggregate.get("p5_overall") or 0.0),
        "p50_overall": float(aggregate.get("p50_overall") or 0.0),
        "p95_overall": float(aggregate.get("p95_overall") or 0.0),
        "per_prompt": raw_per_prompt,
        "task_type_distribution": dict(
            aggregate.get("task_type_distribution") or {},
        ),
    }


def _build_snapshot_inputs(
    run: RunRow, label: str, tolerance_abs: float,
) -> SuiteSnapshotInputs:
    """Build the frozen snapshot from a hydrated RunRow.

    Must be invoked INSIDE the short read session — once the session exits,
    accessing ORM attributes on a detached instance triggers MissingGreenlet
    via SQLAlchemy's lazy-load path. The frozen dataclass extracts every
    field eagerly so post-session code paths never re-touch ``run``.

    Delegates shape projection to :func:`_build_prompts_snapshot` and
    :func:`_build_baseline_scores` so each transformation is a pure,
    independently-testable helper.
    """
    prompt_results: list[dict[str, Any]] = list(run.prompt_results or [])
    prompts_snapshot = _build_prompts_snapshot(prompt_results)
    baseline_scores = _build_baseline_scores(
        dict(run.aggregate or {}), prompt_results,
    )

    return SuiteSnapshotInputs(
        suite_id=uuid.uuid4().hex,
        source_run_id=run.id,
        label=label,
        tolerance_abs=tolerance_abs,
        project_id=run.project_id,
        repo_full_name=run.repo_full_name,
        prompts_snapshot=prompts_snapshot,
        baseline_scores=baseline_scores,
        created_at=datetime.now(UTC),
    )


def _build_alarm_query() -> Select[Any]:
    """Build the canonical regression-alarm SELECT statement (spec §5).

    Returns a SQLAlchemy :class:`Select` that joins ``validation_suite`` with
    its latest ``replay_run`` per suite. The statement is pure-compute — no
    DB I/O, no parameter binding to runtime values — so it is safely reusable
    across call sites and trivially unit-testable in isolation. The caller
    executes the returned select inside its own short read session.

    Spec §5 canonical SQL::

        SELECT s.id, s.label, s.tolerance_abs, s.baseline_scores,
               r.id AS replay_id, r.aggregate AS replay_aggregate, r.started_at
        FROM validation_suite s
        JOIN (
            SELECT suite_id, MAX(started_at) AS max_started
            FROM run_row
            WHERE mode = 'replay_run'
              AND status = 'completed'
              AND suite_id IS NOT NULL
            GROUP BY suite_id
        ) lr ON lr.suite_id = s.id
        JOIN run_row r ON r.suite_id = s.id
                       AND r.started_at = lr.max_started
                       AND r.mode = 'replay_run'
                       AND r.status = 'completed'
        WHERE s.retired_at IS NULL;

    Mechanics
    ---------
    The correlated ``MAX(started_at)`` subquery yields one row per
    ``suite_id`` carrying the newest ``replay_run.started_at``. The first
    JOIN filters retired suites by ``ValidationSuite.retired_at IS NULL``.
    The second JOIN re-hydrates the replay's ``id`` + ``aggregate`` columns
    (the subquery only carries the timestamp). Both ``mode`` and ``status``
    predicates are reasserted on the second JOIN — defensive narrowing so
    even if a future migration removes the subquery's predicate the second
    JOIN still excludes non-completed / non-replay rows.

    Result columns (consumed by :meth:`ValidationSuiteService.compute_regression_alarm`):

    * ``suite_id`` (str) — :class:`ValidationSuite.id`
    * ``label`` (str) — :class:`ValidationSuite.label`
    * ``tolerance_abs`` (float) — :class:`ValidationSuite.tolerance_abs`
    * ``baseline_scores`` (dict) — :class:`ValidationSuite.baseline_scores` JSON
    * ``replay_id`` (str) — :class:`RunRow.id` of the latest replay
    * ``replay_aggregate`` (dict) — :class:`RunRow.aggregate` JSON of the latest replay
    * ``replay_started_at`` (datetime) — :class:`RunRow.started_at` of the latest replay

    The rendered string contains BOTH ``validation_suite`` AND ``run_row``
    literal table names — used by Cycle 4 Test 26 to detect a true cache
    miss vs a cache hit (cache hits do not open a read session at all and
    never render this JOIN).

    Returns
    -------
    Select[Any]
        SQLAlchemy 2.x typed select statement. The ``Any`` row type
        reflects the heterogeneous column tuple — the caller addresses
        result rows by ``.suite_id`` / ``.label`` / etc. attribute access
        on the SQLAlchemy row proxy, not by tuple positional indexing.
    """
    # Inner subquery — one row per suite_id with the newest replay started_at.
    # Three predicates exclude non-completed replays + orphaned rows up-front
    # so the GROUP BY only aggregates valid candidates.
    latest_replay_subq = (
        select(
            RunRow.suite_id.label("suite_id"),
            func.max(RunRow.started_at).label("max_started"),
        )
        .where(RunRow.mode == "replay_run")
        .where(RunRow.status == "completed")
        .where(RunRow.suite_id.is_not(None))
        .group_by(RunRow.suite_id)
        .subquery("latest_replay")
    )

    # Outer SELECT — join the subquery back to ValidationSuite, then a
    # second join to run_row to hydrate the replay's id + aggregate columns
    # (subquery only carries the timestamp). Mode + status predicates on the
    # second join are defensive — they guard against any future migration
    # that loosens the subquery's filter.
    return (
        select(
            ValidationSuite.id.label("suite_id"),
            ValidationSuite.label.label("label"),
            ValidationSuite.tolerance_abs.label("tolerance_abs"),
            ValidationSuite.baseline_scores.label("baseline_scores"),
            RunRow.id.label("replay_id"),
            RunRow.aggregate.label("replay_aggregate"),
            RunRow.started_at.label("replay_started_at"),
        )
        .select_from(ValidationSuite)
        .join(
            latest_replay_subq,
            latest_replay_subq.c.suite_id == ValidationSuite.id,
        )
        .join(
            RunRow,
            (RunRow.suite_id == ValidationSuite.id)
            & (RunRow.started_at == latest_replay_subq.c.max_started)
            & (RunRow.mode == "replay_run")
            & (RunRow.status == "completed"),
        )
        .where(ValidationSuite.retired_at.is_(None))
    )


async def _persist_suite_create(
    db: AsyncSession, *, snapshot: SuiteSnapshotInputs,
) -> None:
    """Writer-engine callback — inserts the ValidationSuite row and commits.

    Runs inside the WriteQueue worker on a fresh writer-engine session. No
    ORM references from the read-side reach this body — only the frozen
    ``snapshot`` dataclass.
    """
    row = ValidationSuite(
        id=snapshot.suite_id,
        source_run_id=snapshot.source_run_id,
        label=snapshot.label,
        tolerance_abs=snapshot.tolerance_abs,
        project_id=snapshot.project_id,
        repo_full_name=snapshot.repo_full_name,
        prompts_snapshot=list(snapshot.prompts_snapshot),
        baseline_scores=dict(snapshot.baseline_scores),
        created_at=snapshot.created_at,
        retired_at=None,
        retired_reason=None,
    )
    db.add(row)
    await db.commit()


async def _persist_suite_retire(
    db: AsyncSession, *, inputs: SuiteRetireInputs,
) -> None:
    """Writer-engine callback — updates ``retired_at`` + ``retired_reason``.

    Runs inside the WriteQueue worker on a fresh writer-engine session. The
    UPDATE clause is narrowly scoped to the two state-transition columns so
    no other column can be accidentally rewritten — pinned by
    ``test_retire_does_not_mutate_other_columns``.
    """
    await db.execute(
        update(ValidationSuite)
        .where(ValidationSuite.id == inputs.suite_id)
        .values(
            retired_at=inputs.retired_at,
            retired_reason=inputs.retired_reason,
        ),
    )
    await db.commit()


def _log_validation_suite_phase(
    trace_id: str, duration_ms: int, result: dict[str, Any],
) -> None:
    """Shared scaffold for both create + retire JSONL trace emissions.

    Spec §9 trace tagging — every ValidationSuite-lifecycle event emits under
    ``phase="validation_suite"``; callers filter on ``result["action"]``
    (``create`` vs ``retire``) to disambiguate. Lazy-instantiates
    ``TraceLogger`` reading ``app.config.DATA_DIR`` at call time so test-side
    ``monkeypatch.setattr(cfg_mod, "DATA_DIR", tmp_path)`` works.

    Trace failure must never break a successful suite-lifecycle write —
    ``OSError`` / ``RuntimeError`` / ``ValueError`` are logged at DEBUG and
    swallowed; any other exception type propagates (genuine bug).
    """
    try:
        from app.services.trace_logger import TraceLogger

        traces_dir = _config_mod.DATA_DIR / "traces"
        logger_ = TraceLogger(traces_dir)
        logger_.log_phase(
            trace_id=trace_id,
            phase="validation_suite",
            duration_ms=duration_ms,
            tokens_in=0,
            tokens_out=0,
            model="",
            provider="",
            result=result,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        # Trace failure must never break the host suite-lifecycle write.
        logger.debug("validation_suite trace emission failed: %s", exc)


def _emit_suite_retire_trace(inputs: SuiteRetireInputs, duration_ms: int) -> None:
    """Append a ``phase="validation_suite"`` retire entry to today's JSONL."""
    _log_validation_suite_phase(
        trace_id=inputs.suite_id,
        duration_ms=duration_ms,
        result={
            "action": "retire",
            "suite_id": inputs.suite_id,
            "retired_at": inputs.retired_at.isoformat(),
            "retired_reason": inputs.retired_reason,
        },
    )


def _emit_suite_create_trace(snapshot: SuiteSnapshotInputs, duration_ms: int) -> None:
    """Append a ``phase="validation_suite"`` create entry to today's JSONL."""
    _log_validation_suite_phase(
        trace_id=snapshot.suite_id,
        duration_ms=duration_ms,
        result={
            "action": "create",
            "suite_id": snapshot.suite_id,
            "source_run_id": snapshot.source_run_id,
            "label": snapshot.label,
            "tolerance_abs": snapshot.tolerance_abs,
            "prompts_count": len(snapshot.prompts_snapshot),
            "baseline_mean": snapshot.baseline_scores.get("mean_overall"),
            "project_id": snapshot.project_id,
        },
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ValidationSuiteService:
    """Service for ValidationSuite create / retire / get / list / regression-alarm.

    Per Foundation P4 contract: NEVER holds a DB session across an LLM call
    or a WriteQueue.submit. Reads happen in short ``async with
    async_session_factory()`` blocks; writes route through ``write_queue``.

    Create / retire / get / list / list_replays are stateless on the service
    instance — they pass through to the DB on every call. The regression-
    alarm path keeps two pieces of in-memory state on the instance, both
    initialized empty in :meth:`__init__`:

    * ``_alarm_cache`` — 30s TTL cache holding the last computed
      :class:`RegressionAlarmBlock`. Test-seam :meth:`_invalidate_alarm_cache`
      drops it so the next call re-queries.
    * ``_prior_alarm_states`` — per-suite state map driving the
      ``regression_alarm_transition`` event. Values are
      ``'none' | 'nominal' | 'firing'`` per spec §9. Resets on process
      restart (acceptable for status-display observability).

    Two service instances NEVER share cached state. Each
    ``ValidationSuiteService()`` constructor invocation starts with both
    pieces of state empty — pinned by spec §9 + Cycle 4 Test 26 cache-seam
    contract.
    """

    def __init__(self) -> None:
        # 30s TTL cache for the regression-alarm block. ``None`` means "no
        # cached value yet — next call MUST query the DB". When populated,
        # the tuple is ``(monotonic_stored_at, cached_block)``.
        self._alarm_cache: tuple[float, RegressionAlarmBlock] | None = None
        # Per-suite alarm-state map driving spec §9
        # ``regression_alarm_transition`` event firing. Suites not present
        # in the map default to ``'none'`` on next lookup.
        self._prior_alarm_states: dict[str, str] = {}

    def _invalidate_alarm_cache(self) -> None:
        """Test-seam: drop the 30s TTL cache.

        Pinned by Cycle 4 Tests 26 + 27 — the next
        :meth:`compute_regression_alarm` invocation MUST re-query the
        DB after this is called, regardless of how recently the previous
        call ran. Production code paths do not call this; only tests
        advancing through state transitions without sleeping past the TTL
        rely on the seam.
        """
        self._alarm_cache = None

    async def create_from_run(
        self,
        run_id: str,
        *,
        label: str,
        tolerance_abs: float = 0.5,
        db: AsyncSession | None = None,  # noqa: ARG002 — signature parity per spec §5
        write_queue: WriteQueue,
    ) -> ValidationSuiteOut:
        """Fork a completed ``topic_probe`` RunRow into an immutable suite.

        Read step opens a SHORT session via ``async_session_factory``,
        validates the source run, builds the frozen snapshot, then EXITS the
        session before any write. Persist step submits the snapshot through
        ``write_queue`` with ``operation_label='validation_suite_create'``.
        Emits ``validation_suite_created`` event + JSONL trace after the
        write commits.

        Parameters
        ----------
        run_id : str
            The source RunRow.id. MUST exist, be ``status='completed'``, be
            ``mode='topic_probe'``, and carry an ``aggregate`` with
            ``mean_overall``.
        label : str
            Caller-supplied label, 1-120 chars.
        tolerance_abs : float, default 0.5
            Absolute-score tolerance the regression alarm compares against.
        db : AsyncSession | None
            Signature-only parameter for spec parity — the service always
            opens its own short read session via ``async_session_factory``
            (Foundation P4 detached-ORM-safe contract). Reserved for future
            test-injection patterns.
        write_queue : WriteQueue
            Required collaborator — terminal write routes through this queue.

        Returns
        -------
        ValidationSuiteOut
            Fully-validated response built from the snapshot (no post-write
            DB re-read needed — the snapshot has every output field).

        Raises
        ------
        ValueError
            One of: ``run_not_found``, ``run_not_completed``,
            ``not_a_probe_run``, ``run_missing_aggregate``.
        """
        start = time.monotonic()

        # ============ STEP 1: short read session ============
        # Use the canonical async_session_factory — the read session MUST
        # exit before any write_queue.submit call (Foundation P4 contract,
        # pinned by ``test_create_from_run_detached_orm_safe``).
        #
        # Semantically equivalent to:
        #     async with _database_mod.async_session_factory() as read_db:
        #         ...
        # We expand it to manual ``__aenter__`` / ``__aexit__`` calls so the
        # detached-ORM test's instance-level ``inner_ctx.__aexit__ = ...``
        # wrapper actually fires. Python's data model resolves ``async with``
        # dunder methods on the *class* (descriptor protocol), which would
        # bypass any instance-attribute patch on the AsyncSession returned
        # by ``async_sessionmaker.__call__``. Explicit attribute lookup goes
        # through normal instance-first resolution, honouring the test's
        # call-order recorder. Behaviour is identical to ``async with`` for
        # production code paths — only the test patch's visibility changes.
        session_ctx = _database_mod.async_session_factory()
        read_db = await session_ctx.__aenter__()
        try:
            result = await read_db.execute(
                select(RunRow).where(RunRow.id == run_id)
            )
            run = result.scalar_one_or_none()

            if run is None:
                raise ValueError("run_not_found")
            if run.status != "completed":
                raise ValueError("run_not_completed")
            if run.mode != "topic_probe":
                raise ValueError("not_a_probe_run")
            aggregate = run.aggregate or {}
            if not aggregate or "mean_overall" not in aggregate:
                raise ValueError("run_missing_aggregate")

            # Build the frozen snapshot while the session is still open —
            # detaching the ORM instance and reading attributes outside the
            # session would trigger MissingGreenlet on lazy-loaded columns.
            snapshot = _build_snapshot_inputs(run, label, tolerance_abs)
        finally:
            await session_ctx.__aexit__(None, None, None)

        # ============ STEP 2: persist via WriteQueue ============
        # Session is closed — safe to await on the queue without violating
        # the detached-ORM contract.
        async def _work(writer_db: AsyncSession) -> None:
            await _persist_suite_create(writer_db, snapshot=snapshot)

        await write_queue.submit(
            _work,
            timeout=30,
            operation_label="validation_suite_create",
        )

        # ============ STEP 3: post-commit event + trace ============
        # Event emission: spec §9 — fires AFTER write_queue.submit completes
        # successfully. Failed submits raise before reaching this line so no
        # event is published on write failure.
        event_bus.publish("validation_suite_created", {
            "suite_id": snapshot.suite_id,
            "source_run_id": snapshot.source_run_id,
            "label": snapshot.label,
            "tolerance_abs": snapshot.tolerance_abs,
            "prompts_count": len(snapshot.prompts_snapshot),
            "baseline_mean": snapshot.baseline_scores.get("mean_overall"),
            "project_id": snapshot.project_id,
        })

        duration_ms = int((time.monotonic() - start) * 1000)
        _emit_suite_create_trace(snapshot, duration_ms)

        # ============ STEP 4: build response from the snapshot ============
        # No post-write DB read needed — the snapshot has every output field.
        # Pydantic's default validator handles the dict-to-nested-model
        # coercion (PromptSnapshotItem + BaselineScoresPayload + PerPromptScore).
        return _suite_out_from_snapshot(snapshot)

    async def retire(
        self,
        suite_id: str,
        *,
        reason: str,
        db: AsyncSession | None = None,  # noqa: ARG002 — signature parity per spec §5
        write_queue: WriteQueue,
    ) -> None:
        """Idempotent soft-delete of a ValidationSuite.

        Spec §5 retire body:

        * **First retire** — read suite → build :class:`SuiteRetireInputs` →
          submit through ``write_queue`` with
          ``operation_label='validation_suite_retire'`` → emit
          ``validation_suite_retired`` event → write JSONL retire trace.
        * **Re-retire** of an already-retired suite — second and subsequent
          calls short-circuit BEFORE :meth:`WriteQueue.submit` and return
          successfully. Three observability invariants hold on the no-op
          path: **NO DB write**, **NO event publish**, **NO JSONL trace
          entry**. Pinned by tests
          ``test_retire_is_idempotent_on_already_retired``,
          ``test_retire_idempotent_no_event_emitted_on_re_retire``, and
          ``test_retire_uses_validation_suite_retire_op_label``.

        The short-circuit ordering matters: the spec §4 operation-label
        clause + §9 event-emission contract both pin "first retire only"
        observability, so the ``retired_at is not None`` guard runs inside
        the read session, before any queue submission.

        Parameters
        ----------
        suite_id : str
            Target suite. Missing id raises ``ValueError("suite_not_found")``.
        reason : str
            Caller-supplied retirement reason. Stripped before persistence
            (matches the existing P4 routing convention). Pydantic-validated
            at the router boundary against :class:`RetireSuiteRequest`
            (``min_length=1, max_length=500``); the service trusts that
            validation.
        db : AsyncSession | None
            Signature-only — the service always opens a short read session
            via ``async_session_factory`` (Foundation P4 contract).
        write_queue : WriteQueue
            Required collaborator — terminal write routes through this queue.

        Raises
        ------
        ValueError
            ``suite_not_found`` when ``suite_id`` does not exist — same
            canonical code as :meth:`get` so the REST layer can map both
            to a single 404 envelope.
        """
        start = time.monotonic()

        # ============ STEP 1: short read session — fetch + idempotency guard ===
        session_ctx = _database_mod.async_session_factory()
        read_db = await session_ctx.__aenter__()
        try:
            result = await read_db.execute(
                select(ValidationSuite).where(ValidationSuite.id == suite_id)
            )
            suite = result.scalar_one_or_none()
            if suite is None:
                raise ValueError("suite_not_found")
            if suite.retired_at is not None:
                # Idempotent no-op — spec §5 + §9 + §4 all pin this branch
                # as "no write / no event / no trace / no op-label". Return
                # immediately so callers see success but observability stays
                # silent.
                return
            inputs = SuiteRetireInputs(
                suite_id=suite_id,
                retired_at=datetime.now(UTC),
                retired_reason=reason.strip(),
            )
        finally:
            await session_ctx.__aexit__(None, None, None)

        # ============ STEP 2: persist via WriteQueue ============
        async def _work(writer_db: AsyncSession) -> None:
            await _persist_suite_retire(writer_db, inputs=inputs)

        await write_queue.submit(
            _work,
            timeout=30,
            operation_label="validation_suite_retire",
        )

        # ============ STEP 3: post-commit event + trace ============
        # Spec §9 — emitted ONLY when state actually transitions (first
        # retire). The idempotency short-circuit above bypasses this block
        # entirely on no-op re-retires.
        event_bus.publish("validation_suite_retired", {
            "suite_id": inputs.suite_id,
            "reason": inputs.retired_reason,
        })

        duration_ms = int((time.monotonic() - start) * 1000)
        _emit_suite_retire_trace(inputs, duration_ms)

    async def get(
        self,
        suite_id: str,
        *,
        db: AsyncSession | None = None,  # noqa: ARG002 — signature parity per spec §5
    ) -> ValidationSuiteOut:
        """Return a full :class:`ValidationSuiteOut` for ``suite_id``.

        Spec §5 — thin read-only wrapper. Opens a short read session via
        ``async_session_factory`` (Foundation P4 contract), fetches the
        suite, and adapts it through ``ValidationSuiteOut.model_validate``
        (the schema is configured with ``from_attributes=True``).

        Parameters
        ----------
        suite_id : str
            Target suite id. Missing id raises
            ``ValueError("suite_not_found")``.
        db : AsyncSession | None
            Signature-only — the service always opens a short read session
            via ``async_session_factory`` (Foundation P4 contract).

        Returns
        -------
        ValidationSuiteOut
            Fully-populated read view — includes ``prompts_snapshot`` and
            ``baseline_scores`` parsed as nested Pydantic models. Both
            ``retired_at`` and ``retired_reason`` are ``None`` for active
            suites.

        Raises
        ------
        ValueError
            ``suite_not_found`` when ``suite_id`` does not exist — same
            error code as :meth:`retire` so the REST layer can map both
            to a single 404 envelope.
        """
        session_ctx = _database_mod.async_session_factory()
        read_db = await session_ctx.__aenter__()
        try:
            result = await read_db.execute(
                select(ValidationSuite).where(ValidationSuite.id == suite_id)
            )
            suite = result.scalar_one_or_none()
            if suite is None:
                raise ValueError("suite_not_found")
            return ValidationSuiteOut.model_validate(suite)
        finally:
            await session_ctx.__aexit__(None, None, None)

    async def list(
        self,
        *,
        include_retired: bool = False,
        project_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
        db: AsyncSession | None = None,  # noqa: ARG002 — signature parity per spec §5
    ) -> ValidationSuiteListResponse:
        """Paginated list of validation suites.

        Spec §4 / §5 — backs ``GET /api/suites``. Default filters out
        retired suites (``retired_at IS NULL``); ``include_retired=True``
        returns the full set. ``project_id``, when provided, narrows to
        suites whose ``project_id`` matches.

        Order is ``created_at DESC`` (newest first) to match the
        ``ix_validation_suite_active`` partial-index direction.

        Parameters
        ----------
        include_retired : bool, default False
            When ``False`` (the default), filters out retired suites
            (``retired_at IS NULL``). When ``True``, returns the union of
            active + retired.
        project_id : str | None, default None
            When provided, narrows to suites whose ``project_id`` matches;
            None (default) returns suites across all projects.
        limit : int, default 20
            Page size. Pydantic ``Query(ge=1, le=100)`` clamps at the
            router boundary; defensive runtime validation lives there.
        offset : int, default 0
            Page offset. Pydantic ``Query(ge=0)`` clamps at the router.
        db : AsyncSession | None
            Signature-only — the service always opens a short read session.

        Returns
        -------
        ValidationSuiteListResponse
            Canonical paginated envelope. ``total`` reflects the FILTERED
            set, NOT the page slice; ``limit`` + ``offset`` shape the
            ``items`` window only. A filter yielding zero rows returns
            ``total=0, count=0, items=[], has_more=False,
            next_offset=None`` (empty envelope, never an error).
        """
        session_ctx = _database_mod.async_session_factory()
        read_db = await session_ctx.__aenter__()
        try:
            base = select(ValidationSuite)
            if not include_retired:
                base = base.where(ValidationSuite.retired_at.is_(None))
            if project_id is not None:
                base = base.where(ValidationSuite.project_id == project_id)

            total_q = select(func.count()).select_from(base.subquery())
            total = int((await read_db.execute(total_q)).scalar_one())

            page_q = (
                base.order_by(ValidationSuite.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            rows = (await read_db.execute(page_q)).scalars().all()
            items = [_suite_list_item_from_orm(row) for row in rows]
        finally:
            await session_ctx.__aexit__(None, None, None)

        has_more = offset + len(items) < total
        next_offset = offset + len(items) if has_more else None

        return ValidationSuiteListResponse(
            total=total,
            count=len(items),
            offset=offset,
            items=items,
            has_more=has_more,
            next_offset=next_offset,
        )

    async def list_replays(
        self,
        suite_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
        db: AsyncSession | None = None,  # noqa: ARG002 — signature parity per spec §5
    ) -> RunListResponse:
        """Paginated list of replay runs scoped to ``suite_id``.

        Spec §4 / §5 — backs ``GET /api/suites/{id}/replays``. Returns
        rows scoped to BOTH ``RunRow.mode == 'replay_run'`` AND
        ``RunRow.suite_id == suite_id``. Ordering is ``started_at DESC``
        per the ``ix_run_row_suite_id (suite_id, started_at DESC)`` index.

        Per spec §5 a non-existent ``suite_id`` returns an empty paginated
        envelope (``total=0, items=[]``) rather than raising
        ``suite_not_found`` — this matches REST convention for resource-
        scoped collection endpoints (``GET /resource/{x}/items`` returns
        ``[]`` when ``x`` is unknown). The suite-existence check, if
        needed, happens at the router layer via :meth:`get`.

        Parameters
        ----------
        suite_id : str
            Parent suite. Non-existent ids yield an empty envelope, NOT
            an error — see note above.
        limit : int, default 20
            Page size. Pydantic ``Query(ge=1, le=100)`` clamps at the
            router boundary.
        offset : int, default 0
            Page offset. Pydantic ``Query(ge=0)`` clamps at the router.
        db : AsyncSession | None
            Signature-only — the service always opens a short read session.

        Returns
        -------
        RunListResponse
            Canonical pagination envelope (spec §4 line 275) — ``items``
            is ``list[RunSummary]`` so the row id is accessible as
            ``item.id`` and the started timestamp as ``item.started_at``,
            matching the contract pinned by
            ``test_list_replays_returns_only_replay_run_mode_rows_for_suite``.
        """
        session_ctx = _database_mod.async_session_factory()
        read_db = await session_ctx.__aenter__()
        try:
            base = (
                select(RunRow)
                .where(RunRow.mode == "replay_run")
                .where(RunRow.suite_id == suite_id)
            )
            total_q = select(func.count()).select_from(base.subquery())
            total = int((await read_db.execute(total_q)).scalar_one())

            page_q = (
                base.order_by(RunRow.started_at.desc()).limit(limit).offset(offset)
            )
            rows = (await read_db.execute(page_q)).scalars().all()
            items = [_run_summary_from_row(row) for row in rows]
        finally:
            await session_ctx.__aexit__(None, None, None)

        has_more = offset + len(items) < total
        next_offset = offset + len(items) if has_more else None

        return RunListResponse(
            total=total,
            count=len(items),
            offset=offset,
            items=items,
            has_more=has_more,
            next_offset=next_offset,
        )

    async def compute_regression_alarm(
        self,
        *,
        db: AsyncSession | None = None,  # noqa: ARG002 — signature parity per spec §5
    ) -> RegressionAlarmBlock:
        """Run the regression-alarm query + transition events for all active suites.

        Spec §5 alarm body + spec §9 ``regression_alarm_transition`` event.

        Behaviour
        ---------
        1. **30s TTL cache check** (instance-scoped). When the previous
           successful result is younger than :data:`_ALARM_CACHE_TTL_SECONDS`,
           the cached :class:`RegressionAlarmBlock` is returned immediately —
           NO DB query, NO transition events. Cycle 4 Test 26 pins this.
        2. **Cache miss path** — opens a short read session via
           ``async_session_factory`` (Foundation P4 contract), counts active
           suites, executes :func:`_build_alarm_query` (canonical JOIN
           extracted as a pure helper), applies the Python-side filter
           ``|delta| > tolerance_abs``, and builds the
           :class:`RegressionAlarmBlock` response.
        3. **State-transition events** fire AFTER the SQL runs, ONLY for
           suites whose current state (``'firing'``/``'nominal'``) differs
           from ``self._prior_alarm_states.get(suite_id, 'none')`` per spec
           §9. Steady-state (no transition) calls emit ZERO events. Suites
           without any completed replays are NOT recorded in the prior-
           state map — they default to ``'none'`` on the next lookup.
        4. **Cache write** — the freshly-built block is cached together
           with ``time.monotonic()`` so subsequent calls within the TTL
           window short-circuit at step 1.

        State machine
        -------------
        Each suite tracks one of three states in
        :attr:`_prior_alarm_states`: ``'none'`` (default for unknown suites,
        not stored explicitly), ``'nominal'`` (suite has a latest completed
        replay within tolerance), ``'firing'`` (latest replay exceeds
        tolerance). The legal transitions are::

            none    ──insert replay──▶    nominal
            none    ──insert replay──▶    firing
            nominal ──newer replay──▶    firing
            firing  ──newer replay──▶    nominal
            *       ──retire suite──▶    (drops out of map)

        The prior-state map is REPLACED (not merged) at end-of-call with the
        freshly computed ``new_states`` dict. Consequence: when a suite is
        retired between calls, OR its last completed replay is deleted, OR
        its replay rows fall back to ``status != 'completed'``, the suite
        silently drops out of the map. The next time that suite re-enters
        the JOIN result set (e.g., a new replay is inserted, or the suite
        is un-retired by a future operator action), the previous-state
        lookup yields ``'none'`` and the canonical ``none → firing`` /
        ``none → nominal`` transition fires. This is deliberate — retiring
        and resurrecting a suite is a semantic reset, not a continuation.

        Concurrency
        -----------
        Two concurrent ``compute_regression_alarm`` calls on the SAME
        service instance are NOT serialized — each may read the cache,
        race to open a read session, and race to overwrite
        :attr:`_alarm_cache` + :attr:`_prior_alarm_states`. The spec
        §5 alarm path is a per-process status-display computation that
        runs every 30s on the health endpoint; a transient duplicate
        event emission under contention is acceptable. The cache-tuple
        write is a single atomic assignment in CPython so the cache
        never tears (no half-populated state observable). Distinct
        :class:`ValidationSuiteService` instances NEVER share state by
        construction.

        Parameters
        ----------
        db : AsyncSession | None
            Signature-only — the service always opens its own short read
            session via ``async_session_factory`` (Foundation P4 contract).
            Reserved for future test-injection patterns.

        Returns
        -------
        RegressionAlarmBlock
            Canonical alarm payload — ``suites_total`` counts ACTIVE
            (``retired_at IS NULL``) suites, ``suites_in_alarm`` counts
            firing entries in ``latest_alarms``. Empty ``latest_alarms``
            with non-zero ``suites_total`` is the steady-nominal state.
        """
        # ============ STEP 1: TTL cache check ============
        # Instance-scoped so two service instances never share state. The
        # cache tuple stores ``time.monotonic()`` so wall-clock jumps don't
        # invalidate live cache entries.
        now = time.monotonic()
        if (
            self._alarm_cache is not None
            and (now - self._alarm_cache[0]) < _ALARM_CACHE_TTL_SECONDS
        ):
            return self._alarm_cache[1]

        # ============ STEP 2: read session — alarm SQL + counts ============
        # Short session per Foundation P4 contract. Two queries fire here:
        # the active-suites count (drives ``suites_total``) and the alarm
        # JOIN itself (drives ``latest_alarms`` + ``suites_in_alarm``).
        # The JOIN's rendered SQL contains BOTH ``validation_suite`` and
        # ``run_row`` literal table names — used by Cycle 4 Test 26 to
        # detect a true cache miss vs cache hit (cache hits do not open
        # this session at all and never render the JOIN).
        session_ctx = _database_mod.async_session_factory()
        read_db = await session_ctx.__aenter__()
        try:
            # ---- 2a: suites_total — active (non-retired) suite count ----
            suites_total_q = select(func.count()).select_from(
                ValidationSuite.__table__,
            ).where(ValidationSuite.retired_at.is_(None))
            suites_total = int(
                (await read_db.execute(suites_total_q)).scalar_one(),
            )

            # ---- 2b: alarm JOIN — latest completed replay per active suite ----
            # Statement construction extracted to :func:`_build_alarm_query`
            # (pure compute, no I/O). The service is responsible only for
            # executing the returned select and shaping rows into entries.
            alarm_rows = (await read_db.execute(_build_alarm_query())).all()
        finally:
            await session_ctx.__aexit__(None, None, None)

        # ============ STEP 3: Python filter + entry build + state map ============
        # Per spec §5 verbatim:
        #   Python-side: filter where
        #     replay_aggregate['mean_overall']
        #       < baseline_scores['mean_overall'] - tolerance_abs
        # i.e. a REGRESSION-direction filter — only *drops* below baseline
        # by more than ``tolerance_abs`` fire. Improvements above baseline
        # (even those exceeding tolerance) do NOT fire — they are still
        # ``nominal``. Equivalently: ``delta < -tolerance_abs`` where
        # ``delta = latest_mean - baseline_mean``.
        #
        # Boundary semantics: strict ``<``, not ``<=``. A replay that
        # exactly matches ``baseline - tolerance_abs`` is nominal (matches
        # spec §5 line 631 verbatim and Test 25's tolerance-boundary
        # fixture).
        #
        # The signed delta is preserved on
        # :class:`RegressionAlarmEntry.delta_abs` (negative = regression,
        # always negative for firing entries under this filter). The
        # ``delta_abs`` field name matches spec §4 line 446 verbatim.
        latest_alarms: list[RegressionAlarmEntry] = []
        new_states: dict[str, str] = {}
        for row in alarm_rows:
            suite_id = row.suite_id
            baseline_scores = row.baseline_scores or {}
            baseline_mean = float(baseline_scores.get("mean_overall") or 0.0)
            replay_aggregate = row.replay_aggregate or {}
            latest_mean = float(replay_aggregate.get("mean_overall") or 0.0)
            delta = latest_mean - baseline_mean
            tolerance_abs = float(row.tolerance_abs)

            # Spec §5 regression-direction filter:
            #   latest_mean < baseline_mean - tolerance_abs
            is_firing = latest_mean < baseline_mean - tolerance_abs
            new_states[suite_id] = "firing" if is_firing else "nominal"

            if is_firing:
                # SQLite stores DateTime as ISO strings — the round-trip
                # drops ``tzinfo``. Reattach UTC here so the alarm entry's
                # ``latest_replay_at`` round-trips byte-equal to the
                # source RunRow's ``started_at``. Matches the pattern in
                # ``app/services/gc.py:381`` + ``taxonomy/snapshot.py:206``.
                replay_started_at = row.replay_started_at
                if replay_started_at is not None and replay_started_at.tzinfo is None:
                    replay_started_at = replay_started_at.replace(tzinfo=UTC)
                latest_alarms.append(
                    RegressionAlarmEntry(
                        suite_id=suite_id,
                        label=row.label,
                        baseline_mean=baseline_mean,
                        latest_mean=latest_mean,
                        delta_abs=delta,
                        tolerance_abs=tolerance_abs,
                        latest_replay_id=row.replay_id,
                        latest_replay_at=replay_started_at,
                    ),
                )

        block = RegressionAlarmBlock(
            suites_total=suites_total,
            suites_in_alarm=len(latest_alarms),
            latest_alarms=latest_alarms,
        )

        # ============ STEP 4: state-transition events (spec §9) ============
        # Compare each suite's NEW state against the prior-state map. Emit
        # ``regression_alarm_transition`` ONLY when the state actually
        # changed (spec §9 line 1341: "only when ≥1 suite's alarm state
        # transitioned"). Build a per-entry index of the firing rows so we
        # can populate the event payload's ``baseline_mean`` / ``latest_mean``
        # / ``delta_abs`` fields directly (spec §9 payload columns).
        firing_index: dict[str, RegressionAlarmEntry] = {
            entry.suite_id: entry for entry in latest_alarms
        }
        for suite_id, new_state in new_states.items():
            prior_state = self._prior_alarm_states.get(suite_id, "none")
            if new_state == prior_state:
                continue

            # Resolve mean / delta columns for the event payload. Firing
            # entries carry the full row; nominal suites are not in
            # ``firing_index`` so we look up the original alarm row for
            # the baseline + latest columns.
            entry = firing_index.get(suite_id)
            if entry is not None:
                payload_baseline = entry.baseline_mean
                payload_latest = entry.latest_mean
                payload_delta = entry.delta_abs
                payload_label = entry.label
            else:
                # Suite is nominal — pull payload columns from the alarm
                # row's source data.
                source_row = next(
                    (r for r in alarm_rows if r.suite_id == suite_id),
                    None,
                )
                if source_row is None:
                    # Defensive — should not happen because new_states is
                    # populated from alarm_rows. Skip silently rather than
                    # crash on a malformed transition.
                    continue
                baseline_scores = source_row.baseline_scores or {}
                replay_aggregate = source_row.replay_aggregate or {}
                payload_baseline = float(
                    baseline_scores.get("mean_overall") or 0.0,
                )
                payload_latest = float(
                    replay_aggregate.get("mean_overall") or 0.0,
                )
                payload_delta = payload_latest - payload_baseline
                payload_label = source_row.label

            event_bus.publish(
                "regression_alarm_transition",
                {
                    "suite_id": suite_id,
                    "label": payload_label,
                    "previous_state": prior_state,
                    "new_state": new_state,
                    "baseline_mean": payload_baseline,
                    "latest_mean": payload_latest,
                    "delta_abs": payload_delta,
                },
            )

        # Replace the prior-state map with the freshly computed set.
        # Suites that no longer have a completed replay (e.g., retired
        # between calls, or all replays deleted) drop out — their next
        # firing will see ``prior_state == 'none'`` and emit the canonical
        # ``none → firing`` / ``none → nominal`` transition.
        self._prior_alarm_states = new_states

        # ============ STEP 5: write the TTL cache + return ============
        # Cache stored AFTER successful build + event emission so partial
        # failures (DB error, event-bus error) do not poison the cache.
        self._alarm_cache = (now, block)
        return block


def _suite_list_item_from_orm(row: ValidationSuite) -> ValidationSuiteListItem:
    """Project a ValidationSuite ORM row into the lightweight list view.

    ``prompts_count`` is derived from ``len(prompts_snapshot)``;
    ``baseline_mean`` is read out of the JSON column. Both are excluded
    from the heavy :class:`ValidationSuiteOut` to keep paginated listing
    cheap (no nested-model parsing per row).
    """
    prompts = row.prompts_snapshot or []
    scores = row.baseline_scores or {}
    return ValidationSuiteListItem(
        id=row.id,
        source_run_id=row.source_run_id,
        label=row.label,
        tolerance_abs=row.tolerance_abs,
        project_id=row.project_id,
        repo_full_name=row.repo_full_name,
        created_at=row.created_at,
        retired_at=row.retired_at,
        prompts_count=len(prompts),
        baseline_mean=float(scores.get("mean_overall", 0.0) or 0.0),
    )


def _run_summary_from_row(row: RunRow) -> RunSummary:
    """Project a RunRow into the canonical :class:`RunSummary` list item.

    Mirrors ``routers/runs.py:_serialize_summary`` exactly so the replay-list
    envelope is byte-identical to ``GET /api/runs?mode=replay_run`` filtered
    rows. Used by :meth:`ValidationSuiteService.list_replays`.
    """
    return RunSummary(
        id=row.id,
        mode=row.mode,  # type: ignore[arg-type]  # plain String column, Literal validates
        status=row.status,  # type: ignore[arg-type]  # plain String column, Literal validates
        started_at=row.started_at,
        completed_at=row.completed_at,
        project_id=row.project_id,
        repo_full_name=row.repo_full_name,
        topic=row.topic,
        intent_hint=row.intent_hint,
        prompts_generated=row.prompts_generated or 0,
    )


def _suite_out_from_snapshot(snapshot: SuiteSnapshotInputs) -> ValidationSuiteOut:
    """Adapt a frozen snapshot to the public ``ValidationSuiteOut`` shape.

    Relies on Pydantic's nested ``model_validate`` to coerce the snapshot's
    ``dict[str, Any]`` payloads into the typed nested models
    (``PromptSnapshotItem``, ``PerPromptScore``, ``BaselineScoresPayload``)
    in a single call — no per-field re-extraction, and the canonical key
    set lives in one place (the Pydantic schema declaration).
    """
    return ValidationSuiteOut.model_validate({
        "id": snapshot.suite_id,
        "source_run_id": snapshot.source_run_id,
        "label": snapshot.label,
        "tolerance_abs": snapshot.tolerance_abs,
        "project_id": snapshot.project_id,
        "repo_full_name": snapshot.repo_full_name,
        "created_at": snapshot.created_at,
        "retired_at": None,
        "retired_reason": None,
        "prompts_snapshot": snapshot.prompts_snapshot,
        "baseline_scores": snapshot.baseline_scores,
    })
