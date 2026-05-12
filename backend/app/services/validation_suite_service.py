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
from app.schemas.validation_suite import (
    BaselineScoresPayload,
    PerPromptScore,
    PromptSnapshotItem,
    ValidationSuiteOut,
)
from app.services.event_bus import event_bus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.write_queue import WriteQueue


logger = logging.getLogger(__name__)


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
    """

    suite_id: str
    source_run_id: str | None
    label: str
    tolerance_abs: float
    project_id: str | None
    repo_full_name: str | None
    prompts_snapshot: list[dict]
    baseline_scores: dict
    created_at: datetime


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _build_snapshot_inputs(
    run: RunRow, label: str, tolerance_abs: float,
) -> SuiteSnapshotInputs:
    """Build the frozen snapshot from a hydrated RunRow.

    Must be invoked INSIDE the short read session — once the session exits,
    accessing ORM attributes on a detached instance triggers MissingGreenlet
    via SQLAlchemy's lazy-load path. The frozen dataclass extracts every
    field eagerly so post-session code paths never re-touch ``run``.

    Position-correspondence invariant (§3 key invariant 2): the order of
    ``prompts_snapshot`` items mirrors ``run.prompt_results`` exactly, so
    consumers can pair ``prompts_snapshot[i] <-> baseline_scores.per_prompt[i]``.
    """
    prompt_results: list[dict] = list(run.prompt_results or [])
    prompts_snapshot: list[dict] = []
    for r in prompt_results:
        # Each PromptSnapshotItem field is read tolerantly — different
        # generators (topic_probe vs future seed_agent forks) emit slightly
        # different per-prompt shapes. raw_prompt is the only hard requirement.
        prompts_snapshot.append({
            "raw_prompt": r.get("raw_prompt", ""),
            "intent_label": r.get("intent_label"),
            "original_optimization_id": r.get("optimization_id"),
            "category": r.get("category"),
        })

    aggregate: dict = dict(run.aggregate or {})

    # per_prompt may live in run.aggregate['per_prompt'] (topic_probe canonical
    # shape) OR be derivable from run.prompt_results (older shapes / future
    # forks). Prefer aggregate.per_prompt; fall back to deriving from prompt_results.
    raw_per_prompt: list[dict]
    if isinstance(aggregate.get("per_prompt"), list):
        raw_per_prompt = list(aggregate["per_prompt"])
    else:
        raw_per_prompt = [
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

    baseline_scores: dict = {
        "mean_overall": float(aggregate.get("mean_overall") or 0.0),
        "p5_overall": float(aggregate.get("p5_overall") or 0.0),
        "p50_overall": float(aggregate.get("p50_overall") or 0.0),
        "p95_overall": float(aggregate.get("p95_overall") or 0.0),
        "per_prompt": raw_per_prompt,
        "task_type_distribution": dict(aggregate.get("task_type_distribution") or {}),
    }

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
        # pinned by test_create_from_run_detached_orm_safe).
        #
        # Manual __aenter__/__aexit__ rather than ``async with`` so the
        # detached-ORM test's instance-level __aexit__ wrapper fires (Python's
        # ``async with`` resolves dunder methods on the class, bypassing
        # instance-level patches — manual getattr-style invocation honours
        # the test's call-order recorder).
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
    """Adapt a frozen snapshot to the public ValidationSuiteOut shape.

    Coerces ``baseline_scores.per_prompt`` (list[dict]) → list[PerPromptScore]
    so the returned model has the canonical nested-attribute access path the
    test suite pins (``out.baseline_scores.p5_overall``, etc.).
    """
    per_prompt_raw: list[dict[str, Any]] = list(snapshot.baseline_scores.get("per_prompt") or [])
    per_prompt = [PerPromptScore.model_validate(p) for p in per_prompt_raw]
    baseline = BaselineScoresPayload(
        mean_overall=float(snapshot.baseline_scores.get("mean_overall") or 0.0),
        p5_overall=float(snapshot.baseline_scores.get("p5_overall") or 0.0),
        p50_overall=float(snapshot.baseline_scores.get("p50_overall") or 0.0),
        p95_overall=float(snapshot.baseline_scores.get("p95_overall") or 0.0),
        per_prompt=per_prompt,
        task_type_distribution=dict(snapshot.baseline_scores.get("task_type_distribution") or {}),
    )
    snapshot_items = [
        PromptSnapshotItem.model_validate(p) for p in snapshot.prompts_snapshot
    ]
    return ValidationSuiteOut(
        id=snapshot.suite_id,
        source_run_id=snapshot.source_run_id,
        label=snapshot.label,
        tolerance_abs=snapshot.tolerance_abs,
        project_id=snapshot.project_id,
        repo_full_name=snapshot.repo_full_name,
        created_at=snapshot.created_at,
        retired_at=None,
        retired_reason=None,
        prompts_snapshot=snapshot_items,
        baseline_scores=baseline,
    )
