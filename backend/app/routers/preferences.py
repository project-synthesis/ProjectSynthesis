"""Preferences REST API — GET/PATCH for persistent user settings."""

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictBool

from app.config import DATA_DIR
from app.services.event_bus import event_bus
from app.services.preferences import PreferencesService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["preferences"])

_svc = PreferencesService(DATA_DIR)

# Pipeline keys whose mutation changes ``RoutingManager.available_tiers``
# resolution. When a PATCH touches ANY of these, we additionally broadcast
# ``routing_state_changed`` so frontend / cross-process subscribers see the
# updated tier surface — pre-2026-05-09 only ``preferences_changed`` fired
# and the routing-tier path silently desynced for non-toggling clients.
_ROUTING_RELEVANT_PIPELINE_KEYS = frozenset({"force_sampling", "force_passthrough"})


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
    enable_strategy_intelligence: StrictBool | None = None
    enable_llm_classification_fallback: StrictBool | None = None
    force_sampling: StrictBool | None = None
    force_passthrough: StrictBool | None = None
    optimizer_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    analyzer_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    scorer_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None


class _DefaultsUpdate(BaseModel):
    """Validated default settings."""
    model_config = ConfigDict(extra="forbid")

    strategy: str | None = None


class _DomainReadinessNotificationsUpdate(BaseModel):
    """Validated domain-readiness notification toggles.

    `enabled` is the master SSE→toast gate. `muted_domain_ids` is the
    per-row bell list in `DomainReadinessPanel`. Both optional so the
    frontend can PATCH either axis independently.
    """
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool | None = None
    muted_domain_ids: list[str] | None = None


class PreferencesUpdate(BaseModel):
    """Strict schema for PATCH /api/preferences. Unknown keys are rejected."""
    model_config = ConfigDict(extra="forbid")

    models: _ModelsUpdate | None = None
    pipeline: _PipelineUpdate | None = None
    defaults: _DefaultsUpdate | None = None
    domain_readiness_notifications: _DomainReadinessNotificationsUpdate | None = None


@router.get("/preferences")
async def get_preferences() -> dict:
    """Return full preferences (merged with defaults)."""
    return _svc.load()


@router.patch("/preferences")
async def patch_preferences(body: PreferencesUpdate, request: Request) -> dict:
    """Deep-merge updates into preferences. Validates before saving.

    Side-emits ``routing_state_changed`` when the patch touches a
    routing-relevant pipeline key (``force_sampling`` / ``force_passthrough``)
    so subscribers that drive tier-aware UI react to multi-client / CLI
    patches the same way they react to backend-driven state changes.
    """
    patch_dict = body.model_dump(exclude_none=True)
    try:
        result = _svc.patch(patch_dict)
        event_bus.publish("preferences_changed", result)
        # Routing-relevant key gate (2026-05-09): the backend's
        # ``available_tiers`` resolution depends on these toggles, so a
        # patch that flips them must propagate to the routing channel.
        pipeline_patch = patch_dict.get("pipeline") or {}
        if any(k in pipeline_patch for k in _ROUTING_RELEVANT_PIPELINE_KEYS):
            routing = getattr(request.app.state, "routing", None)
            if routing is not None:
                try:
                    routing.broadcast_external(trigger="preferences_changed")
                except Exception:
                    logger.debug(
                        "broadcast_external failed (non-fatal)",
                        exc_info=True,
                    )
        return result
    except (ValueError, TypeError) as exc:
        logger.warning("Preferences patch rejected: %s", exc)
        raise HTTPException(status_code=422, detail="Invalid preference value.") from exc
