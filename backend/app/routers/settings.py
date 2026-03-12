"""Application settings endpoints.

Provides REST endpoints for reading and updating application-level
settings such as default model, pipeline timeout, and max retries.
Settings are stored in a JSON file to persist across restarts.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.dependencies.auth import get_current_user
from app.schemas.auth import AuthenticatedUser
from app.services.settings_service import load_settings, save_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["settings"])

KNOWN_STRATEGIES = frozenset({
    "chain-of-thought",
    "constraint-injection",
    "context-enrichment",
    "CO-STAR",
    "few-shot-scaffolding",
    "persona-assignment",
    "RISEN",
    "role-task-format",
    "step-by-step",
    "structured-output",
})


class SettingsUpdate(BaseModel):
    """Schema for partial settings updates."""

    default_model: Optional[str] = Field(
        None,
        description='Model selection mode: "auto" or a specific model ID',
    )
    pipeline_timeout: Optional[int] = Field(
        None,
        ge=10,
        le=600,
        description="Pipeline timeout in seconds (10-600)",
    )
    max_retries: Optional[int] = Field(
        None,
        ge=0,
        le=5,
        description="Maximum retry attempts for failed stages (0-5)",
    )
    default_strategy: Optional[str] = Field(
        None,
        description="Default optimization strategy framework, or null for auto",
    )
    auto_validate: Optional[bool] = Field(
        None,
        description="Whether to run the validation stage automatically",
    )
    stream_optimize: Optional[bool] = Field(
        None,
        description="Whether to stream the optimize stage output",
    )

    @field_validator("default_strategy")
    @classmethod
    def validate_strategy(cls, v: str | None) -> str | None:
        if v is not None and v not in KNOWN_STRATEGIES:
            raise ValueError(
                f"Unknown strategy '{v}'. Must be one of: {', '.join(sorted(KNOWN_STRATEGIES))}"
            )
        return v


@router.get("/api/settings")
async def get_settings(
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Get current application settings.

    Returns all settings with their current values, including defaults
    for any settings that have not been explicitly configured.

    Returns:
        Dict of all setting key-value pairs.
    """
    return load_settings()


@router.patch("/api/settings")
async def update_settings(
    update: SettingsUpdate,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Update application settings.

    Only the fields included in the request body are updated.
    Other settings remain unchanged.

    Args:
        update: Partial settings update with only the fields to change.

    Returns:
        Dict of all settings after the update.
    """
    current = load_settings()

    # exclude_unset (not exclude_none) so explicit null clears nullable fields
    # like default_strategy, while omitted fields remain unchanged.
    update_data = update.model_dump(exclude_unset=True)
    if not update_data:
        return current

    current.update(update_data)
    try:
        save_settings(current)
    except OSError as e:
        logger.error("Failed to save settings: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to save settings: {e}")

    logger.info("Settings updated: %s", list(update_data.keys()))
    return current
