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
* Concurrent per-prompt fan-out (v0.4.36): ``asyncio.Semaphore`` capped by
  ``PROBE_PROMPT_CONCURRENCY[tier]`` + ``gather(return_exceptions=True)``.
  Preallocated index-addressed results preserve position correspondence;
  per-prompt ``try / except Exception`` preserves sibling isolation; the
  rate-limit projection guard keeps heuristic fallback scores out of the
  regression baseline.
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

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import select

from app import config as _config_mod
from app.models import ValidationSuite
from app.schemas.runs import RunRequest
from app.services.event_bus import event_bus
from app.services.generators._aggregate import compute_run_aggregate
from app.services.generators._constants import (
    DEFAULT_PROMPT_CONCURRENCY,
    PROBE_PROMPT_CONCURRENCY,
)
from app.services.generators.base import GeneratorResult

logger = logging.getLogger(__name__)


class ReplayRunGenerator:
    """Replay a :class:`ValidationSuite` snapshot end-to-end (spec §5).

    Topic Probe Tier 2 (T2) Cycle 6 generator. Conforms to the
    :class:`RunGenerator` Protocol — the orchestrator invokes
    ``run(request, *, run_id)`` and persists the returned
    :class:`GeneratorResult` through the WriteQueue.

    Owns no DB session across LLM work — the suite-snapshot read happens
    in a SHORT session that closes before the per-prompt loop iterates
    (Foundation P4 contract). Each ``batch_pipeline.run_single_prompt``
    call opens its own short sessions via the injected ``session_factory``
    as needed for enrichment lookups. This keeps the audit-hook
    invariant (zero read-engine writes outside ``cold_path_mode`` /
    ``migration_mode``) holding under the spec §10 audit-hook flip.
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
        """Execute one replay run (spec §5).

        Steps:

        1. Read the :class:`ValidationSuite` snapshot in a short session.
           Raise ``ValueError("suite_not_found")`` / ``"suite_retired"``
           before the session closes.
        2. Close the read session. Compare ``suite.repo_full_name`` with
           ``request.payload['repo_full_name']`` — emit a single
           ``probe_warning(code='repo_drift', ...)`` on mismatch.
        3. For each prompt in ``suite.prompts_snapshot`` CONCURRENTLY
           (semaphore-capped per ``PROBE_PROMPT_CONCURRENCY``):
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

        Parameters
        ----------
        request : RunRequest
            Canonical orchestrator request. Required payload keys:

            * ``suite_id`` (str) — :class:`ValidationSuite.id` to replay.

            Optional payload keys:

            * ``repo_full_name`` (str | None) — current repo; drives the
              ``repo_drift`` warning when it disagrees with the saved
              snapshot's ``repo_full_name``.
            * ``project_id`` (str | None) — threaded through to
              ``batch_pipeline.run_single_prompt`` so the resulting
              enrichment + persistence sees the right project scope.
        run_id : str
            Caller-minted run identifier. Used to correlate emitted
            events (``probe_warning``, ``probe_prompt_completed``) and
            the JSONL trace entry back to the orchestrator's
            :class:`RunRow`.

        Returns
        -------
        GeneratorResult
            Frozen result envelope consumed by
            :class:`RunOrchestrator._persist_final`:

            * ``terminal_status`` ∈ ``{'completed', 'partial', 'failed'}``
              — ``completed`` iff every prompt scored; ``failed`` iff
              every prompt raised; otherwise ``partial``.
            * ``prompts_generated`` — equal to ``len(prompts_snapshot)``.
            * ``prompt_results`` — per-prompt dicts in position
              correspondence with the snapshot (spec §3 invariant 2).
            * ``aggregate`` — canonical 8-key block from
              :func:`compute_run_aggregate` augmented with the additive
              ``replay_warnings`` / ``replay_suite_id`` /
              ``replay_n_completed`` / ``replay_n_failed`` keys
              (spec §2 line 70 + §5 lines 813-816).
            * ``taxonomy_delta`` — empty dict (replay produces no new
              taxonomy state per spec §5; the canonical pipeline already
              owns clustering).
            * ``final_report`` — non-None markdown stub referencing
              ``SuiteDetailView`` + ``suite_id=`` so operator log
              readers can discriminate replay terminals from seed_agent
              ones via ``grep``.

        Raises
        ------
        ValueError
            ``"suite_not_found"`` if no row matches ``suite_id``. Router
            ``routers/suites.py`` maps this to a 404.

            ``"suite_retired"`` if ``suite.retired_at`` is set. Retired
            suites are immutable history records, not replayable
            inputs. Router maps this to a 409.

            Both errors are raised inside the read session and surface
            via the orchestrator's top-level dispatch — the
            :class:`RunRow` lands as ``status='failed'`` under
            ``_mark_failed``'s shielded write.
        """
        start_ts = time.monotonic()
        payload = request.payload
        suite_id = str(payload["suite_id"])
        repo_full_name = payload.get("repo_full_name")
        project_id = payload.get("project_id")

        # ---- Step 1+2: short read session — load snapshot + historical_stats,
        # then EXIT.
        #
        # Foundation P4 contract: the session MUST close before the
        # per-prompt loop iterates so detached-ORM lazy-loads can't fire
        # across LLM work. The snapshot is a frozen JSON payload so post-
        # session reads (``prompts_snapshot`` / ``baseline_scores``) hit
        # plain dicts; no ORM lazy attributes get re-touched.
        #
        # ``historical_stats`` is pre-fetched ONCE here and threaded to every
        # ``run_single_prompt`` call below — mirrors the canonical
        # ``batch_orchestrator.run_batch:113-126`` pattern that avoids the
        # N+1 ``get_score_distribution`` query per prompt. Replay's worst-
        # case workload is a 25-prompt suite; without this prefetch the
        # batch issues 25 redundant aggregate queries against the same
        # read engine and serializes them through the read pool.
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
            # Position-correspondence per-prompt baseline lookup — spec §5
            # lines 749-781. ``baseline_scores.per_prompt[i]["overall"]`` is
            # paired with ``prompts_snapshot[i]`` by index; we snapshot the
            # list so post-session code accesses plain dicts.
            baseline_scores_dict: dict[str, Any] = dict(
                suite.baseline_scores or {},
            )
            per_prompt_baselines: list[dict[str, Any]] = list(
                baseline_scores_dict.get("per_prompt") or [],
            )

            # Pre-fetch historical_stats once — mirrors
            # ``batch_orchestrator.run_batch:117-126``. Defensive try/except
            # because the same canonical caller treats this as best-effort
            # (a failure here just falls back to per-prompt fetch downstream;
            # no replay should fail because the score-distribution table
            # couldn't be aggregated).
            historical_stats = None
            try:
                from app.services.optimization_service import OptimizationService
                svc = OptimizationService(read_db)
                historical_stats = await svc.get_score_distribution(
                    exclude_scoring_modes=["heuristic"],
                )
            except Exception as _hs_exc:
                logger.debug(
                    "replay_run %s: historical_stats prefetch failed (%s) — "
                    "per-prompt fetch will fall back",
                    payload.get("suite_id"), _hs_exc,
                )
        finally:
            await session_ctx.__aexit__(None, None, None)

        # ---- Repo drift check (spec §5 lines 696-704) ----
        # Informational only — the warning fires once per drift but the
        # replay continues with the saved snapshot so the operator can
        # see how the same baseline behaves on a different repo (e.g., a
        # fork or rename).
        #
        # ``repo_drift`` is detected only when BOTH sides are present.
        # ``None`` on either side is missing data, not drift — legacy
        # suites created before ``repo_full_name`` was persisted carry
        # ``None``, and operators replaying without a current repo
        # pinned should not see a spurious warning.
        warnings: list[str] = []
        repo_drift = bool(
            suite_repo_full_name
            and repo_full_name
            and suite_repo_full_name != repo_full_name
        )
        if repo_drift:
            warnings.append("repo_drift")
            event_bus.publish("probe_warning", {
                "run_id": run_id,
                "code": "repo_drift",
                "suite_repo": suite_repo_full_name,
                "current_repo": repo_full_name,
            })

        # ---- Step 3: concurrent per-prompt fan-out (v0.4.36, spec §3.3) ----
        #
        # Invariants the executor preserves:
        #
        # 1. Position correspondence: ``prompt_results`` is preallocated and
        #    index-addressed (``prompt_results[idx] = row``) — never appended
        #    in completion order. Slot i pairs with ``prompts_snapshot[i]``
        #    and ``per_prompt_baselines[i]`` by construction.
        # 2. Failure isolation: ``_run_one`` catches ``Exception`` per prompt
        #    (provider 5xx, taxonomy lookups, scoring divergences). Only
        #    ``BaseException`` (cancellation / SystemExit) surfaces through
        #    ``gather(return_exceptions=True)`` — re-raised AFTER all
        #    siblings settle, BEFORE the dense-list assertion, so the
        #    orchestrator's ``shield()``-ed cleanup sees the true cause.
        # 3. Rate-limit containment (spec §3.4): the first pending carrying
        #    ``rate_limit_meta.rate_limited`` trips a shared event — in-flight
        #    prompts bail at the ``batch_pipeline`` phase gates; unstarted
        #    prompts short-circuit pre-call. The projection guard converts
        #    EVERY rate-limited pending into a score-less failed row so
        #    heuristic fallback scores never enter the regression baseline's
        #    ``latest_mean``.
        # 4. ContextVar: tasks are created inside the orchestrator's
        #    ``current_run_id.set(run_id)`` window, so children inherit the
        #    run id (contextvars copy at ``create_task`` time).
        #
        # Function-level import: keeps the cyclic-import boundary easy to
        # monkeypatch in tests — fakes substitute at this exact attribute path.
        from app.services import batch_pipeline as batch_mod

        tier = "internal"
        concurrency = PROBE_PROMPT_CONCURRENCY.get(
            tier, DEFAULT_PROMPT_CONCURRENCY,
        )
        total_prompts = len(prompts_snapshot)
        prompt_results: list[dict[str, Any] | None] = [None] * total_prompts
        semaphore = asyncio.Semaphore(concurrency)
        rate_limit_event = asyncio.Event()
        rate_limited_flag: dict[str, Any] = {"hit": False}
        progress = {"completed": 0}

        def _baseline_for(idx: int) -> float | None:
            # Position-correspondence baseline lookup (spec §3 invariant 2):
            # ``baseline_scores.per_prompt[idx]`` pairs with
            # ``prompts_snapshot[idx]``. Missing entries degrade to ``None``.
            if idx < len(per_prompt_baselines):
                _bo_raw = per_prompt_baselines[idx].get("overall")
                if _bo_raw is not None:
                    try:
                        return float(_bo_raw)
                    except (TypeError, ValueError):
                        return None
            return None

        def _publish_completed(idx: int, result_row: dict[str, Any]) -> None:
            # ``current`` is a monotonic completed-counter (NOT idx+1):
            # incremented synchronously before publish — single event loop,
            # no await in between, hence race-free. The MCP bus→ctx bridge
            # (tools/probe.py) forwards it to ctx.report_progress, which
            # requires monotonicity under out-of-order completion. ``idx``
            # keeps row identity for the SuiteDetailView diff renderer.
            progress["completed"] += 1
            event_bus.publish("probe_prompt_completed", {
                "run_id": run_id,
                "probe_id": run_id,
                "idx": idx,
                "current": progress["completed"],
                "total": total_prompts,
                "optimization_id": "",  # replay does not persist
                "intent_label": result_row.get("intent_label"),
                "overall_score": result_row.get("overall_score"),
                "delta": result_row.get("delta"),
                "status": result_row.get("status"),
            })

        async def _run_one(idx: int, prompt_row: dict[str, Any]) -> None:
            raw_prompt = str(prompt_row.get("raw_prompt", ""))
            baseline_overall = _baseline_for(idx)
            intent_fallback = prompt_row.get("intent_label")
            async with semaphore:
                if rate_limit_event.is_set():
                    # Pre-start short-circuit: a sibling already hit the
                    # provider rate limit — don't burn a guaranteed-429 call.
                    result_row = _rate_limited_row(
                        idx=idx,
                        raw_prompt=raw_prompt,
                        baseline_overall=baseline_overall,
                        intent_label=intent_fallback,
                    )
                else:
                    try:
                        # Fairness yield (one event-loop pass): guarantees
                        # every semaphore-admitted sibling reaches its
                        # provider call before any sibling's result
                        # processing (e.g. the rate-limit trip below) runs.
                        # Without it a synchronously-completing first call
                        # would pre-start-short-circuit siblings the spec
                        # counts as in-flight (spec §3.4 containment model:
                        # in-flight prompts bail at the ``batch_pipeline``
                        # phase gates, not at the executor).
                        await asyncio.sleep(0)
                        pending = await batch_mod.run_single_prompt(
                            raw_prompt,
                            self._provider,
                            self._prompt_loader,
                            self._embedding_service,
                            repo_full_name=repo_full_name,
                            batch_id=run_id,
                            prompt_index=idx,
                            total_prompts=total_prompts,
                            session_factory=self._session_factory,
                            taxonomy_engine=self._taxonomy_engine,
                            domain_resolver=self._domain_resolver,
                            tier=tier,
                            context_service=self._context_service,
                            historical_stats=historical_stats,
                            project_id=project_id,
                            rate_limit_event=rate_limit_event,
                        )
                        meta = getattr(pending, "rate_limit_meta", None) or {}
                        if meta.get("rate_limited"):
                            # Detection per batch_orchestrator.py:212-224 —
                            # run_single_prompt never raises on 429; the flag
                            # rides on the returned pending. First hit trips
                            # the shared event for siblings.
                            if not rate_limited_flag["hit"]:
                                rate_limited_flag["hit"] = True
                                rate_limit_event.set()
                                logger.warning(
                                    "replay_run %s prompt %d rate-limited "
                                    "(provider=%s, reset_at=%s) — "
                                    "short-circuiting unstarted prompts",
                                    run_id, idx, meta.get("provider"),
                                    meta.get("reset_at_iso"),
                                )
                            # Projection guard (spec §3.4): bail-gate rows
                            # carry status='completed' + heuristic
                            # overall_score — poison for the regression
                            # baseline. Project score-less instead.
                            result_row = _rate_limited_row(
                                idx=idx,
                                raw_prompt=raw_prompt,
                                baseline_overall=baseline_overall,
                                intent_label=(
                                    getattr(pending, "intent_label", None)
                                    or intent_fallback
                                ),
                            )
                        else:
                            result_row = _project_pending_to_result(
                                idx=idx,
                                raw_prompt=raw_prompt,
                                pending=pending,
                                baseline_overall=baseline_overall,
                                intent_label_fallback=intent_fallback,
                            )
                    except Exception as exc:  # noqa: BLE001 — sibling isolation
                        # One prompt's failure does NOT abort the rest.
                        # KeyboardInterrupt / SystemExit / CancelledError
                        # derive from BaseException and propagate to the
                        # gather sweep below.
                        logger.warning(
                            "replay_run %s prompt %d raised (%s) — marking "
                            "failed", run_id, idx, exc,
                        )
                        result_row = {
                            "raw_prompt_idx": idx,
                            "raw_prompt": raw_prompt[:1000],
                            "trace_id": None,
                            "overall_score": None,
                            "dimensions": None,
                            "task_type": None,
                            "intent_label": intent_fallback,
                            "divergence_flags": None,
                            "baseline_overall": baseline_overall,
                            "delta": None,
                            "status": "failed",
                            "error": str(exc)[:500],
                        }
                prompt_results[idx] = result_row
                _publish_completed(idx, result_row)

        tasks = [
            asyncio.create_task(_run_one(i, row))
            for i, row in enumerate(prompts_snapshot)
        ]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        # BaseException sweep BEFORE the dense-list assertion — a
        # cancellation must surface as CancelledError, never as a spurious
        # RuntimeError from a half-filled results list.
        for outcome in outcomes:
            if isinstance(outcome, BaseException) and not isinstance(
                outcome, Exception,
            ):
                raise outcome
        if any(r is None for r in prompt_results):
            raise RuntimeError(
                "replay executor slot invariant violated — sparse results "
                f"list for run {run_id}",
            )
        dense_results: list[dict[str, Any]] = [
            r for r in prompt_results if r is not None
        ]

        # ---- Step 4: aggregate ----
        aggregate = compute_run_aggregate(dense_results)
        # Replay-side metadata (additive — not in the canonical 8-key
        # block so seed/topic_probe consumers are unaffected). Useful for
        # the SuiteDetailView baseline diff which is keyed off suite_id.
        #
        # Spec §2 line 70 + §4 line 348 + §5 lines 813-816: the canonical
        # additive keys are ``replay_warnings`` (list[str]), ``replay_suite_id``
        # (str), ``replay_n_completed`` (int), ``replay_n_failed`` (int).
        # ``replay_warnings`` is what the Cycle 12 SuiteDetailView /
        # RegressionBadge polls out of ``RunRow.aggregate`` to surface the
        # ``suite_repo_drift`` informational marker — its shape matters and
        # downstream readers expect a list (so multiple warning codes can
        # accumulate as future warning types land, e.g., ``baseline_stale``).
        completed_count = aggregate.get("completed_count", 0)
        failed_count = aggregate.get("failed_count", 0)
        rate_limited_count = sum(
            1 for r in dense_results if r.get("error") == "rate_limited"
        )
        aggregate.update({
            "replay_warnings": warnings,
            "replay_suite_id": suite_id,
            "replay_n_completed": completed_count,
            "replay_n_failed": failed_count,
            "replay_rate_limited_count": rate_limited_count,
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
            prompt_results=dense_results,
            aggregate=aggregate,
            taxonomy_delta={},
            final_report=final_report,
        )


def _rate_limited_row(
    *,
    idx: int,
    raw_prompt: str,
    baseline_overall: float | None,
    intent_label: str | None,
) -> dict[str, Any]:
    """Score-less rate-limited row (spec §3.4 projection guard).

    Used for BOTH kinds of rate-limited outcome — pre-start short-circuit
    and in-flight bail-gate projection. Replay is a measurement workload:
    a heuristic-scored fallback row would silently poison the baseline
    comparison, so the row carries NO ``overall_score`` and lands as
    ``status='failed'`` (the existing terminal classification then yields
    ``partial``/``failed`` with zero new status logic).
    """
    return {
        "raw_prompt_idx": idx,
        "raw_prompt": raw_prompt[:1000],
        "trace_id": None,
        "overall_score": None,
        "dimensions": None,
        "task_type": None,
        "intent_label": intent_label,
        "divergence_flags": None,
        "baseline_overall": baseline_overall,
        "delta": None,
        "status": "failed",
        "error": "rate_limited",
    }


def _project_pending_to_result(
    *,
    idx: int,
    raw_prompt: str,
    pending: Any,
    baseline_overall: float | None,
    intent_label_fallback: str | None = None,
) -> dict[str, Any]:
    """Project a :class:`PendingOptimization` into the per-prompt dict shape.

    Canonical contract per spec §5 lines 752-781 + test contracts at
    ``tests/test_replay_run_generator.py``:

    * ``raw_prompt_idx`` = loop index (position correspondence).
    * ``trace_id`` = ``pending.trace_id`` (NOT ``pending.id`` — replay
      does not bulk_persist so ``pending.id`` would be an orphan uuid).
    * ``overall_score`` (NOT ``"overall"`` and NOT ``"score"``) is the
      canonical input contract of :func:`compute_run_aggregate`.
    * ``dimensions`` = 5-dimension dict (clarity / specificity / structure /
      faithfulness / conciseness) projected from
      ``pending.score_*`` fields — spec §5 lines 770-776 enumerates each.
      Without this projection the SuiteDetailView per-prompt regression
      table renders five empty cells (A1 violation — silent field drop).
    * ``baseline_overall`` = ``suite.baseline_scores.per_prompt[idx].overall``
      pulled from the frozen snapshot. Position-correspondence per spec §3
      invariant 2 + §5 line 749.
    * ``delta`` = ``overall_score - baseline_overall`` when both are present;
      ``None`` when either side is missing (replay can run against a partial
      legacy baseline). Drives the Cycle 7 regression-alarm WARN/ALERT
      transition and the Cycle 12 RegressionBadge color encoding — the
      sign of delta is the regression-direction signal.
    * Status mirrors ``pending.status`` so the aggregate's
      completed/failed counts honour rate-limit fallback rows.

    Parameters
    ----------
    intent_label_fallback : str | None
        ``prompts_snapshot[idx]["intent_label"]`` from the frozen suite.
        Used when ``pending.intent_label`` is ``None`` so the per-prompt
        row still carries the saved label — replay should never silently
        lose the intent metadata that the suite snapshot pinned at
        save-as-suite time.
    """
    overall_raw = getattr(pending, "overall_score", None)
    overall_score: float | None = None
    if overall_raw is not None:
        try:
            overall_score = float(overall_raw)
        except (TypeError, ValueError):
            overall_score = None

    delta: float | None = None
    if overall_score is not None and baseline_overall is not None:
        delta = overall_score - baseline_overall

    dimensions: dict[str, float | None] = {
        "clarity": getattr(pending, "score_clarity", None),
        "specificity": getattr(pending, "score_specificity", None),
        "structure": getattr(pending, "score_structure", None),
        "faithfulness": getattr(pending, "score_faithfulness", None),
        "conciseness": getattr(pending, "score_conciseness", None),
    }

    return {
        "raw_prompt_idx": idx,
        "raw_prompt": raw_prompt[:1000],
        "trace_id": getattr(pending, "trace_id", None),
        "overall_score": overall_score,
        "dimensions": dimensions,
        "task_type": getattr(pending, "task_type", None),
        "intent_label": (
            getattr(pending, "intent_label", None) or intent_label_fallback
        ),
        "divergence_flags": getattr(pending, "heuristic_flags", None),
        "baseline_overall": baseline_overall,
        "delta": delta,
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
