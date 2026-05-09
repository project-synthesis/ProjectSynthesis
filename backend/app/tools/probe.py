"""synthesis_probe MCP tool — Foundation P3 cycle 13 dispatch shim.

Refactored under Foundation P3 (v0.4.18) to dispatch through
``RunOrchestrator.run('topic_probe', ...)`` instead of the legacy
``ProbeService.run()`` async-generator path. The response shape
(``ProbeRunResult``) is preserved byte-for-byte: the ``probe_id`` /
``id`` field name is unchanged, but the value is now ``RunRow.id``
(spec § 6.1 + § 7.1 backward-compat).

Pre-stream gates preserved:
  - Missing ``repo_full_name`` → ``ProbeError('link_repo_first')``
  - Pydantic ``ProbeRunRequest`` validation runs at the boundary

Construction unification (post-C6 REFACTOR retained): the validated
``ProbeRunRequest`` lives in this shim long enough to surface the
canonical reason codes; once the ``link_repo_first`` gate has fired,
the request payload is forwarded into the orchestrator as a plain dict.

The ``_orchestrator`` parameter is for test injection only (``_`` prefix
flags it as private); production callers leave it ``None`` and the
MCP-runtime singleton resolves via ``_shared.get_run_orchestrator``.

Cycle 14 follow-up (v0.4.18-p3-PR2): the AC-C6-3 per-prompt progress
contract retired in Cycle 13 is restored via a **bus → ctx bridge**.
The shim mints ``run_id`` itself, registers an
``event_bus.subscribe_for_run(run_id)`` subscription BEFORE dispatching
the orchestrator (Cycle 11 race-free pattern), and runs a background
task that forwards every ``probe_prompt_completed`` event into
``ctx.report_progress(progress, total, message)``. Bridge guarantees:

  - Race-free: subscription registered before dispatch
  - Fail-soft: ``ctx is None`` / missing ``report_progress`` /
    in-flight exceptions silently skipped
  - No leaks: bridge task cancelled + subscription ``aclose``\\d in
    ``finally``

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Literal

from app.schemas.probes import (
    ProbeAggregate,
    ProbeError,
    ProbePromptResult,
    ProbeRunRequest,
    ProbeRunResult,
    ProbeTaxonomyDelta,
)
from app.schemas.runs import RunRequest
from app.services.event_bus import event_bus

logger = logging.getLogger(__name__)


def _resolve_orchestrator() -> Any:
    """Return the MCP-process RunOrchestrator singleton.

    Raises ``ProbeError('run_orchestrator_unavailable')`` if the lifespan
    failed to register one. Mirrors the canonical reason-code surface so
    the FastMCP runtime maps it to a remediation message.
    """
    from app.tools._shared import get_run_orchestrator

    try:
        return get_run_orchestrator()
    except ValueError as exc:
        raise ProbeError(
            "run_orchestrator_unavailable",
            message=(
                "RunOrchestrator not initialized; the MCP server lifespan "
                "did not register one (likely WriteQueue init failure)."
            ),
        ) from exc


def _row_to_probe_run_result(row: Any) -> ProbeRunResult:
    """Hydrate a ``RunRow`` (mode='topic_probe') into a ``ProbeRunResult``.

    Mirrors ``routers/probes._serialize_full`` — same logic, same
    ``topic_probe_meta`` extraction, identical None-guards on the JSON
    columns. Kept inline here rather than imported from the router to
    avoid the cross-layer ``tools → routers`` import.
    """
    prompt_results = [
        ProbePromptResult(**r) for r in (row.prompt_results or [])
    ]
    agg_dict = row.aggregate or {
        "scoring_formula_version": 4,  # SCORING_FORMULA_VERSION default
    }
    agg = ProbeAggregate(**agg_dict)
    delta = ProbeTaxonomyDelta(**(row.taxonomy_delta or {}))

    meta = row.topic_probe_meta or {}
    scope = meta.get("scope") or "**/*"
    commit_sha = meta.get("commit_sha")

    return ProbeRunResult(
        id=row.id,
        topic=row.topic or "",
        scope=scope,
        intent_hint=row.intent_hint or "",
        repo_full_name=row.repo_full_name or "",
        project_id=row.project_id,
        commit_sha=commit_sha,
        started_at=row.started_at,
        completed_at=row.completed_at,
        prompts_generated=row.prompts_generated or 0,
        prompt_results=prompt_results,
        aggregate=agg,
        taxonomy_delta=delta,
        final_report=row.final_report or "",
        status=row.status,
        suite_id=row.suite_id,
    )


def _ctx_supports_progress(ctx: Any) -> bool:
    """Return True iff ``ctx`` exposes an ``async``-callable ``report_progress``.

    The FastMCP runtime supplies a ``Context`` instance with
    ``async def report_progress(progress, total=None, message=None)``;
    test clients sometimes pass ``None`` or an opaque object. The bridge
    must skip silently in those cases — progress reporting is best-effort.
    """
    if ctx is None:
        return False
    fn = getattr(ctx, "report_progress", None)
    return callable(fn)


_TERMINAL_BRIDGE_EVENTS: frozenset[str] = frozenset({
    "probe_completed",
    "probe_failed",
})


async def _bridge_bus_to_ctx(subscription: Any, ctx: Any) -> None:
    """Forward ``probe_prompt_completed`` events into ``ctx.report_progress``.

    Iterates the per-run subscription until a terminal event
    (``probe_completed`` / ``probe_failed``) arrives or the subscription
    is closed. Each ``probe_prompt_completed`` event becomes one call to
    ``ctx.report_progress(progress=current, total=total, message=…)``.

    Fail-soft on every individual forward: any exception raised by
    ``ctx.report_progress`` (transport error, serialization failure, …)
    is swallowed with a debug log so a single bad forward cannot
    interrupt the rest of the bridge or the probe run itself.

    The unsupported-ctx branch still drains the subscription to terminal
    rather than returning early so any events buffered after the bridge
    starts are flushed cleanly before ``handle_probe``'s ``finally``
    closes the subscription. Subscription typed ``Any`` to avoid an
    import cycle through ``event_bus._RunSubscription``.
    """
    forward_progress = _ctx_supports_progress(ctx)

    async for event in subscription:
        if forward_progress and event.kind == "probe_prompt_completed":
            payload = event.payload or {}
            current = payload.get("current")
            total = payload.get("total")
            try:
                await ctx.report_progress(
                    progress=current,
                    total=total,
                    message=f"prompt {current}/{total}",
                )
            except Exception:  # noqa: BLE001 — best-effort
                # Progress reporting is best-effort. Swallow the failure
                # so the bridge keeps draining the queue and the run
                # completes faithfully even when the IDE-side transport
                # errors on a single forward.
                logger.debug(
                    "bus→ctx bridge: report_progress failed; continuing",
                    exc_info=True,
                )
        if event.kind in _TERMINAL_BRIDGE_EVENTS:
            break


async def handle_probe(
    topic: str,
    scope: str | None = None,
    intent_hint: Literal["audit", "refactor", "explore", "regression-test"] | None = None,
    n_prompts: int | None = None,
    repo_full_name: str | None = None,
    ctx=None,  # FastMCP Context | None — bridge forwards progress when present
    _orchestrator: Any | None = None,
) -> ProbeRunResult:
    """MCP tool handler for ``synthesis_probe`` — dispatch via RunOrchestrator.

    Pipeline:
      1. Validate inputs through ``ProbeRunRequest`` (Pydantic enforces
         topic length 3-500, n_prompts range 5-25)
      2. Pre-flight gate: missing ``repo_full_name`` → ``ProbeError``
         (mirrors the REST router's ``link_repo_first`` short-circuit)
      3. Mint ``run_id``; register ``event_bus.subscribe_for_run(run_id)``
         BEFORE dispatch (Cycle 11 race-free pattern); kick off the
         bridge task that forwards bus → ``ctx.report_progress``
      4. Build ``RunRequest`` and dispatch via
         ``RunOrchestrator.run('topic_probe', ...)`` with the
         pre-allocated ``run_id``
      5. ``finally``: cancel the bridge task + ``aclose`` the subscription
         so ``event_bus._subscribers`` returns to its pre-call cardinality
         (no leaks — verified by ``test_handle_probe_cleans_up_…``)
      6. Read the resulting ``RunRow`` and shape into ``ProbeRunResult``

    The response shape is preserved byte-for-byte (spec § 7.1).

    Bridge guarantees (Cycle 14, AC-C6-3 restored):
      - Race-free: subscription registered before orchestrator starts
      - Fail-soft: ``ctx is None``, missing ``report_progress`` attr,
        and exceptions raised inside ``report_progress`` are all swallowed
      - No task leak: bridge task cancelled in ``finally``
      - No subscription leak: ``await subscription.aclose()`` in ``finally``
    """
    # Pydantic boundary validation — keeps the production contract enforced
    # even when callers bypass the ``@mcp.tool`` Field constraints.
    request = ProbeRunRequest(
        topic=topic,
        scope=scope,
        intent_hint=intent_hint,
        n_prompts=n_prompts,
        repo_full_name=repo_full_name,
    )

    if not request.repo_full_name:
        # Carry the reason code in the exception message so callers
        # matching on ``link_repo_first`` (e.g. existing C5/C6 tests +
        # Cycle 13 cat-8 tests) see it without inspecting the `.reason`
        # attribute. Mirrors the pre-Foundation handler's surface.
        raise ProbeError(
            "link_repo_first",
            message=(
                "link_repo_first: Link a GitHub repo before running a topic probe."
            ),
        )

    if _orchestrator is None:
        _orchestrator = _resolve_orchestrator()

    # Race-free pattern: pre-mint run_id, subscribe BEFORE dispatching the
    # orchestrator. The 500ms ring-buffer replay inside _RunSubscription is
    # defense-in-depth; subscribing here means the buffer cannot miss any
    # event the generator publishes.
    run_id = str(uuid.uuid4())
    subscription = event_bus.subscribe_for_run(run_id)
    bridge_task: asyncio.Task[None] = asyncio.create_task(
        _bridge_bus_to_ctx(subscription, ctx),
    )

    try:
        payload = request.model_dump()
        run_request = RunRequest(mode="topic_probe", payload=payload)
        run_row = await _orchestrator.run(
            "topic_probe", run_request, run_id=run_id,
        )
    finally:
        # Cancel + close in this order so the bridge stops iterating
        # before the subscription's queue is sentinel-closed; this avoids
        # the bridge picking up the sentinel mid-iteration as an event.
        bridge_task.cancel()
        try:
            await bridge_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            # Cancellation is expected; any other exception is best-effort.
            pass
        try:
            await subscription.aclose()
        except Exception:  # noqa: BLE001
            # Subscription cleanup is best-effort — the underlying
            # discard from event_bus._subscribers happens unconditionally
            # via _RunSubscription._cleanup.
            logger.debug(
                "bus→ctx bridge: subscription.aclose() failed; ignoring",
                exc_info=True,
            )

    return _row_to_probe_run_result(run_row)
