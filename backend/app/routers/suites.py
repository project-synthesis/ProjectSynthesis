"""ValidationSuite REST surface — Topic Probe Tier 2 (v0.4.22).

Spec: ``docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md`` §4.

Six endpoints, all of which delegate persistence to
:class:`ValidationSuiteService` (Cycles 2-4) — the router NEVER touches the DB
directly except for the replay-placeholder ``RunRow`` INSERT, which routes
through ``app.state.write_queue.submit()`` per the Foundation P4 contract.

Error envelope
--------------
``ValueError("<code>")`` raised by the service maps to
``HTTPException(status, detail=<code>)`` via :func:`_map_service_error`. The
10-code → status table (spec §4 lines 337-346) is encoded in
:data:`_ERROR_CODE_STATUS` — a single source of truth so the router stays
declarative.

Pydantic ``ValidationError`` (raised on body-field constraint violations like
empty label / tolerance outside `[0.1, 5.0]` / oversized reason) is translated
into the canonical 400 envelope with code = ``invalid_label`` /
``invalid_tolerance`` / ``invalid_reason``. FastAPI's default 422 response
would break the spec §4 error-envelope contract.

Replay endpoint (Cycle 5 GREEN-step shape per spec §10 Cycle 5)
---------------------------------------------------------------
:func:`replay_suite` returns 202 immediately after INSERTing the initial
``RunRow(mode='replay_run', status='running', suite_id=...)`` through the
write queue. NO generator spawn — that lands in Cycle 6 (registers
``ReplayRunGenerator`` in lifespan) + Cycle 8 (``dispatch_async``) together.
The orphan ``status='running'`` row is intentional and reconciled by the
existing ``_gc_orphan_runs`` sweep within 1h TTL.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError

from app.dependencies.rate_limit import RateLimit
from app.models import RunRow
from app.schemas.runs import RunListResponse
from app.schemas.validation_suite import (
    ReplayRunOut,
    RetireSuiteRequest,
    SaveSuiteRequest,
    ValidationSuiteListResponse,
    ValidationSuiteOut,
)
from app.services.validation_suite_service import ValidationSuiteService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["suites"])

# Module-level service instance. The service is stateful only on the regression
# alarm cache + prior-state map — neither is touched by these endpoints, so a
# single shared instance is safe across requests. The alarm cache is
# instance-scoped per the service docstring; the health-endpoint code path
# owns its own instance.
_service = ValidationSuiteService()


# ---------------------------------------------------------------------------
# Error envelope mapping — spec §4 lines 337-346
# ---------------------------------------------------------------------------


_ERROR_CODE_STATUS: dict[str, int] = {
    # 400 — request shape / target is structurally wrong
    "not_a_probe_run": 400,
    "invalid_label": 400,
    "invalid_tolerance": 400,
    "invalid_reason": 400,
    "topic_only_unavailable": 400,
    "link_repo_first": 400,
    # 404 — resource not found
    "run_not_found": 404,
    "suite_not_found": 404,
    # 409 — state conflict
    "run_not_completed": 409,
    "run_missing_aggregate": 409,
    "suite_retired": 409,
}


def _map_service_error(exc: ValueError) -> HTTPException:
    """Translate a service-layer ``ValueError("<code>")`` into the canonical envelope.

    The service raises bare-code ``ValueError``s; the router maps the code to
    a status via :data:`_ERROR_CODE_STATUS`. Unknown codes (defensive — the
    service contract is the canonical source) fall back to 500 with the raw
    code so they surface in logs rather than masquerading as 200.
    """
    code = str(exc).strip() or "unknown"
    status = _ERROR_CODE_STATUS.get(code, 500)
    return HTTPException(status_code=status, detail=code)


def _classify_validation_error(exc: ValidationError, *, route: str) -> HTTPException:
    """Map a Pydantic ``ValidationError`` to the right 400-code envelope.

    Field-name → code mapping is route-scoped because different bodies share
    field names (``label`` lives on :class:`SaveSuiteRequest` only;
    ``reason`` on :class:`RetireSuiteRequest` only).
    """
    # First-error field name drives the code. Pydantic's ``errors()`` is a
    # list of dicts; ``loc`` is a tuple — index [-1] is the field.
    errors = exc.errors()
    field = errors[0]["loc"][-1] if errors and errors[0].get("loc") else None

    if route == "save_as_suite":
        if field == "label":
            return HTTPException(status_code=400, detail="invalid_label")
        if field == "tolerance_abs":
            return HTTPException(status_code=400, detail="invalid_tolerance")
        # Defensive: unknown field on save-as-suite body falls back to invalid_label
        # so the response is still 400 with a known code (matches the test
        # contract on the 400 status, even if the field is unexpected).
        return HTTPException(status_code=400, detail="invalid_label")

    if route == "retire":
        # Only field on RetireSuiteRequest is ``reason``.
        return HTTPException(status_code=400, detail="invalid_reason")

    # Unknown route — defensive fallback. This branch is never hit in
    # production because every Pydantic-validated body is route-scoped.
    return HTTPException(status_code=400, detail="invalid_request")


# ---------------------------------------------------------------------------
# Endpoint 1 — POST /api/probes/{run_id}/save-as-suite (spec §4 line 272)
# ---------------------------------------------------------------------------


@router.post(
    "/probes/{run_id}/save-as-suite",
    response_model=ValidationSuiteOut,
    status_code=201,
    dependencies=[Depends(RateLimit(lambda: "20/minute"))],
)
async def save_as_suite(
    run_id: str,
    request: Request,
) -> ValidationSuiteOut:
    """Fork a completed ``topic_probe`` run into an immutable ValidationSuite.

    Spec §4 line 272 — 20/min rate limit, 201 on success.
    """
    # Parse + validate body manually so a Pydantic ``ValidationError`` maps to
    # the spec §4 400-with-code envelope rather than FastAPI's default 422.
    try:
        raw = await request.json()
    except Exception as exc:  # noqa: BLE001 — translate to 400
        raise HTTPException(status_code=400, detail="invalid_json") from exc

    try:
        body = SaveSuiteRequest.model_validate(raw)
    except ValidationError as exc:
        raise _classify_validation_error(exc, route="save_as_suite") from exc

    write_queue = request.app.state.write_queue
    try:
        return await _service.create_from_run(
            run_id,
            label=body.label,
            tolerance_abs=body.tolerance_abs,
            write_queue=write_queue,
        )
    except ValueError as exc:
        raise _map_service_error(exc) from exc


# ---------------------------------------------------------------------------
# Endpoint 2 — GET /api/suites (spec §4 line 273)
# ---------------------------------------------------------------------------


@router.get("/suites", response_model=ValidationSuiteListResponse)
async def list_suites(
    project_id: str | None = Query(None),
    repo_full_name: str | None = Query(None),  # noqa: ARG001 — spec parity (filter passthrough TBD)
    include_retired: bool = Query(False),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> ValidationSuiteListResponse:
    """Paginated list of validation suites.

    Spec §4 line 273 — default filters out retired suites. ``project_id``
    narrows to the matching project; ``repo_full_name`` is accepted for spec
    parity but currently a passthrough (the service primitive accepts only
    ``project_id`` as a narrowing filter in Cycle 5; if a future cycle adds
    repo-level filtering it lands in the service surface without breaking
    this endpoint).
    """
    return await _service.list(
        include_retired=include_retired,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# Endpoint 3 — GET /api/suites/{suite_id} (spec §4 line 274)
# ---------------------------------------------------------------------------


@router.get("/suites/{suite_id}", response_model=ValidationSuiteOut)
async def get_suite(suite_id: str) -> ValidationSuiteOut:
    """Full :class:`ValidationSuiteOut` read view.

    Spec §4 line 274 — 200 / 404.
    """
    try:
        return await _service.get(suite_id)
    except ValueError as exc:
        raise _map_service_error(exc) from exc


# ---------------------------------------------------------------------------
# Endpoint 4 — GET /api/suites/{suite_id}/replays (spec §4 line 275)
# ---------------------------------------------------------------------------


@router.get("/suites/{suite_id}/replays", response_model=RunListResponse)
async def list_suite_replays(
    suite_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> RunListResponse:
    """Paginated list of replay runs scoped to this suite.

    Spec §4 line 275 — items are ``RunSummary`` filtered to
    ``mode='replay_run' AND suite_id={suite_id}``. Unknown suites yield an
    empty envelope (REST convention for resource-scoped collections).
    """
    return await _service.list_replays(suite_id, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Endpoint 5 — POST /api/suites/{suite_id}/replay (spec §4 line 276)
# ---------------------------------------------------------------------------


@router.post(
    "/suites/{suite_id}/replay",
    response_model=ReplayRunOut,
    status_code=202,
    dependencies=[Depends(RateLimit(lambda: "5/minute"))],
)
async def replay_suite(
    suite_id: str,
    request: Request,
    response: Response,
) -> ReplayRunOut:
    """Kick off a replay run against a suite (placeholder dispatch).

    Spec §4 line 276 — 202 Accepted, 5/min rate limit. ``Location`` header
    mirrors ``poll_url`` (RFC 7240); ``Retry-After`` is a polling hint.

    Cycle 5 GREEN-step shape per spec §10 Cycle 5: this handler INSERTs the
    initial ``RunRow(mode='replay_run', status='running', suite_id=...)``
    via ``WriteQueue.submit()`` and returns 202 immediately — NO generator
    spawn yet. Cycle 6 + Cycle 8 together land the real ``dispatch_async``
    + ``ReplayRunGenerator`` registration. The orphan ``status='running'``
    row is reconciled by the existing ``_gc_orphan_runs`` sweep within 1h.

    Precondition checks fire BEFORE the placeholder dispatch — no RunRow is
    written when the suite is missing (404) or retired (409).
    """
    # ---- Precondition: suite must exist + not be retired ----
    try:
        suite = await _service.get(suite_id)
    except ValueError as exc:
        raise _map_service_error(exc) from exc

    if suite.retired_at is not None:
        raise HTTPException(status_code=409, detail="suite_retired")

    # ---- Mint replay run_id + INSERT placeholder RunRow via WriteQueue ----
    run_id = uuid.uuid4().hex
    # Naive UTC for SQLite DateTime column compatibility (matches RunRow
    # column convention — see ``_seed_completed_probe_run`` test helper and
    # ``services/run_orchestrator.py`` _create_row pattern).
    started_at = datetime.now(UTC).replace(tzinfo=None)

    async def _persist_initial_run_row(db: Any) -> None:
        """Writer-queue callback — INSERT the placeholder RunRow.

        Runs on the writer engine. The placeholder row carries every column
        the GC sweep needs to reconcile orphans (``mode='replay_run'``,
        ``status='running'``, ``suite_id``) plus the provenance columns
        (``project_id``, ``repo_full_name``) carried over from the suite.
        """
        row = RunRow(
            id=run_id,
            mode="replay_run",
            status="running",
            started_at=started_at,
            suite_id=suite_id,
            project_id=suite.project_id,
            repo_full_name=suite.repo_full_name,
        )
        db.add(row)
        await db.commit()

    write_queue = request.app.state.write_queue
    await write_queue.submit(
        _persist_initial_run_row,
        timeout=30,
        operation_label="replay_run_create_initial",
    )

    # ---- Response headers + body ----
    poll_url = f"/api/runs/{run_id}"
    response.headers["Location"] = poll_url
    response.headers["Retry-After"] = "5"

    # ``started_at`` is naive UTC at write time; reattach tzinfo for the
    # response so consumers see a timezone-aware ISO timestamp matching the
    # ``ReplayRunOut.started_at: datetime`` schema convention.
    return ReplayRunOut(
        run_id=run_id,
        suite_id=suite_id,
        # ``mode`` + ``status`` have Literal defaults on the schema; explicit
        # for documentation symmetry with the body shape pinned by Test 7.
        mode="replay_run",
        status="running",
        started_at=started_at.replace(tzinfo=UTC),
        poll_url=poll_url,
    )


# ---------------------------------------------------------------------------
# Endpoint 6 — POST /api/suites/{suite_id}/retire (spec §4 line 277)
# ---------------------------------------------------------------------------


@router.post(
    "/suites/{suite_id}/retire",
    response_model=ValidationSuiteOut,
    status_code=200,
    dependencies=[Depends(RateLimit(lambda: "30/minute"))],
)
async def retire_suite(
    suite_id: str,
    request: Request,
) -> ValidationSuiteOut:
    """Idempotent soft-delete of a ValidationSuite.

    Spec §4 line 277 — 30/min rate limit, 200 on success (including re-retire
    no-op per the service idempotency contract). Returns the updated suite
    row so the frontend can refresh its local cache without a follow-up GET.
    """
    # Parse + validate body manually for the 400-code envelope (see
    # ``save_as_suite`` for the rationale).
    try:
        raw = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid_json") from exc

    try:
        body = RetireSuiteRequest.model_validate(raw)
    except ValidationError as exc:
        raise _classify_validation_error(exc, route="retire") from exc

    write_queue = request.app.state.write_queue
    try:
        await _service.retire(
            suite_id,
            reason=body.reason,
            write_queue=write_queue,
        )
        # Return the updated row so callers can immediately observe the
        # ``retired_at`` / ``retired_reason`` transition. ``get`` runs in
        # its own short read session per the service contract — the second
        # retire (idempotent no-op) returns the row in its already-retired
        # state without raising.
        return await _service.get(suite_id)
    except ValueError as exc:
        raise _map_service_error(exc) from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


__all__ = ["router"]


# ``Annotated`` is imported but currently unused in the body — preserved as
# part of the standard router-module import surface so a future cycle adding
# typed Depends() shorthand (e.g., ``Annotated[None, Depends(RateLimit(...))]``)
# doesn't need to touch imports.
_ = Annotated
