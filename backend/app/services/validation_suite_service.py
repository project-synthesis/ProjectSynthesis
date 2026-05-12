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
``list``. Cycle 4 adds ``compute_regression_alarm``.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app import config as _config_mod
from app import database as _database_mod
from app.models import RunRow, ValidationSuite
from app.schemas.validation_suite import ValidationSuiteOut
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


def _emit_suite_create_trace(snapshot: SuiteSnapshotInputs, duration_ms: int) -> None:
    """Append a ``phase="validation_suite"`` entry to today's traces JSONL.

    Per spec §9 trace tagging. Lazy-instantiates ``TraceLogger`` reading
    ``app.config.DATA_DIR`` at call time — preserves test-side
    ``monkeypatch.setattr(cfg_mod, "DATA_DIR", tmp_path)`` semantics.
    """
    try:
        from app.services.trace_logger import TraceLogger

        traces_dir = _config_mod.DATA_DIR / "traces"
        logger_ = TraceLogger(traces_dir)
        logger_.log_phase(
            trace_id=snapshot.suite_id,
            phase="validation_suite",
            duration_ms=duration_ms,
            tokens_in=0,
            tokens_out=0,
            model="",
            provider="",
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
    except (OSError, RuntimeError, ValueError) as exc:
        # Trace failure must never break suite creation.
        logger.debug("validation_suite trace emission failed: %s", exc)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ValidationSuiteService:
    """Stateless service for ValidationSuite create / retire / get / list.

    Per Foundation P4 contract: NEVER holds a DB session across an LLM call
    or a WriteQueue.submit. Reads happen in short ``async with
    async_session_factory()`` blocks; writes route through ``write_queue``.
    """

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
