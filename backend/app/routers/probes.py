"""Topic Probe REST surface (Tier 1, v0.4.12 + Foundation P3, v0.4.18).

Refactored to a backward-compat shim under Foundation P3 (Cycle 11):

  - ``POST /api/probes`` — race-free SSE stream. The router mints a
    ``run_id``, registers an ``event_bus.subscribe_for_run`` subscription
    BEFORE dispatching the run, then iterates the subscription to stream
    events to the client. Pydantic ``ValidationError`` translates to a
    canonical HTTP 400 with ``detail='invalid_request'``. Missing
    ``repo_full_name`` short-circuits to a 400 with ``detail='link_repo_first'``.
  - ``GET /api/probes`` — paginated list (``ProbeListResponse``), sorted
    by ``started_at desc``. Reads from ``RunRow WHERE mode='topic_probe'``.
  - ``GET /api/probes/{probe_id}`` — full ``ProbeRunResult``. 404 with
    ``probe_not_found`` reason code on miss.

The 8 SSE event types (``probe_started``, ``probe_grounding``,
``probe_generating``, ``probe_prompt_completed``, ``probe_completed``,
``probe_failed``, ``ProbeRateLimitedEvent``, ``rate_limit_active``) are
preserved byte-for-byte. Only additive change: every payload carries a
``run_id`` field for cross-channel correlation.

See:
  - ``docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md`` § 6.2
  - ``docs/specs/topic-probe-2026-04-29.md`` § 4.6 (pre-Foundation contract)
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies.rate_limit import RateLimit
from app.models import RunRow
from app.schemas.pipeline_contracts import SCORING_FORMULA_VERSION
from app.schemas.probes import (
    ProbeAggregate,
    ProbeListResponse,
    ProbePromptResult,
    ProbeRunRequest,
    ProbeRunResult,
    ProbeRunSummary,
    ProbeTaxonomyDelta,
)
from app.schemas.runs import RunRequest
from app.services.event_bus import event_bus
from app.utils.asyncio_helpers import _swallow_task_exception
from app.utils.sse import format_sse

# Foundation P3 Cycle 14 (v0.4.18) — ``get_probe_service`` re-export
# retired alongside the ``ProbeService`` class itself. The router now
# dispatches via ``RunOrchestrator`` directly; tests no longer need a
# ``Depends(...)`` factory to override.
__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["probes"])

_PROBE_RATE_LIMIT = RateLimit(lambda: settings.PROBE_RATE_LIMIT)


# ---------------------------------------------------------------------------
# Serialization helpers — read from RunRow WHERE mode='topic_probe'
# ---------------------------------------------------------------------------


def _serialize_summary(row: RunRow) -> ProbeRunSummary:
    """Project a ``RunRow`` row down to the compact ``ProbeRunSummary``.

    Only invoked on rows where ``mode='topic_probe'``; ``topic`` and
    ``repo_full_name`` are guaranteed populated by the orchestrator's
    ``_create_row`` for probe-mode dispatches. Defensive ``or ''``
    fallbacks keep legacy rows that may have been written before the
    P3 contract from raising on serialization.
    """
    mean_overall: float | None = None
    agg = row.aggregate or {}
    if isinstance(agg, dict):
        v = agg.get("mean_overall")
        if isinstance(v, (int, float)):
            mean_overall = float(v)
    return ProbeRunSummary(
        id=row.id,
        topic=row.topic or "",
        repo_full_name=row.repo_full_name or "",
        started_at=row.started_at,
        completed_at=row.completed_at,
        status=row.status,
        prompts_generated=row.prompts_generated or 0,
        mean_overall=mean_overall,
    )


def _serialize_full(row: RunRow) -> ProbeRunResult:
    """Hydrate a ``RunRow`` row into the full ``ProbeRunResult`` model.

    Reads ``scope`` and ``commit_sha`` from ``topic_probe_meta`` JSON
    column (P3 unified storage) — those used to live as dedicated
    columns on the legacy ``probe_run`` table.
    """
    prompt_results = [ProbePromptResult(**r) for r in (row.prompt_results or [])]
    agg_dict = row.aggregate or {
        "scoring_formula_version": SCORING_FORMULA_VERSION,
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
        status=row.status,  # type: ignore[arg-type]
        suite_id=row.suite_id,
    )


# ---------------------------------------------------------------------------
# POST /api/probes — race-free SSE streaming run via RunOrchestrator
# ---------------------------------------------------------------------------


@router.post(
    "/probes",
    dependencies=[Depends(_PROBE_RATE_LIMIT)],
)
async def post_probe(
    request: Request,
    prefer: str | None = Header(None, alias="Prefer"),
):
    """Kick off a topic probe.

    Two dispatch modes share the same body validation + rate-limit gate:

      * **Default (no ``Prefer`` header)** — race-free SSE stream. The
        router mints a ``run_id``, registers an
        ``event_bus.subscribe_for_run`` subscription BEFORE dispatching
        the run, then iterates the subscription to stream events to the
        client. Terminates on ``probe_completed`` / ``probe_failed``.
        Spec § 6.2 contract; unchanged from v0.4.18.

      * **``Prefer: respond-async`` (T2 Cycle 8, v0.4.22)** — returns
        ``202 Accepted`` immediately after the initial ``RunRow`` INSERT
        commits, with ``Location`` + ``Retry-After`` headers and a
        JSON body ``{run_id, status, poll_url}``. The spawned
        background task drives the run to terminal state without
        holding the HTTP connection. Callers poll
        ``GET /api/probes/{run_id}`` until ``status`` reaches a
        terminal value. RFC 7240 / spec § 8 + § 10 Cycle 8.

    Race-free SSE pattern (default path):
      1. Caller mints ``run_id = str(uuid.uuid4())``
      2. Construct ``event_bus.subscribe_for_run(run_id)`` BEFORE dispatch
      3. ``asyncio.create_task(orchestrator.run("topic_probe", ..., run_id=run_id))``
      4. SSE stream iterates the subscription; terminates on
         ``probe_completed`` or ``probe_failed``
      5. ``finally: await subscription.aclose()`` — no leaked subscribers
         on client disconnect

    202+polling pattern (Cycle 8):
      1. Caller mints ``run_id = uuid.uuid4().hex``
      2. ``orchestrator.dispatch_async(...)`` awaits the initial INSERT
         then spawns ``_run_to_completion`` — race-safe immediate poll
      3. Router returns 202 with ``Location: /api/probes/{run_id}`` +
         ``Retry-After: 5`` + JSON body
      4. Caller polls ``GET /api/probes/{run_id}`` until terminal

    Pre-stream gates (preserve pre-Foundation reason codes, shared by
    both dispatch modes):
      - Missing ``repo_full_name`` AND ``grounding_mode='codebase'`` →
        400 ``link_repo_first`` (T2 Cycle 7: gate is mode-aware so
        ``grounding_mode='topic_only'`` requests bypass it)
      - Pydantic validation failure → 400 ``invalid_request``
      - Malformed JSON → 400 ``invalid_json``
    """
    try:
        raw = await request.json()
    except Exception as exc:  # noqa: BLE001 — translate to 400 with reason
        raise HTTPException(status_code=400, detail="invalid_json") from exc

    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="invalid_request")

    # T2 Cycle 7 (spec §5 + §4 error envelope): the ``link_repo_first`` gate
    # only fires under codebase mode. Topic-only requests do not require a
    # linked repo — that's the whole point of the mode. The raw-dict lookup
    # uses the same default ``"codebase"`` as ``ProbeRunRequest.grounding_mode``
    # so omitting the field preserves the v0.4.12 contract.
    raw_grounding_mode = raw.get("grounding_mode", "codebase")
    if raw_grounding_mode == "codebase" and not raw.get("repo_full_name"):
        raise HTTPException(status_code=400, detail="link_repo_first")

    try:
        body = ProbeRunRequest(**raw)
    except ValidationError as exc:
        # Translate Pydantic's 422 into the router's canonical 400
        # ``invalid_request`` reason code so probe clients have a single
        # error shape to switch on. Validation details are still
        # available via the exception's ``errors()`` for logging.
        logger.info(
            "POST /api/probes: invalid request body — %s",
            exc.errors(),
        )
        raise HTTPException(status_code=400, detail="invalid_request") from exc

    orchestrator = getattr(request.app.state, "run_orchestrator", None)
    if orchestrator is None:
        # Surfaces only if lifespan failed to register the orchestrator
        # (e.g., WriteQueue init failed). Probe-mode SSE cannot proceed.
        raise HTTPException(
            status_code=503,
            detail="run_orchestrator_unavailable",
        )

    run_request = RunRequest(mode="topic_probe", payload=body.model_dump())

    # T2 Cycle 8: branch on ``Prefer: respond-async``. RFC 7240 says the
    # header value is a comma-separated list of preference tokens; case-
    # insensitive substring match keeps us tolerant of variants like
    # ``respond-async, wait=10`` without forcing a strict-equality
    # comparison. The header is absent on the legacy SSE path.
    is_async = prefer is not None and "respond-async" in prefer.lower()

    if is_async:
        # 202+polling path — spec § 8 line 289-293 response shape.
        # ``uuid.uuid4().hex`` matches the canonical 202 router pattern
        # in routers/suites.py::replay_suite for cross-router consistency.
        run_id = uuid.uuid4().hex

        # dispatch_async awaits the initial RunRow INSERT before
        # returning, so the caller's immediate
        # ``GET /api/probes/{run_id}`` is race-safe — the row is
        # already committed when we hand back the 202.
        await orchestrator.dispatch_async(
            mode="topic_probe",
            request=run_request,
            run_id=run_id,
        )

        poll_url = f"/api/probes/{run_id}"
        # ``Location`` mirrors ``poll_url`` (RFC 7240); ``Retry-After``
        # is a polling-cadence hint — integer seconds, default 5 per
        # spec § 8 line 291. Body shape per spec § 8 line 292.
        return JSONResponse(
            status_code=202,
            content={
                "run_id": run_id,
                "status": "running",
                "poll_url": poll_url,
            },
            headers={
                "Location": poll_url,
                "Retry-After": "5",
            },
        )

    # Default SSE path — unchanged from v0.4.18.
    run_id = str(uuid.uuid4())

    # Subscribe FIRST so the buffer captures any events the orchestrator
    # publishes during _create_row + generator start. The 500ms ring-buffer
    # replay inside _RunSubscription is defense-in-depth for any caller
    # that subscribes after dispatch (e.g., reconnecting clients).
    subscription = event_bus.subscribe_for_run(run_id)

    # Kick off run as background task with pre-allocated run_id.
    run_task: asyncio.Task[Any] = asyncio.create_task(
        orchestrator.run("topic_probe", run_request, run_id=run_id),
    )
    # Suppress "Task exception was never retrieved" warnings — the SSE
    # consumer doesn't await the task itself; the orchestrator handles
    # its own failure marking under asyncio.shield, and the bus events
    # carry probe_failed for the client.
    run_task.add_done_callback(_swallow_task_exception)

    async def event_stream():
        try:
            async for event in subscription:
                yield format_sse(event.kind, event.payload)
                # Termination on terminal events only. Rate-limit events
                # (``ProbeRateLimitedEvent``, ``rate_limit_active``) are
                # informational, not terminal.
                if event.kind in ("probe_completed", "probe_failed"):
                    break
        finally:
            await subscription.aclose()
            # If the client disconnects, run_task may still be running;
            # the orchestrator handles its own cancellation/cleanup via
            # ``asyncio.shield`` in ``_mark_failed``.

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /api/probes — paginated list (RunRow WHERE mode='topic_probe')
# ---------------------------------------------------------------------------


@router.get("/probes", response_model=ProbeListResponse)
async def list_probes(
    status: str | None = Query(None, description="Filter by run status."),
    project_id: str | None = Query(None, description="Filter by project node id."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> ProbeListResponse:
    """Return a paginated, ``started_at desc`` view of topic-probe runs.

    Only ``RunRow`` rows with ``mode='topic_probe'`` are surfaced — seed
    runs live under ``GET /api/seed`` (Cycle 12). Both modes share the
    same underlying table.
    """
    base = select(RunRow).where(RunRow.mode == "topic_probe")
    if status is not None:
        base = base.where(RunRow.status == status)
    if project_id is not None:
        base = base.where(RunRow.project_id == project_id)

    total_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(total_q)).scalar_one()

    page_q = base.order_by(RunRow.started_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(page_q)).scalars().all()
    items = [_serialize_summary(r) for r in rows]

    has_more = offset + len(items) < total
    next_offset = offset + len(items) if has_more else None

    return ProbeListResponse(
        total=int(total),
        count=len(items),
        offset=offset,
        items=items,
        has_more=has_more,
        next_offset=next_offset,
    )


# ---------------------------------------------------------------------------
# GET /api/probes/{probe_id} — single fetch
# ---------------------------------------------------------------------------


@router.get("/probes/{probe_id}", response_model=ProbeRunResult)
async def get_probe(
    probe_id: str,
    db: AsyncSession = Depends(get_db),
) -> ProbeRunResult:
    """Return the full ``ProbeRunResult`` for a probe id, or 404.

    Defends against accidentally returning a seed-mode row that happens
    to share the id space — though the orchestrator never reuses ids,
    the explicit ``mode == 'topic_probe'`` guard preserves the surface
    contract that this endpoint only exposes probe runs.
    """
    row = await db.get(RunRow, probe_id)
    if row is None or row.mode != "topic_probe":
        raise HTTPException(status_code=404, detail="probe_not_found")
    return _serialize_full(row)


# ---------------------------------------------------------------------------
# Type-checker placeholder — keep ``Any`` import-time available for the
# ``asyncio.Task[Any]`` annotation above without polluting public API.
# ---------------------------------------------------------------------------

from typing import Any  # noqa: E402  (kept after the routes for readability)
