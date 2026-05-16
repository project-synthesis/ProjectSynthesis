"""ValidationSuite REST surface — Topic Probe Tier 2 (v0.4.22).

Spec: ``docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md`` §4
(REST surface + error envelope + 10 codes) + §6 (replay flow) + §10 Cycle 5.

Six endpoints, all of which delegate persistence to
:class:`ValidationSuiteService` (Cycles 2-4) — the router NEVER touches the DB
directly. The replay endpoint dispatches the :class:`ReplayRunGenerator`
through ``app.state.run_orchestrator.dispatch_async()``, which performs the
initial ``RunRow`` INSERT inside its own writer closure (Foundation P4 contract).

Endpoints
---------
======================================================  ==================  ===========================
Path + method                                           Status              Rate limit
======================================================  ==================  ===========================
``POST /api/probes/{run_id}/save-as-suite``             201 / 400 / 404 /   20/minute
                                                        409 / 429
``GET /api/suites``                                     200                 unlimited
``GET /api/suites/{suite_id}``                          200 / 404           unlimited
``GET /api/suites/{suite_id}/replays``                  200                 unlimited
``POST /api/suites/{suite_id}/replay``                  202 / 404 / 409 /   5/minute
                                                        429
``POST /api/suites/{suite_id}/retire``                  200 / 400 / 404 /   30/minute
                                                        429
======================================================  ==================  ===========================

Error envelope
--------------
All 4xx responses use the canonical ``{"detail": "<code>"}`` shape (FastAPI
default — matches the ``routers/probes.py`` precedent). Spec §4's
``{code, message}`` wording describes the LOGICAL envelope, not the wire
format. The 10-code → status table (spec §4 lines 337-346) is encoded in
:data:`_ERROR_CODE_STATUS` — single source of truth so the router stays
declarative:

================================  ======  ========================================
Code                              Status  Origin
================================  ======  ========================================
``not_a_probe_run``               400     ``ValidationSuiteService.create_from_run``
``invalid_label``                 400     Pydantic ``SaveSuiteRequest``
``invalid_tolerance``             400     Pydantic ``SaveSuiteRequest``
``invalid_reason``                400     Pydantic ``RetireSuiteRequest``
``topic_only_unavailable``        400     ``ValidationSuiteService`` (replay path)
``link_repo_first``               400     ``ValidationSuiteService`` (replay path)
``run_not_found``                 404     ``ValidationSuiteService.create_from_run``
``suite_not_found``               404     ``ValidationSuiteService.get`` / .retire
``run_not_completed``             409     ``ValidationSuiteService.create_from_run``
``run_missing_aggregate``         409     ``ValidationSuiteService.create_from_run``
``suite_retired``                 409     :func:`replay_suite` precondition
================================  ======  ========================================

``ValueError("<code>")`` raised by the service maps to
``HTTPException(status, detail=<code>)`` via :func:`_map_service_error`.
Pydantic ``ValidationError`` (raised on body-field constraint violations like
empty label / tolerance outside ``[0.1, 5.0]`` / oversized reason) is
translated via :func:`_classify_validation_error` → the canonical 400-with-code
envelope. FastAPI's default 422 response would break the spec §4 error-
envelope contract.

Replay endpoint (spec §10 Cycles 5 + 6 + 8 combined)
----------------------------------------------------
:func:`replay_suite` dispatches the :class:`ReplayRunGenerator` via
``app.state.run_orchestrator.dispatch_async(mode='replay_run', ...)`` and
returns 202 Accepted immediately. ``dispatch_async`` awaits the initial
``RunRow(mode='replay_run', status='running', suite_id=...)`` INSERT BEFORE
returning — the caller's immediate ``GET /api/runs/{run_id}`` is race-safe.
The spawned ``_run_to_completion`` task drives the generator to a terminal
``completed`` / ``partial`` / ``failed`` status; ``_gc_orphan_runs`` reconciles
any rows that miss terminal cleanup (1h TTL — defense-in-depth for crashes).

Scope hardness (spec §14): NO ``PATCH /api/suites/{id}`` route. Suites are
immutable after creation; the ONLY state transition is the retire soft-delete
writing ``retired_at`` / ``retired_reason``. A PATCH attempt returns 405
Method Not Allowed at the routing layer (pinned by
``tests/test_routers_suites.py::test_retire_invalid_reason_returns_400``).

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ValidationError

from app.dependencies.rate_limit import RateLimit
from app.schemas.runs import RunListResponse, RunRequest
from app.schemas.validation_suite import (
    ReplayRunOut,
    RetireSuiteRequest,
    SaveSuiteRequest,
    ValidationSuiteListResponse,
    ValidationSuiteOut,
)
from app.services.validation_suite_service import ValidationSuiteService

# TypeVar bound to ``BaseModel`` lets :func:`_parse_body` return the narrow
# request-body model type without an ``isinstance`` reassertion at the call
# site. Lives below imports so ruff E402 stays satisfied — the bound
# reference (``BaseModel``) must already be in scope.
_BodyT = TypeVar("_BodyT", bound=BaseModel)

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

    Parameters
    ----------
    exc:
        Service-layer exception whose ``str()`` is the canonical error code.

    Returns
    -------
    HTTPException
        Mapped to the spec §4 status (400 / 404 / 409) or 500 for unknown
        codes (defensive fallback — surfaces in logs rather than masquerading
        as a successful response).
    """
    code = str(exc).strip() or "unknown"
    status = _ERROR_CODE_STATUS.get(code, 500)
    return HTTPException(status_code=status, detail=code)


def _classify_validation_error(exc: ValidationError, *, route: str) -> HTTPException:
    """Map a Pydantic ``ValidationError`` to the right 400-code envelope.

    Field-name → code mapping is route-scoped because different bodies share
    field names (``label`` lives on :class:`SaveSuiteRequest` only;
    ``reason`` on :class:`RetireSuiteRequest` only).

    Parameters
    ----------
    exc:
        Pydantic validation error raised by ``model_validate()``.
    route:
        Either ``"save_as_suite"`` or ``"retire"`` — selects the
        field-to-code mapping (Pydantic validation error → spec §4 code).

    Returns
    -------
    HTTPException
        Status 400 with one of ``invalid_label`` / ``invalid_tolerance`` /
        ``invalid_reason`` / ``invalid_request`` (defensive fallback). Never
        raises — caller is responsible for raising the returned exception.
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


async def _parse_body(
    request: Request,
    model: type[_BodyT],
    *,
    route: str,
) -> _BodyT:
    """Parse + validate a request JSON body, translating errors to spec §4 codes.

    Centralized JSON-parse + ``model_validate`` + ``ValidationError`` →
    spec §4 envelope translation. Both :func:`save_as_suite` and
    :func:`retire_suite` need this same translation because FastAPI's
    default 422 response would break the spec §4 error-envelope contract
    (the spec requires 400 with a code-string detail, not 422 with a
    field-path-prefixed error array).

    Parameters
    ----------
    request:
        Live FastAPI request — only used for ``await request.json()``.
    model:
        Pydantic ``BaseModel`` subclass to validate against. Generic via
        :data:`_BodyT` ``TypeVar(..., bound=BaseModel)`` so callers get a
        narrow return type without an ``isinstance`` reassertion.
    route:
        Either ``"save_as_suite"`` or ``"retire"`` — passed to
        :func:`_classify_validation_error` for field-to-code resolution.

    Returns
    -------
    _BodyT
        Validated body as an instance of the requested model class.

    Raises
    ------
    HTTPException
        * 400 ``invalid_json`` — body not parseable as JSON.
        * 400 ``invalid_label`` / ``invalid_tolerance`` / ``invalid_reason``
          — Pydantic validation failed; specific code per the field.
    """
    try:
        raw = await request.json()
    except Exception as exc:  # noqa: BLE001 — translate to 400
        raise HTTPException(status_code=400, detail="invalid_json") from exc
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise _classify_validation_error(exc, route=route) from exc


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

    Spec §4 line 272 — 20/min rate limit. Forking is idempotent at the run
    level (a single run may back multiple distinct suites with different
    labels and tolerances).

    Parameters
    ----------
    run_id:
        Source ``RunRow.id`` (``mode='topic_probe', status='completed'``).
    request:
        FastAPI request — used to (1) parse the JSON body manually so
        Pydantic errors land in the spec §4 400-with-code envelope, and
        (2) reach the per-process ``WriteQueue`` singleton.

    Returns
    -------
    ValidationSuiteOut
        Fully-populated suite snapshot, status 201 Created.

    Raises
    ------
    HTTPException
        * 400 ``invalid_label`` — label empty or > 120 chars.
        * 400 ``invalid_tolerance`` — tolerance outside ``[0.1, 5.0]``.
        * 400 ``invalid_json`` — body not parseable as JSON.
        * 400 ``not_a_probe_run`` — source run mode != ``topic_probe``.
        * 404 ``run_not_found`` — no row matches ``run_id``.
        * 409 ``run_not_completed`` — source run still ``running``.
        * 409 ``run_missing_aggregate`` — source ``aggregate`` is NULL or
          missing the required ``mean_overall`` key.
        * 429 — rate limit (20/min) exceeded.
    """
    body = await _parse_body(request, SaveSuiteRequest, route="save_as_suite")
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

    Spec §4 line 273 — default filters out retired suites.

    Parameters
    ----------
    project_id:
        Optional narrowing to a single project. ``None`` returns suites
        across all projects.
    repo_full_name:
        Accepted for spec parity but currently a passthrough (the service
        primitive accepts only ``project_id`` as a narrowing filter in
        Cycle 5; if a future cycle adds repo-level filtering it lands in
        the service surface without breaking this endpoint).
    include_retired:
        When ``False`` (default) retired suites are filtered out — matches
        the spec §4 default-filter contract. ``True`` returns all suites.
    limit:
        Page size, clamped ``[1, 100]``.
    offset:
        Zero-indexed page offset.

    Returns
    -------
    ValidationSuiteListResponse
        Canonical pagination envelope
        ``{total, count, offset, items, has_more, next_offset}``. ``items``
        are ``ValidationSuiteListItem`` (excludes heavy
        ``prompts_snapshot`` / ``baseline_scores`` JSON columns).
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
    """Full :class:`ValidationSuiteOut` read view of a single suite.

    Spec §4 line 274.

    Parameters
    ----------
    suite_id:
        Target ``ValidationSuite.id``.

    Returns
    -------
    ValidationSuiteOut
        Full suite read (includes ``prompts_snapshot`` + ``baseline_scores``
        JSON columns omitted from the list-view item type), status 200 OK.

    Raises
    ------
    HTTPException
        * 404 ``suite_not_found`` — no row matches ``suite_id``.
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
    ``mode='replay_run' AND suite_id={suite_id}``.

    Parameters
    ----------
    suite_id:
        Target ``ValidationSuite.id`` whose replay runs to list. Unknown
        suite_ids yield an empty envelope (REST convention for
        resource-scoped collections — never 404 on the collection itself,
        only on the resource via :func:`get_suite`).
    limit:
        Page size, clamped ``[1, 100]``.
    offset:
        Zero-indexed page offset.

    Returns
    -------
    RunListResponse
        Canonical pagination envelope. ``items`` are ``RunSummary`` rows
        ordered by ``started_at desc``.
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
    """Kick off a replay run against a suite.

    Spec §4 line 276 — 202 Accepted, 5/min rate limit. ``Location`` header
    mirrors ``poll_url`` (RFC 7240); ``Retry-After`` is a polling hint
    (integer seconds).

    Spec §10 Cycles 5 + 6 + 8 combined: dispatches the
    :class:`ReplayRunGenerator` via
    ``app.state.run_orchestrator.dispatch_async(mode='replay_run', ...)``.
    The orchestrator awaits the initial
    ``RunRow(mode='replay_run', status='running', suite_id=...)`` INSERT
    BEFORE returning, so the caller's immediate ``GET /api/runs/{run_id}``
    is race-safe. The spawned ``_run_to_completion`` task drives the
    generator to a terminal status under ``asyncio.shield`` — closing the
    202 HTTP connection cannot cancel the run.

    Precondition checks fire BEFORE dispatch — no RunRow is written when
    the suite is missing (404) or retired (409).

    Parameters
    ----------
    suite_id:
        Target ``ValidationSuite.id`` to replay.
    request:
        FastAPI request — used to reach the per-process ``WriteQueue``
        singleton via ``request.app.state.write_queue``.
    response:
        FastAPI response — mutated in-place to set ``Location`` and
        ``Retry-After`` headers.

    Returns
    -------
    ReplayRunOut
        Body shape per spec §4 lines 426-442: ``run_id``, ``suite_id``,
        ``mode='replay_run'``, ``status='running'``, ``started_at``,
        ``poll_url``. Status 202 Accepted.

    Raises
    ------
    HTTPException
        * 404 ``suite_not_found`` — no row matches ``suite_id``.
        * 409 ``suite_retired`` — suite exists but ``retired_at`` is set.
        * 429 — rate limit (5/min) exceeded.
        * 503 ``run_orchestrator_unavailable`` — defensive guard surfaces
          only if lifespan failed to register the orchestrator (matches the
          ``routers/probes.py`` precedent).
    """
    # ---- Precondition: suite must exist + not be retired ----
    try:
        suite = await _service.get(suite_id)
    except ValueError as exc:
        raise _map_service_error(exc) from exc

    if suite.retired_at is not None:
        raise HTTPException(status_code=409, detail="suite_retired")

    # ---- Resolve orchestrator (defensive — matches probes.py precedent) ----
    orchestrator = getattr(request.app.state, "run_orchestrator", None)
    if orchestrator is None:
        # Surfaces only if lifespan failed to register the orchestrator
        # (e.g., WriteQueue init failed). Symmetric with the probes router
        # so observability dashboards can switch on a single reason code.
        raise HTTPException(
            status_code=503, detail="run_orchestrator_unavailable",
        )

    # ---- Mint replay run_id + dispatch ReplayRunGenerator ----
    run_id = uuid.uuid4().hex
    # Naive UTC for SQLite DateTime column compatibility (matches RunRow
    # column convention — see ``_seed_completed_probe_run`` test helper and
    # ``services/run_orchestrator.py`` _create_row pattern). The
    # orchestrator's ``_create_row`` will use its own ``_utcnow()`` for the
    # actual row write; the response timestamp is the caller-facing
    # contract (microseconds-earlier — within the SQLite resolution
    # tolerance accepted across the codebase).
    started_at = datetime.now(UTC).replace(tzinfo=None)

    # ``RunRequest.payload`` carries the keys ``_create_row`` reads:
    #   * ``suite_id``      — pinned to ``RunRow.suite_id`` (regression-alarm JOIN)
    #   * ``project_id``    — denormalized project provenance from suite
    #   * ``repo_full_name``— denormalized repo provenance from suite
    # ``ReplayRunGenerator.run()`` reads ``suite_id`` + (optional)
    # ``repo_full_name`` / ``project_id`` from the same payload, so a single
    # source of truth flows through both the row INSERT and the generator
    # body.
    run_request = RunRequest(
        mode="replay_run",
        payload={
            "suite_id": suite_id,
            "project_id": suite.project_id,
            "repo_full_name": suite.repo_full_name,
        },
    )

    # ``dispatch_async`` awaits the initial RunRow INSERT before returning,
    # so the caller's immediate ``GET /api/runs/{run_id}`` is race-safe (spec
    # §8 race-safety + §10 Cycle 8 OPERATE O1). The spawned background task
    # runs under ``asyncio.shield`` — closing the 202 HTTP connection cannot
    # cancel the replay.
    await orchestrator.dispatch_async(
        mode="replay_run",
        request=run_request,
        run_id=run_id,
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

    Three observability invariants hold on the no-op (re-retire) path per
    the service contract: NO DB write, NO event publish, NO JSONL trace
    entry.

    Parameters
    ----------
    suite_id:
        Target ``ValidationSuite.id``.
    request:
        FastAPI request — used to (1) parse the JSON body manually so
        Pydantic errors land in the spec §4 400-with-code envelope, and
        (2) reach the per-process ``WriteQueue`` singleton.

    Returns
    -------
    ValidationSuiteOut
        Updated suite read with ``retired_at`` / ``retired_reason`` set,
        status 200 OK.

    Raises
    ------
    HTTPException
        * 400 ``invalid_reason`` — reason empty or > 500 chars.
        * 400 ``invalid_json`` — body not parseable as JSON.
        * 404 ``suite_not_found`` — no row matches ``suite_id``.
        * 429 — rate limit (30/min) exceeded.
    """
    body = await _parse_body(request, RetireSuiteRequest, route="retire")
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
