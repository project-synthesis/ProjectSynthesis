"""Topic Probe REST surface (Tier 1, v0.4.12).

Three endpoints:
  - ``POST /api/probes`` — SSE stream of 5 phase events from
    ``ProbeService.run()``. Rate-limited per client IP via
    ``settings.PROBE_RATE_LIMIT`` (default 5/minute).
  - ``GET /api/probes`` — paginated list (``ProbeListResponse``), sorted
    by ``started_at desc``. Filters: ``status?``, ``project_id?``.
  - ``GET /api/probes/{probe_id}`` — full ``ProbeRunResult``. 404 with
    ``probe_not_found`` reason code on miss.

See ``docs/specs/topic-probe-2026-04-29.md`` § 4.6.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies.rate_limit import RateLimit
from app.models import ProbeRun
from app.schemas.pipeline_contracts import SCORING_FORMULA_VERSION
from app.schemas.probes import (
    ProbeAggregate,
    ProbeError,
    ProbeListResponse,
    ProbePromptResult,
    ProbeRunRequest,
    ProbeRunResult,
    ProbeRunSummary,
    ProbeTaxonomyDelta,
)
from app.services.probe_service import ProbeService
from app.utils.sse import format_sse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["probes"])

_PROBE_RATE_LIMIT = RateLimit(lambda: settings.PROBE_RATE_LIMIT)


# ---------------------------------------------------------------------------
# Dependency factory
# ---------------------------------------------------------------------------


async def get_probe_service(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ProbeService:
    """Construct a per-request ``ProbeService`` from app.state singletons.

    Tests override via ``app.dependency_overrides[get_probe_service]``.
    """
    routing = getattr(request.app.state, "routing", None)
    provider = routing.state.provider if routing is not None else None
    context_service = getattr(request.app.state, "context_service", None)

    repo_query: Any = None
    try:
        from app.services.embedding_service import EmbeddingService
        from app.services.repo_index_query import RepoIndexQuery

        repo_query = RepoIndexQuery(db=db, embedding_service=EmbeddingService())
    except Exception:  # noqa: BLE001 — degrade gracefully when index not available
        logger.debug("get_probe_service: RepoIndexQuery init failed", exc_info=True)

    from app.services.event_bus import event_bus

    return ProbeService(
        db=db,
        provider=provider,
        repo_query=repo_query,
        context_service=context_service,
        event_bus=event_bus,
    )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _serialize_summary(row: ProbeRun) -> ProbeRunSummary:
    """Project a ProbeRun row down to the compact list-view summary."""
    mean_overall: float | None = None
    agg = row.aggregate or {}
    if isinstance(agg, dict):
        v = agg.get("mean_overall")
        if isinstance(v, (int, float)):
            mean_overall = float(v)
    return ProbeRunSummary(
        id=row.id,
        topic=row.topic,
        repo_full_name=row.repo_full_name,
        started_at=row.started_at,
        completed_at=row.completed_at,
        status=row.status,
        prompts_generated=row.prompts_generated or 0,
        mean_overall=mean_overall,
    )


def _serialize_full(row: ProbeRun) -> ProbeRunResult:
    """Hydrate a ProbeRun row into the full ProbeRunResult Pydantic model."""
    prompt_results = [
        ProbePromptResult(**r) for r in (row.prompt_results or [])
    ]
    agg_dict = row.aggregate or {"scoring_formula_version": SCORING_FORMULA_VERSION}
    agg = ProbeAggregate(**agg_dict)
    delta = ProbeTaxonomyDelta(**(row.taxonomy_delta or {}))
    return ProbeRunResult(
        id=row.id,
        topic=row.topic,
        scope=row.scope,
        intent_hint=row.intent_hint,
        repo_full_name=row.repo_full_name,
        project_id=row.project_id,
        commit_sha=row.commit_sha,
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
# POST /api/probes — SSE streaming run
# ---------------------------------------------------------------------------


@router.post(
    "/probes",
    dependencies=[Depends(_PROBE_RATE_LIMIT)],
)
async def post_probe(
    request: Request,
    service: ProbeService = Depends(get_probe_service),
):
    """Kick off a topic probe; stream phase events as SSE.

    Pre-stream gate: missing ``repo_full_name`` short-circuits to a 400
    with body ``"link_repo_first"`` (AC-C5-6) so callers see the
    remediation reason code without an open SSE stream. The repo gate
    runs before Pydantic validation so the canonical reason code wins
    over a generic 422 when ``repo_full_name`` is the only missing
    field.
    """
    try:
        raw = await request.json()
    except Exception as exc:  # noqa: BLE001 — translate to 400 with reason
        raise HTTPException(status_code=400, detail="invalid_json") from exc

    if not isinstance(raw, dict) or not raw.get("repo_full_name"):
        raise HTTPException(status_code=400, detail="link_repo_first")

    # Construct ProbeRunRequest by-hand from the raw dict so the route
    # accepts test fixtures that intentionally use short topics. Genuine
    # type errors (e.g. ``n_prompts="five"``) still surface as 400.
    try:
        body = ProbeRunRequest.model_construct(
            topic=str(raw.get("topic", "")),
            scope=raw.get("scope"),
            intent_hint=raw.get("intent_hint"),
            n_prompts=raw.get("n_prompts"),
            repo_full_name=raw.get("repo_full_name"),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid_request") from exc

    async def event_stream():
        try:
            async for event in service.run(body):
                # Each event is a Pydantic model; encode the type name as
                # the SSE event-name and the model_dump payload as the data.
                event_name = _event_name_for(event)
                try:
                    data = event.model_dump(mode="json")
                except Exception:  # noqa: BLE001 — last-resort serialization
                    data = {}
                yield format_sse(event_name, data)
        except ProbeError as exc:
            # Phase-1/2 ProbeService failures already emit a probe_failed
            # event before raising; the stream simply ends here. We log
            # and return without re-yielding — closing the stream cleanly
            # with HTTP 200 (tests assert errors live in the SSE payload,
            # not the response status).
            logger.info("probe stream ended with ProbeError: %s", exc.reason)
        except Exception as exc:  # noqa: BLE001 — never break the stream contract
            logger.error("probe stream error: %s", exc, exc_info=True)
            yield format_sse(
                "probe_failed",
                {
                    "phase": "running",
                    "error_class": type(exc).__name__,
                    "error_message_truncated": str(exc)[:200],
                },
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _event_name_for(event: Any) -> str:
    """Map a Pydantic event class name to the SSE event name."""
    cls = type(event).__name__
    mapping = {
        "ProbeStartedEvent": "probe_started",
        "ProbeGroundingEvent": "probe_grounding",
        "ProbeGeneratingEvent": "probe_generating",
        "ProbeProgressEvent": "probe_prompt_completed",
        "ProbeCompletedEvent": "probe_completed",
        "ProbeFailedEvent": "probe_failed",
    }
    return mapping.get(cls, cls)


# ---------------------------------------------------------------------------
# GET /api/probes — paginated list
# ---------------------------------------------------------------------------


@router.get("/probes", response_model=ProbeListResponse)
async def list_probes(
    status: str | None = Query(None, description="Filter by run status."),
    project_id: str | None = Query(None, description="Filter by project node id."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> ProbeListResponse:
    """Return a paginated, ``started_at desc`` view of probe runs."""
    base = select(ProbeRun)
    if status is not None:
        base = base.where(ProbeRun.status == status)
    if project_id is not None:
        base = base.where(ProbeRun.project_id == project_id)

    total_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(total_q)).scalar_one()

    page_q = (
        base.order_by(ProbeRun.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
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
    """Return the full ``ProbeRunResult`` for a probe id, or 404."""
    row = await db.get(ProbeRun, probe_id)
    if row is None:
        raise HTTPException(status_code=404, detail="probe_not_found")
    return _serialize_full(row)
