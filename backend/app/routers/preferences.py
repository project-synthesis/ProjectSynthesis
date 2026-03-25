"""Preferences REST API — GET/PATCH for persistent user settings."""

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool

from app.config import DATA_DIR
from app.services.event_bus import event_bus
from app.services.preferences import PreferencesService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["preferences"])

_svc = PreferencesService(DATA_DIR)


class _ModelsUpdate(BaseModel):
    """Validated model selection."""
    model_config = ConfigDict(extra="forbid")

    analyzer: str | None = None
    optimizer: str | None = None
    scorer: str | None = None


class _PipelineUpdate(BaseModel):
    """Validated pipeline toggles and effort levels."""
    model_config = ConfigDict(extra="forbid")

    enable_explore: StrictBool | None = None
    enable_scoring: StrictBool | None = None
    enable_adaptation: StrictBool | None = None
    force_sampling: StrictBool | None = None
    force_passthrough: StrictBool | None = None
    optimizer_effort: Literal["low", "medium", "high", "max"] | None = None
    analyzer_effort: Literal["low", "medium", "high", "max"] | None = None
    scorer_effort: Literal["low", "medium", "high", "max"] | None = None


class _DefaultsUpdate(BaseModel):
    """Validated default settings."""
    model_config = ConfigDict(extra="forbid")

    strategy: str | None = None


class PreferencesUpdate(BaseModel):
    """Strict schema for PATCH /api/preferences. Unknown keys are rejected."""
    model_config = ConfigDict(extra="forbid")

    models: _ModelsUpdate | None = None
    pipeline: _PipelineUpdate | None = None
    defaults: _DefaultsUpdate | None = None


@router.get("/preferences")
async def get_preferences() -> dict:
    """Return full preferences (merged with defaults)."""
    return _svc.load()


@router.patch("/preferences")
async def patch_preferences(body: PreferencesUpdate) -> dict:
    """Deep-merge updates into preferences. Validates before saving."""
    try:
        result = _svc.patch(body.model_dump(exclude_none=True))
        event_bus.publish("preferences_changed", result)
        return result
    except (ValueError, TypeError) as exc:
        logger.warning("Preferences patch rejected: %s", exc)
        raise HTTPException(status_code=422, detail="Invalid preference value.") from exc
