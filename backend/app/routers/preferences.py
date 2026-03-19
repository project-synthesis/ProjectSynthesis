"""Preferences REST API — GET/PATCH for persistent user settings."""

from fastapi import APIRouter, HTTPException

from app.config import DATA_DIR
from app.services.preferences import PreferencesService

router = APIRouter(prefix="/api", tags=["preferences"])

_svc = PreferencesService(DATA_DIR)


@router.get("/preferences")
async def get_preferences() -> dict:
    """Return full preferences (merged with defaults)."""
    return _svc.load()


@router.patch("/preferences")
async def patch_preferences(body: dict) -> dict:
    """Deep-merge updates into preferences. Validates before saving."""
    try:
        return _svc.patch(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
