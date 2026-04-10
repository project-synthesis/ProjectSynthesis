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


class ApplyResponse(BaseModel):
    status: str
    tag: str
    message: str = ""


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


@router.post("/apply", response_model=ApplyResponse, status_code=202)
async def apply_update(
    request: Request,
    body: ApplyRequest,
    _rate: None = Depends(RateLimit(lambda: "1/minute")),
) -> ApplyResponse:
    """Trigger Phase 1 of the update: checkout, deps, alembic, restart."""
    svc = getattr(request.app.state, "update_service", None)
    if not svc:
        raise HTTPException(500, "Update service not initialized")

    try:
        result = await svc.apply_update(body.tag)
        return ApplyResponse(
            status=result["status"],
            tag=result["tag"],
            message="Update applied. Services restarting...",
        )
    except RuntimeError as exc:
        if "already in progress" in str(exc).lower():
            raise HTTPException(409, str(exc))
        raise HTTPException(500, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
