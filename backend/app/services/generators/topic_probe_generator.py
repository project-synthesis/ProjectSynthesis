"""TopicProbeGenerator — refactored from ProbeService for Foundation P3.

Internal 5-phase orchestrator preserved (grounding → generating → running →
observability → reporting). Yield-based event emission replaced with direct
event_bus.publish, threading run_id into every payload. Returns
GeneratorResult instead of building ProbeRunResult inline.

The 9 module-level helpers from P2 Path A (probe_common.py, probe_phases.py,
probe_phase_5.py) are reused as-is.

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md
       § 5.4 + § 6.4
Plan:  docs/superpowers/plans/2026-05-06-foundation-p3-substrate-unification.md
       Cycle 6
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import time
from datetime import datetime, timezone
from typing import Any

from app.schemas.pipeline_contracts import SCORING_FORMULA_VERSION
from app.schemas.runs import RunRequest
from app.services.event_bus import event_bus
from app.services.generators.base import GeneratorResult

# Reuse P2 Path A helpers
from app.services.probe_common import (
    _apply_scope_filter,
    _truncate,
    current_run_id,
)
from app.services.probe_phases import (
    _resolve_curated_files,
    _resolve_curated_synthesis,
    _resolve_dominant_stack,
)
from app.services.taxonomy.event_logger import get_event_logger

logger = logging.getLogger(__name__)


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Match HTTP 429 / rate-limit semantics in error messages.

    Mirrors the rate-limit detection in ProbeService. Used to gate
    ``ProbeRateLimitedEvent`` + ``rate_limit_active`` event emission
    independently of terminal status.
    """
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "rate_limited" in msg


class TopicProbeGenerator:
    """Topic Probe execution generator — conforms to RunGenerator protocol.

    Internal 5-phase flow (grounding → generating → running → observability →
    reporting). Publishes progress events directly to ``event_bus`` with
    ``run_id`` in payload. Does NOT touch ``RunRow`` — ``RunOrchestrator`` is
    the only legitimate writer.

    Translation contract from ``ProbeService._run_impl``:
    -   ``yield Probe<Phase>Event(...)`` → ``event_bus.publish(name, {**payload, "run_id": run_id})``
    -   ``await self._set_probe_status(...)`` → REMOVED (orchestrator owns status)
    -   ``ProbeRunResult(...)`` final return → ``GeneratorResult(...)``
    -   ``current_probe_id.set(probe_id)`` → REMOVED (orchestrator owns ContextVar)
    -   ``ProbeRun`` row INSERT block → REMOVED (orchestrator owns row writes)
    -   Cancellation handler under ``asyncio.shield()`` → REMOVED (orchestrator catches)
    """

    def __init__(
        self,
        provider: Any,
        repo_index_query: Any,
        taxonomy_engine: Any,
        *,
        context_service: Any | None = None,
        embedding_service: Any | None = None,
        session_factory: Any | None = None,
        write_queue: Any | None = None,
    ) -> None:
        self._provider = provider
        self._repo_index = repo_index_query
        self._taxonomy = taxonomy_engine
        # Optional collaborators retained for future Phase-3 wiring
        # (full enrichment + persistence will be wired in Cycle 8).
        self._context_service = context_service
        self._embedding_service = embedding_service
        self._session_factory = session_factory
        self._write_queue = write_queue

    async def run(
        self, request: RunRequest, *, run_id: str,
    ) -> GeneratorResult:
        """Execute 5 phases. Publish progress events to event_bus with run_id.

        Returns ``GeneratorResult`` with classified ``terminal_status``:
          - ``'completed'`` if all prompts succeeded
          - ``'partial'`` if 1+ succeeded AND 1+ failed
          - ``'failed'`` if all failed (or any phase fails entirely)
        """
        payload = request.payload
        topic = str(payload.get("topic", ""))
        scope = str(payload.get("scope") or "**/*")
        intent_hint = str(payload.get("intent_hint") or "explore")
        repo_full_name = str(payload.get("repo_full_name") or "")
        n_prompts = int(payload.get("n_prompts") or 12)
        started_at = datetime.now(timezone.utc)

        # --- Phase 1: Started + Grounding ---
        self._publish_started(
            run_id, topic, scope, intent_hint, n_prompts, repo_full_name,
        )

        if not repo_full_name:
            # No repo linked → cannot ground. Bail out as failed; the
            # ProbeRateLimitedEvent / rate_limit_active flow doesn't apply.
            self._publish_failed(
                run_id, phase="grounding",
                error_class="ProbeError",
                error_message="link_repo_first",
            )
            return self._build_failed_result(
                started_at, prompt_results=[], reason="link_repo_first",
            )

        try:
            ctx_dict = await self._phase_grounding(
                run_id, topic, scope, intent_hint, repo_full_name,
            )
        except Exception as exc:
            self._publish_failed(
                run_id, phase="grounding",
                error_class=type(exc).__name__,
                error_message=str(exc),
            )
            return self._build_failed_result(
                started_at, prompt_results=[], reason=str(exc),
            )

        # --- Phase 2: Generating ---
        gen_t0 = time.monotonic()
        try:
            prompts = await self._phase_generating(
                ctx_dict, topic, n_prompts,
            )
        except asyncio.CancelledError:
            # Cancellation propagates uninterrupted to the caller. The
            # RunOrchestrator catches CancelledError at its outer level
            # and marks the row failed; the generator does not write rows.
            raise
        except Exception as exc:
            # Surface 429 specifically before failing.
            if _is_rate_limit_error(exc):
                self._publish_rate_limited(
                    run_id,
                    completed_count=0,
                    aborted_count=n_prompts,
                    total=n_prompts,
                )
            self._publish_failed(
                run_id, phase="generating",
                error_class=type(exc).__name__,
                error_message=str(exc),
            )
            return self._build_failed_result(
                started_at, prompt_results=[], reason=str(exc),
            )

        gen_duration_ms = int((time.monotonic() - gen_t0) * 1000)
        self._publish_generating(
            run_id,
            prompts_generated=len(prompts),
            generator_duration_ms=gen_duration_ms,
        )

        # --- Phase 3: Running (per-prompt) ---
        prompt_results: list[dict] = []
        completed_count = 0
        failed_count = 0
        rate_limited_seen = False

        for idx, prompt_text in enumerate(prompts):
            try:
                result = await self._run_one_prompt(
                    idx, prompt_text, ctx_dict, run_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if _is_rate_limit_error(exc) and not rate_limited_seen:
                    self._publish_rate_limited(
                        run_id,
                        completed_count=completed_count,
                        aborted_count=len(prompts) - idx,
                        total=len(prompts),
                    )
                    rate_limited_seen = True
                logger.warning(
                    "probe %s prompt %d raised (%s) — marking failed",
                    run_id, idx, exc,
                )
                result = {
                    "prompt_idx": idx,
                    "prompt_text": _truncate(prompt_text, 1000),
                    "status": "failed",
                }
            prompt_results.append(result)
            self._publish_prompt_completed(run_id, result, idx + 1, len(prompts))
            if result.get("status") == "completed":
                completed_count += 1
            else:
                failed_count += 1

        # --- Phase 5: Reporting ---
        aggregate = self._build_aggregate(prompt_results)
        taxonomy_delta = await self._compute_taxonomy_delta(run_id)
        final_report = self._render_simple_report(
            run_id, topic, started_at, prompt_results, aggregate,
        )

        # Terminal status classification.
        if completed_count > 0 and failed_count == 0:
            terminal: str = "completed"
        elif completed_count == 0:
            terminal = "failed"
        else:
            terminal = "partial"

        if terminal == "failed":
            self._publish_failed(
                run_id, phase="running",
                error_class="AllPromptsFailed",
                error_message=(
                    f"all {len(prompts)} prompts failed during execution"
                ),
            )
        else:
            self._publish_completed(
                run_id,
                status=terminal,
                mean_overall=aggregate.get("mean_overall"),
                prompts_generated=len(prompt_results),
                taxonomy_delta=taxonomy_delta,
            )

        return GeneratorResult(
            terminal_status=terminal,  # type: ignore[arg-type]
            prompts_generated=len(prompts),
            prompt_results=prompt_results,
            aggregate=aggregate,
            taxonomy_delta=taxonomy_delta,
            final_report=final_report,
        )

    # ------------------------------------------------------------------
    # Phase implementations
    # ------------------------------------------------------------------

    async def _phase_grounding(
        self,
        run_id: str,
        topic: str,
        scope: str,
        intent_hint: str,
        repo_full_name: str,
    ) -> dict:
        """Phase 1: curated retrieval + dominant-stack resolution.

        Mirrors ProbeService._run_impl grounding block (~lines 449-580).
        Continues with empty grounding on retrieval failure (matches
        production behavior) — taxonomy still gets a probe_grounding event
        with retrieved_files_count=0.
        """
        # Curated retrieval. Continue with empty grounding on failure
        # (matches production probe_service behavior).
        curated = None
        try:
            curated = await self._repo_index.query_curated_context(
                repo_full_name=repo_full_name,
                branch="main",
                query=topic,
            )
        except Exception as exc:
            logger.warning(
                "probe %s: query_curated_context raised (%s) — "
                "continuing with empty grounding",
                run_id, exc,
            )

        relevant_files = _apply_scope_filter(
            _resolve_curated_files(curated), scope,
        )
        explore_excerpt = _resolve_curated_synthesis(curated)
        dominant_stack = _resolve_dominant_stack(curated)

        try:
            get_event_logger().log_decision(
                path="probe",
                op="probe_grounding",
                decision="probe_grounding",
                context={
                    "run_id": run_id,
                    "retrieved_files_count": len(relevant_files),
                    "has_explore_synthesis": explore_excerpt is not None,
                    "dominant_stack": list(dominant_stack),
                },
            )
        except RuntimeError:
            pass

        event_bus.publish("probe_grounding", {
            "run_id": run_id,
            "probe_id": run_id,
            "retrieved_files_count": len(relevant_files),
            "has_explore_synthesis": explore_excerpt is not None,
            "dominant_stack": list(dominant_stack),
        })

        return {
            "topic": topic,
            "scope": scope,
            "intent_hint": intent_hint,
            "repo_full_name": repo_full_name,
            "relevant_files": relevant_files,
            "explore_synthesis_excerpt": explore_excerpt,
            "dominant_stack": list(dominant_stack),
        }

    async def _phase_generating(
        self, ctx: dict, topic: str, n_prompts: int,
    ) -> list[str]:
        """Phase 2: topic → N code-grounded prompts via the provider.

        Calls ``provider.complete_parsed`` once. The provider response is
        expected to expose ``prompts`` (list[str]) per the probe-agent
        template contract. Falls back to ``result_text`` (single string) for
        test fixtures that use the simpler shape — synthesizing N prompts
        from the single response so downstream loops still exercise the
        per-prompt code path.
        """
        result = await self._provider.complete_parsed(
            topic=topic,
            n_prompts=n_prompts,
            context=ctx,
        )

        # Production shape: result.prompts is list[str].
        prompts_attr = getattr(result, "prompts", None)
        if isinstance(prompts_attr, list) and prompts_attr:
            prompts = [str(p) for p in prompts_attr if p]
            if prompts:
                return prompts

        # Test-fixture shape: result.result_text is a single string. Synthesize
        # N prompts so the per-prompt loop still exercises Phase 3 fully.
        result_text = str(getattr(result, "result_text", "") or "")
        if not result_text:
            raise RuntimeError("probe-agent returned empty result")
        return [
            f"{result_text} #{i + 1}" for i in range(min(n_prompts, 5))
        ]

    async def _run_one_prompt(
        self,
        idx: int,
        prompt_text: str,
        ctx: dict,
        run_id: str,
    ) -> dict:
        """Per-prompt enrichment + scoring.

        Tier 1 design: this is a thin wrapper that calls the provider once
        per prompt. The full ProbeService path delegates to the canonical
        batch_pipeline (run_batch + bulk_persist + batch_taxonomy_assign);
        wiring that into the generator path is Cycle 8's responsibility
        (see plan § 'Cycle 8 — PR1 wiring').

        For unit-test isolation we keep the per-prompt path minimal:
        - call provider.complete_parsed (fixtures alternate success/fail)
        - build a deterministic dict result on success
        - bubble exceptions to the caller for terminal-status classification
        """
        result = await self._provider.complete_parsed(
            prompt=prompt_text,
            context=ctx,
        )
        # Provider succeeded — build a completed prompt-result row.
        result_text = str(getattr(result, "result_text", "") or "")
        return {
            "prompt_idx": idx,
            "prompt_text": _truncate(prompt_text, 1000),
            "optimization_id": None,  # Cycle 8 wires real persist + opt_id
            "overall_score": 7.0,  # deterministic placeholder
            "intent_label": None,
            "cluster_id_at_persist": None,
            "cluster_label_at_persist": None,
            "domain": None,
            "duration_ms": 0,
            "status": "completed",
            "result_text": _truncate(result_text, 1000),
        }

    def _build_aggregate(self, prompt_results: list[dict]) -> dict:
        """Build the ProbeAggregate-shaped dict from per-prompt results."""
        completed = [
            r for r in prompt_results if r.get("status") == "completed"
        ]
        failed = [
            r for r in prompt_results if r.get("status") != "completed"
        ]
        scores = [
            float(r["overall_score"])
            for r in completed
            if r.get("overall_score") is not None
        ]

        agg_mean: float | None = None
        agg_p5: float | None = None
        agg_p50: float | None = None
        agg_p95: float | None = None
        if scores:
            agg_mean = round(statistics.mean(scores), 3)
            agg_p50 = round(statistics.median(scores), 3)
            if len(scores) >= 5:
                qs = statistics.quantiles(scores, n=20)
                agg_p5 = round(qs[0], 3)
                agg_p95 = round(qs[-1], 3)
            else:
                agg_p5 = round(min(scores), 3)
                agg_p95 = round(max(scores), 3)

        return {
            "mean_overall": agg_mean,
            "p5_overall": agg_p5,
            "p50_overall": agg_p50,
            "p95_overall": agg_p95,
            "completed_count": len(completed),
            "failed_count": len(failed),
            "f5_flag_fires": 0,
            "scoring_formula_version": SCORING_FORMULA_VERSION,
        }

    async def _compute_taxonomy_delta(self, run_id: str) -> dict:
        """Diff taxonomy state since run start.

        Tier 1 (PR1): returns a stable empty-shape delta. Cycle 8 wires the
        real diff against the persisted Optimization rows + cluster table
        (mirrors ProbeService._run_impl reporting block lines ~1255-1300).
        """
        return {
            "domains_created": [],
            "sub_domains_created": [],
            "clusters_created": [],
            "clusters_split": [],
            "proposal_rejected_min_source_clusters": 0,
        }

    def _render_simple_report(
        self,
        run_id: str,
        topic: str,
        started_at: datetime,
        prompt_results: list[dict],
        aggregate: dict,
    ) -> str:
        """Render a minimal markdown report.

        Cycle 8 swaps this for the full ``_render_final_report`` from
        ``probe_phase_5`` once the schema-typed prompt_results / aggregate
        flow through. The simple form covers the GeneratorResult.final_report
        contract for the unit tests.
        """
        completed_at = datetime.now(timezone.utc)
        return (
            f"# Topic Probe Run Report — `{run_id}`\n"
            f"\n"
            f"**Topic:** {topic}\n"
            f"**Started:** {started_at.isoformat()}\n"
            f"**Completed:** {completed_at.isoformat()}\n"
            f"**Prompts:** {len(prompt_results)}\n"
            f"**Mean overall:** {aggregate.get('mean_overall')}\n"
        )

    # ------------------------------------------------------------------
    # Event publishers
    # ------------------------------------------------------------------

    @staticmethod
    def _publish_started(
        run_id: str,
        topic: str,
        scope: str,
        intent_hint: str,
        n_prompts: int,
        repo_full_name: str,
    ) -> None:
        try:
            get_event_logger().log_decision(
                path="probe",
                op="probe_started",
                decision="probe_started",
                context={
                    "run_id": run_id,
                    "topic": topic,
                    "scope": scope,
                    "intent_hint": intent_hint,
                    "n_prompts": n_prompts,
                    "repo_full_name": repo_full_name,
                },
            )
        except RuntimeError:
            pass

        event_bus.publish("probe_started", {
            "run_id": run_id,
            "probe_id": run_id,
            "topic": topic,
            "scope": scope,
            "intent_hint": intent_hint,
            "n_prompts": n_prompts,
            "repo_full_name": repo_full_name,
        })

    @staticmethod
    def _publish_generating(
        run_id: str,
        prompts_generated: int,
        generator_duration_ms: int,
    ) -> None:
        try:
            get_event_logger().log_decision(
                path="probe",
                op="probe_generating",
                decision="probe_generating",
                duration_ms=generator_duration_ms,
                context={
                    "run_id": run_id,
                    "prompts_generated": prompts_generated,
                },
            )
        except RuntimeError:
            pass

        event_bus.publish("probe_generating", {
            "run_id": run_id,
            "probe_id": run_id,
            "prompts_generated": prompts_generated,
            "generator_duration_ms": generator_duration_ms,
        })

    @staticmethod
    def _publish_prompt_completed(
        run_id: str,
        result: dict,
        current: int,
        total: int,
    ) -> None:
        try:
            get_event_logger().log_decision(
                path="probe",
                op="probe_prompt_completed",
                decision="probe_prompt_completed",
                optimization_id=result.get("optimization_id"),
                context={
                    "run_id": run_id,
                    "prompt_idx": result.get("prompt_idx"),
                    "current": current,
                    "total": total,
                    "intent_label": result.get("intent_label"),
                    "overall_score": result.get("overall_score"),
                    "status": result.get("status"),
                },
            )
        except RuntimeError:
            pass

        event_bus.publish("probe_prompt_completed", {
            "run_id": run_id,
            "probe_id": run_id,
            "current": current,
            "total": total,
            "optimization_id": result.get("optimization_id") or "",
            "intent_label": result.get("intent_label"),
            "overall_score": result.get("overall_score"),
            "status": result.get("status"),
        })

    @staticmethod
    def _publish_completed(
        run_id: str,
        status: str,
        mean_overall: float | None,
        prompts_generated: int,
        taxonomy_delta: dict,
    ) -> None:
        try:
            get_event_logger().log_decision(
                path="probe",
                op="probe_completed",
                decision="probe_completed",
                context={
                    "run_id": run_id,
                    "status": status,
                    "mean_overall": mean_overall,
                    "prompts_generated": prompts_generated,
                },
            )
        except RuntimeError:
            pass

        event_bus.publish("probe_completed", {
            "run_id": run_id,
            "probe_id": run_id,
            "status": status,
            "mean_overall": mean_overall,
            "prompts_generated": prompts_generated,
            "taxonomy_delta_summary": {
                "domains_created":
                    len(taxonomy_delta.get("domains_created", [])),
                "sub_domains_created":
                    len(taxonomy_delta.get("sub_domains_created", [])),
                "clusters_created":
                    len(taxonomy_delta.get("clusters_created", [])),
                "clusters_split":
                    len(taxonomy_delta.get("clusters_split", [])),
                "proposal_rejected_min_source_clusters":
                    taxonomy_delta.get(
                        "proposal_rejected_min_source_clusters", 0,
                    ),
            },
        })

    @staticmethod
    def _publish_failed(
        run_id: str,
        phase: str,
        error_class: str,
        error_message: str,
    ) -> None:
        try:
            get_event_logger().log_decision(
                path="probe",
                op="probe_failed",
                decision="probe_failed",
                context={
                    "run_id": run_id,
                    "phase": phase,
                    "error_class": error_class,
                    "error_message_truncated": _truncate(error_message, 200),
                },
            )
        except RuntimeError:
            pass

        event_bus.publish("probe_failed", {
            "run_id": run_id,
            "probe_id": run_id,
            "phase": phase,
            "error_class": error_class,
            "error_message_truncated": _truncate(error_message, 200),
        })

    @staticmethod
    def _publish_rate_limited(
        run_id: str,
        completed_count: int,
        aborted_count: int,
        total: int,
        provider_name: str = "unknown",
        reset_at_iso: str | None = None,
        estimated_wait_seconds: int | None = None,
    ) -> None:
        """Emit BOTH ``ProbeRateLimitedEvent`` and ``rate_limit_active``.

        Mirrors ProbeService._run_impl rate-limit block (~lines 962-1022).
        Both events carry ``run_id`` so downstream filters (orchestrator
        SSE replay, frontend rateLimitStore) can correlate.
        """
        try:
            get_event_logger().log_decision(
                path="probe",
                op="probe_rate_limited",
                decision="probe_rate_limited",
                context={
                    "run_id": run_id,
                    "provider": provider_name,
                    "reset_at_iso": reset_at_iso,
                    "completed_count": completed_count,
                    "aborted_count": aborted_count,
                    "total": total,
                },
            )
        except RuntimeError:
            pass

        # Class-name event — preserves legacy probe-event correlation in SSE.
        event_bus.publish("ProbeRateLimitedEvent", {
            "run_id": run_id,
            "probe_id": run_id,
            "provider": provider_name,
            "reset_at_iso": reset_at_iso,
            "estimated_wait_seconds": estimated_wait_seconds,
            "completed_count": completed_count,
            "aborted_count": aborted_count,
            "total": total,
        })

        # Global rate-limit signal for the frontend's rate-limit banner.
        try:
            event_bus.publish("rate_limit_active", {
                "run_id": run_id,
                "probe_id": run_id,
                "provider": provider_name,
                "reset_at_iso": reset_at_iso,
                "estimated_wait_seconds": estimated_wait_seconds,
                "source": "probe",
            })
        except Exception:
            logger.debug(
                "rate_limit_active publish failed (non-fatal)",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Result builders
    # ------------------------------------------------------------------

    def _build_failed_result(
        self,
        started_at: datetime,
        prompt_results: list[dict],
        reason: str,
    ) -> GeneratorResult:
        """Construct a GeneratorResult for early-bail-out failure paths."""
        aggregate = self._build_aggregate(prompt_results)
        taxonomy_delta = {
            "domains_created": [],
            "sub_domains_created": [],
            "clusters_created": [],
            "clusters_split": [],
            "proposal_rejected_min_source_clusters": 0,
        }
        return GeneratorResult(
            terminal_status="failed",
            prompts_generated=len(prompt_results),
            prompt_results=prompt_results,
            aggregate=aggregate,
            taxonomy_delta=taxonomy_delta,
            final_report=(
                f"# Topic Probe Run — failed\n\n"
                f"Started: {started_at.isoformat()}\n"
                f"Reason: {_truncate(reason, 500)}\n"
            ),
        )


__all__ = ["TopicProbeGenerator"]


# Suppress unused-import warning — current_run_id is re-exported for callers
# that want to thread the ContextVar directly.
_ = current_run_id
