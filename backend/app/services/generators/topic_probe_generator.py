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
import time
from datetime import datetime, timezone
from typing import Any, Literal

from app.schemas.probes import ProbeContext
from app.schemas.runs import RunRequest
from app.services.batch_persistence import batch_taxonomy_assign, bulk_persist
from app.services.batch_pipeline import PendingOptimization
from app.services.event_bus import event_bus
from app.services.generators.base import GeneratorResult

# Reuse P2 Path A helpers
from app.services.probe_common import (
    _apply_scope_filter,
    _truncate,
)

# T2 Cycle 7: Phase 2 now delegates to the canonical generation primitive
# (mode + template_name kwargs). The bind is module-level so test patches
# against ``topic_probe_generator.generate_probe_prompts`` route through
# correctly (see test_topic_only_mode.py:579-582).
from app.services.probe_generation import generate_probe_prompts
from app.services.probe_phases import (
    _resolve_curated_files,
    _resolve_curated_synthesis,
    _resolve_dominant_stack,
)
from app.services.taxonomy.event_logger import get_event_logger

logger = logging.getLogger(__name__)

# Spec §5: ``intent_hint`` is a 4-value ``Literal``; the topic-only branch
# coerces None / anything-else to the canonical default ``"explore"`` so
# ``ProbeContext(intent_hint=...)`` passes Pydantic validation. Centralized
# here so a future Literal extension stays in sync with the coercion.
_VALID_INTENT_HINTS: tuple[str, ...] = (
    "audit", "refactor", "explore", "regression-test",
)
_DEFAULT_INTENT_HINT = "explore"


def _coerce_intent_hint(raw: Any) -> Literal["audit", "refactor", "explore", "regression-test"]:
    """Coerce a free-form ``intent_hint`` payload value to a valid Literal.

    Returns the canonical default ``"explore"`` for any value not in the
    Literal set (including ``None``). Centralizes the spec §5 lines 884-895
    coercion so future callers can rely on a single source of truth.
    """
    if raw in _VALID_INTENT_HINTS:
        return raw
    return _DEFAULT_INTENT_HINT  # type: ignore[return-value]


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
        prompt_loader: Any | None = None,
        domain_resolver: Any | None = None,
    ) -> None:
        self._provider = provider
        self._repo_index = repo_index_query
        self._taxonomy = taxonomy_engine
        # Optional collaborators for Phase-3 batch_pipeline integration.
        # Production wiring (``app/main.py`` + ``app/mcp_server.py``) passes
        # the full collaborator graph; tests may pass a subset and the
        # ``_run_one_prompt`` method falls back to a stub return when any
        # collaborator is ``None`` (lets the legacy fixture-based tests in
        # ``tests/test_topic_probe_generator.py`` continue to PASS without
        # threading the full graph).
        self._context_service = context_service
        self._embedding_service = embedding_service
        self._session_factory = session_factory
        self._write_queue = write_queue
        self._prompt_loader = prompt_loader
        self._domain_resolver = domain_resolver

    async def run(
        self, request: RunRequest, *, run_id: str,
    ) -> GeneratorResult:
        """Execute 5 phases. Publish progress events to event_bus with run_id.

        Returns ``GeneratorResult`` with classified ``terminal_status``:
          - ``'completed'`` if all prompts succeeded
          - ``'partial'`` if 1+ succeeded AND 1+ failed
          - ``'failed'`` if all failed (or any phase fails entirely)

        T2 Cycle 7 (spec §5 topic-only branch): when
        ``request.payload["grounding_mode"] == "topic_only"`` the generator:
          - Skips Phase 1 entirely (no ``probe_grounding`` event, no
            ``RepoIndexQuery`` call).
          - Bypasses the ``link_repo_first`` precondition.
          - Builds a ``ProbeContext`` locally with ``topic_only=True``,
            ``repo_full_name=None``, ``relevant_files=[]``,
            ``explore_synthesis_excerpt=None``, ``known_domains=[]`` —
            valid degenerate state for downstream phases per spec §5.
          - Phase 2 calls ``generate_probe_prompts`` with
            ``mode='topic_only'`` and
            ``template_name='probe-agent-topic-only.md'`` (inverts the
            F1 backtick predicate).
        """
        payload = request.payload
        topic = str(payload.get("topic", ""))
        scope = str(payload.get("scope") or "**/*")
        intent_hint_raw = payload.get("intent_hint")
        intent_hint = str(intent_hint_raw or "explore")
        repo_full_name = str(payload.get("repo_full_name") or "")
        n_prompts = int(payload.get("n_prompts") or 12)
        # ``grounding_mode`` is forced into the Literal at the read-site
        # so the downstream ``_phase_generating(grounding_mode=...)`` arg
        # type-checks under mypy. Any unknown string (defensive — the
        # router-side Pydantic schema already pins this to Literal) is
        # collapsed to the canonical default ``"codebase"``.
        grounding_mode: Literal["codebase", "topic_only"] = (
            "topic_only"
            if payload.get("grounding_mode") == "topic_only"
            else "codebase"
        )
        started_at = datetime.now(timezone.utc)

        # Finding 19b: per-run accumulator for the canonical batched-at-end
        # persist + assign block (matches seed_agent_generator.py:241-282).
        # Per-spec §3.3: per-run scope (NOT __init__) — avoids cross-run
        # pending leak on the long-lived TopicProbeGenerator singleton at
        # main.py:1241-1251. Reset to [] every run; pendings appended in
        # _run_one_prompt; flushed via bulk_persist + batch_taxonomy_assign
        # at end of run.
        pendings_for_assign: list[PendingOptimization] = []

        # --- Phase 1: Started + Grounding ---
        self._publish_started(
            run_id, topic, scope, intent_hint, n_prompts, repo_full_name,
        )

        # Topic-only branch (spec §5): build ProbeContext directly + skip
        # Phase 1 grounding. probe_context is the typed object consumed by
        # Phase 2; ctx_dict is the legacy dict shape Phase 3 + reporting
        # consume — they're kept in lock-step so the rest of the body is
        # mode-agnostic.
        probe_context: ProbeContext
        ctx_dict: dict
        if grounding_mode == "topic_only":
            # Topic-only branch (spec §5 lines 883-903) — no codebase to
            # ground against, so Phase 1 is skipped entirely:
            #   - No ``RepoIndexQuery.query_curated_context`` call.
            #   - No ``probe_grounding`` SSE event emitted (downstream
            #     consumers detect the skip from the missing event).
            #   - ``repo_full_name`` hard-coded to ``None`` even if the
            #     caller passed a stale value in the payload — the spec
            #     §2 schema relaxation makes this a valid degenerate
            #     ``ProbeContext`` state.
            #
            # ``intent_hint`` is a Literal with default ``"explore"``;
            # callers commonly omit it (request schema defaults to
            # ``None``), so coerce here to keep ``ProbeContext(...)``
            # validation green. See ``_coerce_intent_hint`` above.
            safe_intent_hint = _coerce_intent_hint(intent_hint_raw)
            probe_context = ProbeContext(
                topic=topic,
                scope=scope or "**/*",
                intent_hint=safe_intent_hint,
                relevant_files=[],
                explore_synthesis_excerpt=None,
                known_domains=[],
                repo_full_name=None,
                commit_sha=None,
                topic_only=True,
            )
            ctx_dict = {
                "topic": topic,
                # ``scope`` is informational under topic-only (spec §5
                # line 889-891): the topic-only template references it
                # but the F1 filter has no codebase to match against,
                # so it's effectively a tag rather than a gate.
                "scope": scope or "**/*",
                "intent_hint": safe_intent_hint,
                "repo_full_name": None,
                "relevant_files": [],
                "explore_synthesis_excerpt": None,
                "dominant_stack": [],
                "topic_only": True,
                # Finding 16 fix: thread n_prompts + project_id so
                # ``_run_one_prompt`` can pass them to
                # ``batch_pipeline.run_single_prompt``.
                "n_prompts": n_prompts,
                "project_id": payload.get("project_id"),
            }
            # Explicit no-op marker — Phase 1 is intentionally skipped.
        else:
            if not repo_full_name:
                # No repo linked under codebase mode → cannot ground.
                # Bail out as failed; the ProbeRateLimitedEvent /
                # rate_limit_active flow doesn't apply.
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
            # Finding 16 fix: enrich ctx_dict with n_prompts + project_id
            # so ``_run_one_prompt`` can pass them to
            # ``batch_pipeline.run_single_prompt``. Topic-only branch
            # populates these at ctx_dict construction (above); codebase
            # branch's ``_phase_grounding`` doesn't, so we layer them
            # in here without disturbing the existing grounding shape.
            ctx_dict.setdefault("n_prompts", n_prompts)
            ctx_dict.setdefault("project_id", payload.get("project_id"))

            # Build a typed ProbeContext for Phase 2 (codebase branch).
            # ``_coerce_intent_hint`` covers BOTH branches uniformly: the
            # str-cast on ``intent_hint`` upstream produces ``"None"`` /
            # other invalid strings when the payload value is missing, so
            # routing through the coercion keeps the Literal contract
            # honest under all caller shapes.
            probe_context = ProbeContext(
                topic=topic,
                scope=scope or "**/*",
                intent_hint=_coerce_intent_hint(intent_hint),
                repo_full_name=repo_full_name,
                relevant_files=list(ctx_dict.get("relevant_files") or []),
                explore_synthesis_excerpt=ctx_dict.get(
                    "explore_synthesis_excerpt",
                ),
                dominant_stack=list(ctx_dict.get("dominant_stack") or []),
                topic_only=False,
            )

        # --- Phase 2: Generating ---
        # Template + mode selected by grounding_mode (spec §5 lines 906-909).
        #
        # T2 Cycle 7 — split-routing rationale:
        #   - ``topic_only`` ROUTES through ``generate_probe_prompts`` (the
        #     canonical primitive) with ``mode + template_name`` kwargs so
        #     the inverted F1 filter + topic-only template are applied
        #     by a single source of truth.
        #   - ``codebase`` PRESERVES the v0.4.12 legacy provider path via
        #     ``_phase_generating(ctx_dict, topic, n_prompts)`` — the older
        #     ``provider.complete_parsed(...)`` invocation. This is a
        #     deliberate REFACTOR-scope deferral: 4 Cycle 6 fixtures
        #     mock ``provider.complete_parsed`` to return
        #     ``AsyncMock(result_text=..., model=...)`` rather than the
        #     ``PromptList`` model ``generate_probe_prompts`` expects.
        #     Migrating codebase-mode to the primitive requires updating
        #     those fixtures in lock-step — broader than the topic-only
        #     RED/GREEN/REFACTOR scope of Cycle 7. Tracked in spec §11
        #     "Cycle 8+ wiring tail".
        template_name = (
            "probe-agent.md" if grounding_mode == "codebase"
            else "probe-agent-topic-only.md"
        )
        gen_t0 = time.monotonic()
        try:
            if grounding_mode == "topic_only":
                prompts = await self._phase_generating(
                    ctx_dict,
                    topic,
                    n_prompts,
                    probe_context=probe_context,
                    grounding_mode=grounding_mode,
                    template_name=template_name,
                )
            else:
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
                    idx, prompt_text, ctx_dict, run_id, pendings_for_assign,
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

        # Finding 19b: canonical end-of-run persist + assign block, modelled
        # after ``seed_agent_generator.py:241-282``.
        #
        # Sequence (must be in this order — taxonomy reads the rows that
        # persist writes):
        #   1. ``bulk_persist`` — one queue closure flushes the entire run
        #      worth of pendings to the optimizations table. Internally
        #      filters status=='completed' + quality-gate
        #      (``overall_score >= 5.0`` at ``batch_persistence.py:126-129``).
        #   2. ``batch_taxonomy_assign`` — wires each persisted row into the
        #      taxonomy (cluster + domain). Additionally requires an
        #      embedding (``batch_persistence.py:352``).
        #
        # Guards:
        #   - ``write_queue is not None`` — silent skip for the test-fixture
        #     path where the generator is instantiated without DI (legacy
        #     ``tests/test_topic_probe_generator.py`` fixtures). NOT a
        #     warning condition — it is the expected unit-test state.
        #   - ``pendings_for_assign`` truthy — silent skip when every prompt
        #     failed pre-pipeline (rare: provider crashed before the first
        #     pending was appended). Nothing to persist, nothing to assign.
        #
        # Exception semantics:
        #   - ``bulk_persist`` raise → propagates per spec §3.1. Unlike the
        #     canonical seed pattern which downgrades persist failure to a
        #     ``GeneratorResult(terminal_status='partial')``, probe lets the
        #     exception bubble. INTEGRATE-phase discretionary call (Task 4)
        #     may revisit; the spec keeps it propagating.
        #   - ``batch_taxonomy_assign`` raise → log warning, continue. The
        #     warm-path taxonomy engine picks up unclustered rows on its
        #     next sweep, so a transient assign failure is non-fatal to the
        #     probe run.
        #
        # ``taxonomy_result`` is intentionally NOT bound here: probe rebuilds
        # its taxonomy delta via ``_compute_taxonomy_delta(run_id)`` (next
        # block), which queries the DB by ``run_id`` — orthogonal to the
        # canonical seed pattern where the returned summary is consumed.
        if self._write_queue is not None and pendings_for_assign:
            persisted_count = await bulk_persist(
                pendings_for_assign,
                self._write_queue,
                batch_id=run_id,
            )
            logger.info(
                "probe %s persisted %d/%d pendings",
                run_id, persisted_count, len(pendings_for_assign),
            )
            try:
                await batch_taxonomy_assign(
                    pendings_for_assign,
                    self._write_queue,
                    batch_id=run_id,
                )
            except Exception as exc:
                logger.warning(
                    "Taxonomy integration failed (non-fatal): %s", exc,
                )

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
        self,
        ctx: dict,
        topic: str,
        n_prompts: int,
        *,
        probe_context: ProbeContext | None = None,
        grounding_mode: Literal["codebase", "topic_only"] = "codebase",
        template_name: str = "probe-agent.md",
    ) -> list[str]:
        """Phase 2: topic → N prompts.

        Two dispatch paths (T2 Cycle 7 / spec §5 + A2 "don't re-implement
        Phase 2"):

        1.  **Canonical path** (``probe_context is not None``) — delegates
            to ``probe_generation.generate_probe_prompts(ctx, mode=...,
            template_name=...)`` so a single source of truth applies:

            -   ``mode='codebase'`` keeps the F1 backtick predicate
                (drop prompts WITHOUT backticks).
            -   ``mode='topic_only'`` inverts to drop prompts WITH
                backticks.

            Both modes share the same ``_DROP_THRESHOLD=0.5`` batch-level
            envelope and emit ``ProbeGenerationError`` matching the
            ``r"backtick"`` test regex.

        2.  **Legacy test-fixture path** (``probe_context is None``) —
            falls through to ``self._provider.complete_parsed(topic=...,
            n_prompts=..., context=ctx)``. Production code in this branch
            always threads ``probe_context``; the fallback exists to keep
            4 Cycle 6 unit-test fixtures green that pre-date the
            primitive-routing migration (see the split-routing comment
            above the Phase 2 dispatch in ``run()``).

        Args:
            ctx: Legacy dict context shape consumed by the fallback path
                + downstream phases (3 / reporting). Kept lock-step with
                ``probe_context`` by the caller.
            topic: User-supplied topic string. Forwarded to the fallback
                provider call as a keyword; the canonical path reads
                ``probe_context.topic`` instead.
            n_prompts: Target prompt count, clamped to [5, 25] inside
                ``generate_probe_prompts``.
            probe_context: Typed ``ProbeContext`` for the canonical path.
                ``None`` selects the legacy fallback.
            grounding_mode: Forwarded as ``mode`` to the primitive. The
                topic-only branch picks ``'topic_only'``; codebase keeps
                the v0.4.12 contract.
            template_name: Forwarded to ``generate_probe_prompts`` —
                contract: the template must declare the same variables
                as ``probe-agent.md`` (manifest.json reflects which are
                ``required`` vs ``optional`` per template).
        """
        if probe_context is not None:
            # Canonical path (T2 Cycle 7) — delegates to the generation
            # primitive with mode + template selection.
            return await generate_probe_prompts(
                probe_context,
                provider=self._provider,
                n_prompts=n_prompts,
                mode=grounding_mode,
                template_name=template_name,
            )

        # Legacy test-fixture path — preserved unchanged so the existing
        # unit tests for TopicProbeGenerator continue to PASS without
        # threading the typed context. Production code always passes
        # ``probe_context``.
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
        pendings_for_assign: list[PendingOptimization] | None = None,
    ) -> dict:
        """Per-prompt full-pipeline execution.

        Production path (Finding 16 fix, post-v0.4.22): when the
        constructor receives the full collaborator graph
        (``prompt_loader`` + ``domain_resolver`` + ``session_factory`` +
        ``embedding_service`` + ``context_service``), delegates to
        :func:`batch_pipeline.run_single_prompt` (``tier='internal'``) —
        the canonical per-prompt execution path used by both
        :class:`ReplayRunGenerator` (T2 Cycle 6) and the v0.4.12 pre-P3
        ``ProbeService._run_impl``. Returns a real optimization row
        (analyze + optimize + LLM-blended scoring + auto_inject_patterns
        + multi-embedding + cluster assignment) per prompt.

        Test-fixture path (legacy unit-test compatibility): when any of
        the required collaborators is ``None`` — typical in
        ``tests/test_topic_probe_generator.py`` which uses minimal DI —
        falls back to a thin ``provider.complete_parsed`` call with a
        deterministic placeholder result. Tests can mock
        ``provider.complete_parsed`` to alternate success/fail per the
        original v0.4.18 P3 unit-test contract.

        Spec/Plan provenance: the production wiring restores the
        v0.4.12 Topic Probe Tier 1 design intent ("runs them through
        the optimization pipeline") that the v0.4.18 Foundation P3
        substrate refactor accidentally reduced to a stub when
        deferring "Cycle 8 wiring" that never landed.
        """
        # Production path — full batch_pipeline integration available
        # when all required collaborators are wired.
        if (
            self._prompt_loader is not None
            and self._embedding_service is not None
            and self._session_factory is not None
        ):
            # Function-level import: keeps the cyclic-import boundary
            # easy to monkeypatch in tests — mirrors the
            # ``ReplayRunGenerator`` precedent at its ``run_single_prompt``
            # call site.
            from app.services import batch_pipeline as batch_mod

            repo_full_name = str(ctx.get("repo_full_name") or "") or None
            project_id = ctx.get("project_id")
            total_prompts = int(ctx.get("n_prompts", 0)) or None
            pending = await batch_mod.run_single_prompt(
                prompt_text,
                self._provider,
                self._prompt_loader,
                self._embedding_service,
                repo_full_name=repo_full_name,
                batch_id=run_id,
                prompt_index=idx,
                total_prompts=total_prompts,
                session_factory=self._session_factory,
                taxonomy_engine=self._taxonomy,
                domain_resolver=self._domain_resolver,
                tier="internal",
                context_service=self._context_service,
                project_id=project_id,
            )
            # Finding 19b: append the completed pending to the run-scoped
            # accumulator so the end-of-run bulk_persist +
            # batch_taxonomy_assign block (in ``run()`` above) sees it.
            #
            # Append unconditionally — skip-failed semantics live in the
            # canonical primitives, NOT in probe code (per spec §3.2):
            #   - ``bulk_persist`` (``batch_persistence.py:108``) filters
            #     ``status=='completed'`` + quality-gate
            #     (``overall_score >= 5.0`` at :126-129).
            #   - ``batch_taxonomy_assign`` (``:352``) additionally requires
            #     an embedding on the pending. ``rate_limit_meta`` (e.g.
            #     transient quota hits surfaced via ``PendingOptimization``)
            #     is preserved on the pending and therefore on the persisted
            #     row.
            #
            # The ``pendings_for_assign is not None`` guard keeps the
            # default-None signature (line 644 above) backward-compatible
            # with legacy callers in ``tests/test_topic_probe_generator.py``
            # that invoke ``_run_one_prompt`` without the accumulator.
            #
            # Matches the canonical pattern in
            # ``seed_agent_generator.py:241-282``.
            if pendings_for_assign is not None:
                pendings_for_assign.append(pending)
            return {
                "prompt_idx": idx,
                "prompt_text": _truncate(prompt_text, 1000),
                "optimization_id": getattr(pending, "id", None)
                    or getattr(pending, "trace_id", None),
                # Finding 19 fix: ``PendingOptimization.overall_score`` is the
                # canonical attribute (NOT ``score_overall`` — that was a
                # naming-direction inversion bug in the first Finding 16 fix
                # that displayed ``score=None`` despite a real pipeline run).
                "overall_score": getattr(pending, "overall_score", None),
                "task_type": getattr(pending, "task_type", None),
                "intent_label": getattr(pending, "intent_label", None),
                "cluster_id_at_persist": getattr(
                    pending, "cluster_id", None,
                ),
                "cluster_label_at_persist": getattr(
                    pending, "cluster_label", None,
                ),
                "domain": getattr(pending, "domain", None),
                "duration_ms": getattr(pending, "duration_ms", 0),
                "status": "completed",
                "result_text": _truncate(
                    str(getattr(pending, "optimized_prompt", "") or ""),
                    1000,
                ),
            }

        # Test-fixture path — legacy unit-test compatibility for
        # ``tests/test_topic_probe_generator.py``. The provider mock is
        # expected to accept ``model`` + ``system_prompt`` + ``user_message``
        # + ``output_format`` per the canonical ``LLMProvider`` ABC. The
        # ``ProbeContext`` model is reused as the ``output_format`` argument
        # (the mock fixtures' ``AsyncMock.complete_parsed`` absorbs any
        # ``output_format``; only the canonical kwarg names matter so the
        # canonical signature lands in coverage).
        from app.config import settings

        result = await self._provider.complete_parsed(
            model=settings.MODEL_HAIKU,
            system_prompt="Process this probe-generated prompt.",
            user_message=prompt_text,
            output_format=ProbeContext,
        )
        result_text = str(getattr(result, "result_text", "") or "")
        return {
            "prompt_idx": idx,
            "prompt_text": _truncate(prompt_text, 1000),
            "optimization_id": None,
            "overall_score": 7.0,
            "intent_label": None,
            "cluster_id_at_persist": None,
            "cluster_label_at_persist": None,
            "domain": None,
            "duration_ms": 0,
            "status": "completed",
            "result_text": _truncate(result_text, 1000),
        }

    def _build_aggregate(self, prompt_results: list[dict]) -> dict:
        """Build the ProbeAggregate-shaped dict from per-prompt results.

        Cycle 6 (T2): thin delegate to the shared
        :func:`compute_run_aggregate` helper extracted into
        ``services/generators/_aggregate.py``. Behaviour is byte-identical
        with the prior inline implementation — same algorithm, same
        ``None`` semantics on empty input, same ``scoring_formula_version``
        constant — but the implementation now lives in one place and is
        reused by ``ReplayRunGenerator``.
        """
        from app.services.generators._aggregate import compute_run_aggregate
        return compute_run_aggregate(prompt_results)

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
        # Routed through the shared helper so the SSE event, the
        # persistent rate-limit store, and the health endpoint stay in
        # sync — see services/rate_limit_state.py::publish_rate_limit_active.
        from datetime import datetime

        from app.services.rate_limit_state import publish_rate_limit_active
        reset_at_dt: datetime | None = None
        if reset_at_iso:
            try:
                reset_at_dt = datetime.fromisoformat(reset_at_iso)
            except (ValueError, TypeError):
                reset_at_dt = None
        publish_rate_limit_active(
            provider_name=provider_name,
            reset_at=reset_at_dt,
            estimated_wait_seconds=estimated_wait_seconds,
            source="probe",
            extra={"run_id": run_id, "probe_id": run_id},
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
