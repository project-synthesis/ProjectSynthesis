"""Update status and apply endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.dependencies.rate_limit import RateLimit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/update", tags=["update"])


class UpdateStatusResponse(BaseModel):
    current_version: str
    latest_version: str | None = None
    latest_tag: str | None = None
    update_available: bool = False
    changelog: str | None = None
    changelog_entries: list[dict[str, str]] | None = None
    checked_at: str | None = None
    detection_tier: str = "none"


class ApplyRequest(BaseModel):
    tag: str = Field(description="Git tag to update to, e.g. 'v0.4.0'")
    force: bool = Field(
        default=False,
        description=(
            "Bypass non-blocking pre-flight warnings (e.g., commits ahead of "
            "origin, in-flight optimizations remaining after drain). Blocking "
            "issues — non-prompt uncommitted changes, invalid tag — are "
            "always enforced, even with force=True."
        ),
    )


class ApplyResponse(BaseModel):
    status: str
    tag: str
    message: str = ""
    stash_pop_conflicts: list[str] = Field(default_factory=list)


class PreflightDirtyFile(BaseModel):
    path: str
    status: str
    source: str
    in_prompts_tree: bool


class PreflightResponse(BaseModel):
    can_apply: bool
    blocking_issues: list[str]
    warnings: list[str]
    dirty_files: list[PreflightDirtyFile]
    user_customizations: list[str]
    commits_ahead_of_origin: int
    commits_behind_origin: int
    on_detached_head: bool
    in_flight_optimizations: int
    in_flight_trace_ids: list[str]
    will_auto_stash: bool
    target_tag: str | None
    target_tag_exists_locally: bool


@router.get("/status", response_model=UpdateStatusResponse)
async def get_update_status(
    request: Request,
    _rate: None = Depends(RateLimit(lambda: settings.DEFAULT_RATE_LIMIT)),
) -> UpdateStatusResponse:
    """Return cached update status from last startup check."""
    svc = getattr(request.app.state, "update_service", None)
    if not svc or not svc.status:
        return UpdateStatusResponse(current_version="unknown")

    s = svc.status
    return UpdateStatusResponse(
        current_version=s.current_version,
        latest_version=s.latest_version,
        latest_tag=s.latest_tag,
        update_available=s.update_available,
        changelog=s.changelog,
        changelog_entries=s.changelog_entries,
        checked_at=s.checked_at.isoformat() if s.checked_at else None,
        detection_tier=s.detection_tier,
    )


@router.get("/preflight", response_model=PreflightResponse)
async def preflight_update(
    request: Request,
    tag: str | None = None,
    _rate: None = Depends(RateLimit(lambda: settings.DEFAULT_RATE_LIMIT)),
) -> PreflightResponse:
    """Comprehensive pre-update readiness probe.

    Frontend calls this BEFORE enabling the "Update & Restart" button so
    the user can see dirty files, in-flight optimizations, branch
    divergence, and customization counts up front. ``tag`` defaults to
    the cached ``latest_tag`` from ``GET /api/update/status``.
    """
    svc = getattr(request.app.state, "update_service", None)
    if not svc:
        raise HTTPException(500, "Update service not initialized")

    status = await svc.preflight(tag=tag)
    return PreflightResponse(
        can_apply=status.can_apply,
        blocking_issues=list(status.blocking_issues),
        warnings=list(status.warnings),
        dirty_files=[
            PreflightDirtyFile(
                path=d.path, status=d.status, source=d.source,
                in_prompts_tree=d.in_prompts_tree,
            )
            for d in status.dirty_files
        ],
        user_customizations=list(status.user_customizations),
        commits_ahead_of_origin=status.commits_ahead_of_origin,
        commits_behind_origin=status.commits_behind_origin,
        on_detached_head=status.on_detached_head,
        in_flight_optimizations=status.in_flight_optimizations,
        in_flight_trace_ids=list(status.in_flight_trace_ids),
        will_auto_stash=status.will_auto_stash,
        target_tag=status.target_tag,
        target_tag_exists_locally=status.target_tag_exists_locally,
    )


@router.post("/apply", response_model=ApplyResponse, status_code=202)
async def apply_update(
    request: Request,
    body: ApplyRequest,
    _rate: None = Depends(RateLimit(lambda: "1/minute")),
) -> ApplyResponse:
    """Trigger Phase 1 of the update: pre-flight, drain, stash, checkout,
    deps, alembic, stash-pop, restart.

    Set ``force=True`` to bypass non-blocking warnings (commits ahead,
    in-flight optimizations remaining after drain). Blocking issues —
    non-prompt uncommitted changes, invalid tag — are always enforced.
    """
    svc = getattr(request.app.state, "update_service", None)
    if not svc:
        raise HTTPException(500, "Update service not initialized")

    try:
        result = await svc.apply_update(body.tag, force=body.force)
        return ApplyResponse(
            status=result["status"],
            tag=result["tag"],
            message="Update applied. Services restarting...",
            stash_pop_conflicts=list(result.get("stash_pop_conflicts") or []),
        )
    except RuntimeError as exc:
        if "already in progress" in str(exc).lower():
            raise HTTPException(409, str(exc))
        raise HTTPException(500, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
