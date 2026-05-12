"""ReplayRunGenerator — Topic Probe Tier 2, T2 Cycle 6 (v0.4.22).

Re-runs a frozen :class:`ValidationSuite` snapshot through the canonical
``batch_pipeline.run_single_prompt`` per-prompt path and returns a
:class:`GeneratorResult` consumed by :class:`RunOrchestrator`.

Surface contract (spec §5):

* Constructor (keyword-only) mirrors the
  ``batch_pipeline.run_single_prompt`` collaborator union: ``provider``,
  ``prompt_loader``, ``embedding_service``, ``session_factory``,
  ``taxonomy_engine``, ``domain_resolver``, ``context_service``,
  ``write_queue``.
* ``async def run(request, *, run_id) -> GeneratorResult`` — conforms to
  the canonical :class:`RunGenerator` Protocol.
* ``request.payload["suite_id"]`` MUST be read out of the payload.
* Suite snapshot is loaded in a SHORT read session (Foundation P4
  detached-ORM-safe contract); session closes BEFORE per-prompt loop
  iterates.
* Retired suite (``retired_at is not None``) raises
  ``ValueError("suite_retired")`` — the router maps this to a 409.
* Repo drift (saved suite repo ≠ request repo) emits a single
  ``probe_warning`` event with ``code='repo_drift'``. Informational only —
  replay proceeds with the saved snapshot.
* Sequential per-prompt loop wraps every
  ``batch_pipeline.run_single_prompt`` call in ``try / except Exception``
  so one prompt's failure does NOT abort siblings.
* Per-prompt dict carries ``trace_id=pending.trace_id`` (NOT
  ``pending.id`` — replay does NOT bulk_persist so ``pending.id`` would
  be an orphan uuid) and uses ``overall_score`` as the canonical score
  key (matches the input contract of ``compute_run_aggregate``).
* Final report is a non-None markdown stub referencing
  ``SuiteDetailView`` + ``suite_id=`` so operator log readers can
  distinguish replay terminal rows from seed agent rows.
* JSONL trace tagged ``phase="replay_run"`` (spec §9 trace tagging).

Spec: ``docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md``
       §2 components + §5 ReplayRunGenerator body + §9 trace tagging.
Plan: ``docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md`` Cycle 6.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app import config as _config_mod
from app.models import ValidationSuite
from app.schemas.runs import RunRequest
from app.services.event_bus import event_bus
from app.services.generators._aggregate import compute_run_aggregate
from app.services.generators.base import GeneratorResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ReplayRunGenerator:
    """Replay a :class:`ValidationSuite` snapshot end-to-end.

    Conforms to the :class:`RunGenerator` Protocol — the orchestrator
    invokes ``run(request, *, run_id)`` and persists the returned
    :class:`GeneratorResult` through the WriteQueue.

    Owns no DB session across LLM work — the suite-snapshot read happens
    in a SHORT session that closes before the per-prompt loop iterates
    (Foundation P4 contract). Each ``batch_pipeline.run_single_prompt``
    call opens its own short sessions via the injected ``session_factory``
    as needed for enrichment lookups.
    """

    def __init__(
        self,
        *,
        provider: Any,
        prompt_loader: Any,
        embedding_service: Any,
        session_factory: Any,
        taxonomy_engine: Any,
        domain_resolver: Any,
        context_service: Any,
        write_queue: Any,
    ) -> None:
        self._provider = provider
        self._prompt_loader = prompt_loader
        self._embedding_service = embedding_service
        self._session_factory = session_factory
        self._taxonomy_engine = taxonomy_engine
        self._domain_resolver = domain_resolver
        self._context_service = context_service
        self._write_queue = write_queue

    async def run(
        self, request: RunRequest, *, run_id: str,
    ) -> GeneratorResult:
        """Execute one replay run.

        Steps:

        1. Read the :class:`ValidationSuite` snapshot in a short session.
           Raise ``ValueError("suite_not_found")`` / ``"suite_retired"``
           before the session closes.
        2. Close the read session. Compare ``suite.repo_full_name`` with
           ``request.payload['repo_full_name']`` — emit a single
           ``probe_warning(code='repo_drift', ...)`` on mismatch.
        3. For each prompt in ``suite.prompts_snapshot`` SEQUENTIALLY:
           call ``batch_pipeline.run_single_prompt`` under try/except,
           project the :class:`PendingOptimization` into a per-prompt
           dict (``trace_id=pending.trace_id``, ``overall_score``,
           etc.), append to ``prompt_results``. Publish a
           ``probe_prompt_completed`` event per row so the SSE stream
           stays compatible with the topic-probe event grammar.
        4. Compute the aggregate via :func:`compute_run_aggregate` and
           layer the replay-side metadata keys on top.
        5. Emit a single ``replay_run`` JSONL trace entry (spec §9).
        6. Return :class:`GeneratorResult` with classified
           ``terminal_status`` and a non-None stub ``final_report``.
        """
        start_ts = time.monotonic()
        payload = request.payload
        suite_id = str(payload["suite_id"])
        repo_full_name = payload.get("repo_full_name")
        project_id = payload.get("project_id")

        # ---- Step 1+2: short read session — load snapshot, then EXIT ----
        # Foundation P4 contract: the session MUST close before the
        # per-prompt loop iterates so detached-ORM lazy-loads can't fire
        # across LLM work. The snapshot is a frozen JSON payload so post-
        # session reads (``prompts_snapshot`` / ``baseline_scores``) hit
        # plain dicts; no ORM lazy attributes get re-touched.
        session_ctx = self._session_factory()
        read_db = await session_ctx.__aenter__()
        try:
            row_result = await read_db.execute(
                select(ValidationSuite).where(ValidationSuite.id == suite_id),
            )
            suite: ValidationSuite | None = row_result.scalar_one_or_none()
            if suite is None:
                raise ValueError("suite_not_found")
            if suite.retired_at is not None:
                # Retired suites are not replayable. Raise BEFORE the
                # per-prompt loop iterates — RunOrchestrator catches the
                # exception in its top-level dispatch and marks the row
                # failed. Router maps ``suite_retired`` to a 409.
                raise ValueError("suite_retired")
            # Snapshot everything we need into plain Python objects so
            # post-session code paths never re-touch the ORM instance.
            suite_repo_full_name: str | None = suite.repo_full_name
            prompts_snapshot: list[dict[str, Any]] = list(
                suite.prompts_snapshot or [],
            )
        finally:
            await session_ctx.__aexit__(None, None, None)

        # ---- Repo drift check (spec §5 lines 696-704) ----
        # Informational only — the warning fires once per drift but the
        # replay continues with the saved snapshot so the operator can
        # see how the same baseline behaves on a different repo (e.g., a
        # fork or rename).
        if (
            suite_repo_full_name
            and repo_full_name
            and suite_repo_full_name != repo_full_name
        ):
            event_bus.publish("probe_warning", {
                "run_id": run_id,
                "code": "repo_drift",
                "suite_repo": suite_repo_full_name,
                "current_repo": repo_full_name,
            })

        # ---- Step 3: sequential per-prompt loop ----
        # Per spec §5: NOT ``asyncio.TaskGroup`` — a single child's
        # non-rate-limit exception propagates ``BaseExceptionGroup`` which
        # aborts siblings, leaving partial state. ``for idx, item in
        # enumerate(...)`` + per-iteration ``try/except Exception`` keeps
        # one failure isolated to that row's ``status='failed'``.
        #
        # Function-level import keeps the cyclic boundary (the batch
        # pipeline imports a handful of services that themselves import
        # event_bus / config) easy to monkeypatch in tests — the helper
        # ``_patch_run_single_prompt`` in the RED tests substitutes a fake
        # at this exact attribute path.
        from app.services import batch_pipeline as batch_mod

        prompt_results: list[dict[str, Any]] = []
        for idx, prompt_row in enumerate(prompts_snapshot):
            raw_prompt = str(prompt_row.get("raw_prompt", ""))
            try:
                pending = await batch_mod.run_single_prompt(
                    raw_prompt,
                    self._provider,
                    self._prompt_loader,
                    self._embedding_service,
                    repo_full_name=repo_full_name,
                    batch_id=run_id,
                    prompt_index=idx,
                    total_prompts=len(prompts_snapshot),
                    session_factory=self._session_factory,
                    taxonomy_engine=self._taxonomy_engine,
                    domain_resolver=self._domain_resolver,
                    tier="internal",
                    context_service=self._context_service,
                    project_id=project_id,
                )
                result_row = _project_pending_to_result(
                    idx=idx, raw_prompt=raw_prompt, pending=pending,
                )
            except Exception as exc:
                # Sibling-isolation: one prompt's failure does NOT abort
                # the rest of the loop. Status='failed' + error message
                # (truncated to keep the row bounded).
                logger.warning(
                    "replay_run %s prompt %d raised (%s) — marking failed",
                    run_id, idx, exc,
                )
                result_row = {
                    "raw_prompt_idx": idx,
                    "raw_prompt": raw_prompt[:1000],
                    "trace_id": None,
                    "overall_score": None,
                    "task_type": None,
                    "intent_label": None,
                    "divergence_flags": None,
                    "status": "failed",
                    "error": str(exc)[:500],
                }
            prompt_results.append(result_row)

            # Reuse the existing ``probe_prompt_completed`` event so SSE
            # subscribers (HistoryPanel, SuiteDetailView, RunDetailView)
            # don't need a new event type — the run_id correlates the row
            # back to the replay invocation.
            event_bus.publish("probe_prompt_completed", {
                "run_id": run_id,
                "probe_id": run_id,
                "current": idx + 1,
                "total": len(prompts_snapshot),
                "optimization_id": "",  # replay does not persist
                "intent_label": result_row.get("intent_label"),
                "overall_score": result_row.get("overall_score"),
                "status": result_row.get("status"),
            })

        # ---- Step 4: aggregate ----
        aggregate = compute_run_aggregate(prompt_results)
        # Replay-side metadata (additive — not in the canonical 8-key
        # block so seed/topic_probe consumers are unaffected). Useful for
        # the SuiteDetailView baseline diff which is keyed off suite_id.
        completed_count = aggregate.get("completed_count", 0)
        failed_count = aggregate.get("failed_count", 0)
        aggregate.update({
            "replay_suite_id": suite_id,
            "replay_n_completed": completed_count,
            "replay_n_failed": failed_count,
            "replay_repo_drift": (
                bool(suite_repo_full_name)
                and bool(repo_full_name)
                and suite_repo_full_name != repo_full_name
            ),
        })

        # ---- Step 5: JSONL trace (spec §9 trace tagging) ----
        # Emitted BEFORE returning so the orchestrator's terminal persist
        # is independent of the trace. Failure is non-fatal (matches the
        # validation_suite_service.py:_log_validation_suite_phase
        # pattern).
        duration_ms = int((time.monotonic() - start_ts) * 1000)
        _emit_replay_run_trace(
            run_id=run_id,
            suite_id=suite_id,
            duration_ms=duration_ms,
            aggregate=aggregate,
        )

        # ---- Step 6: terminal status classification + return ----
        if completed_count > 0 and failed_count == 0:
            terminal: str = "completed"
        elif completed_count == 0:
            terminal = "failed"
        else:
            terminal = "partial"

        # Stub final_report — non-None per spec §5. SuiteDetailView in the
        # frontend renders the actual baseline-vs-latest diff from the
        # aggregate + the per-prompt rows; a one-line marker here keeps
        # operator log readers (grep on ``suite_id=``) able to discriminate
        # replay terminal rows from seed_agent rows.
        final_report = (
            f"# Replay — suite_id={suite_id}\n"
            f"See SuiteDetailView for the baseline-vs-latest diff."
        )

        return GeneratorResult(
            terminal_status=terminal,  # type: ignore[arg-type]
            prompts_generated=len(prompts_snapshot),
            prompt_results=prompt_results,
            aggregate=aggregate,
            taxonomy_delta={},
            final_report=final_report,
        )


def _project_pending_to_result(
    *, idx: int, raw_prompt: str, pending: Any,
) -> dict[str, Any]:
    """Project a :class:`PendingOptimization` into the per-prompt dict shape.

    Canonical contract per spec §5 + test contracts at
    ``tests/test_replay_run_generator.py``:

    * ``raw_prompt_idx`` = loop index (position correspondence).
    * ``trace_id`` = ``pending.trace_id`` (NOT ``pending.id`` — replay
      does not bulk_persist so ``pending.id`` would be an orphan uuid).
    * ``overall_score`` (NOT ``"overall"`` and NOT ``"score"``) is the
      canonical input contract of :func:`compute_run_aggregate`.
    * Status mirrors ``pending.status`` so the aggregate's
      completed/failed counts honour rate-limit fallback rows.
    """
    return {
        "raw_prompt_idx": idx,
        "raw_prompt": raw_prompt[:1000],
        "trace_id": getattr(pending, "trace_id", None),
        "overall_score": getattr(pending, "overall_score", None),
        "task_type": getattr(pending, "task_type", None),
        "intent_label": getattr(pending, "intent_label", None),
        "divergence_flags": getattr(pending, "heuristic_flags", None),
        "status": getattr(pending, "status", "completed"),
        "error": getattr(pending, "error", None),
    }


def _emit_replay_run_trace(
    *,
    run_id: str,
    suite_id: str,
    duration_ms: int,
    aggregate: dict[str, Any],
) -> None:
    """Append a ``phase='replay_run'`` JSONL entry to today's traces file.

    Spec §9 trace tagging — replay runs MUST emit a JSONL trace with
    ``phase='replay_run'``. Lazy-imports :class:`TraceLogger` reading
    ``app.config.DATA_DIR`` at call time so test-side
    ``monkeypatch.setattr(cfg_mod, "DATA_DIR", tmp_path)`` works.

    Trace failure must never break a successful replay — ``OSError`` /
    ``RuntimeError`` / ``ValueError`` are logged at DEBUG and swallowed.
    Mirrors the pattern in
    ``validation_suite_service._log_validation_suite_phase``.
    """
    try:
        from app.services.trace_logger import TraceLogger

        traces_dir = _config_mod.DATA_DIR / "traces"
        logger_ = TraceLogger(traces_dir)
        logger_.log_phase(
            trace_id=run_id,
            phase="replay_run",
            duration_ms=duration_ms,
            tokens_in=0,
            tokens_out=0,
            model="",
            provider="",
            result={
                "action": "replay",
                "run_id": run_id,
                "suite_id": suite_id,
                "completed_count": aggregate.get("completed_count", 0),
                "failed_count": aggregate.get("failed_count", 0),
                "mean_overall": aggregate.get("mean_overall"),
            },
        )
    except (OSError, RuntimeError, ValueError) as exc:
        # Trace failure must never break a successful replay write.
        logger.debug("replay_run trace emission failed: %s", exc)


__all__ = ["ReplayRunGenerator"]
