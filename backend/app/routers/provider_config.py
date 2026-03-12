"""LLM provider API key management endpoints.

GET    /api/provider/config   — always public; returns provider status + masked key.
PATCH  /api/provider/api-key  — bootstrap mode (unauthenticated when no provider
                                AND no GitHub OAuth configured); JWT-required otherwise.
DELETE /api/provider/api-key  — JWT-required; removes saved key.
"""

from __future__ import annotations

import logging
import os
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.dependencies.auth import get_current_user
from app.dependencies.rate_limit import RateLimit
from app.providers.detector import ProviderNotAvailableError, detect_provider
from app.services.api_credentials_service import (
    delete_api_key,
    get_api_key_status,
    save_api_key,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["provider-config"])


class SaveApiKeyRequest(BaseModel):
    api_key: str

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("api_key must not be empty")
        # Basic format check — Anthropic keys start with sk-ant-
        if not re.match(r"^sk-ant-", v):
            raise ValueError(
                "Invalid API key format. Anthropic API keys "
                "start with 'sk-ant-'"
            )
        if len(v) < 20:
            raise ValueError("API key is too short")
        return v


# ── Helpers ───────────────────────────────────────────────────────────────


def _is_bootstrap_mode(request: Request) -> bool:
    """Check if the app is in bootstrap mode (no provider AND no GitHub OAuth).

    In bootstrap mode, the PATCH endpoint allows unauthenticated access so
    users can configure their first API key without needing to log in first.
    """
    provider = getattr(request.app.state, "provider", None)
    github_configured = bool(
        settings.GITHUB_APP_CLIENT_ID and settings.GITHUB_APP_CLIENT_SECRET
    )
    return provider is None and not github_configured


def _provider_status_response(request: Request, **extra: object) -> dict:
    """Build a standardised provider status dict from current app state."""
    _prov = getattr(request.app.state, "provider", None)
    return {
        **extra,
        "provider_active": _prov.name if _prov else "none",
        "provider_available": _prov is not None,
        "api_key": get_api_key_status(),
    }


def _check_env_var_conflict() -> None:
    """Raise 409 if the API key is pinned via environment variable."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=409,
            detail=(
                "API key is configured via environment variable and "
                "cannot be changed from the UI. Update the "
                "ANTHROPIC_API_KEY env var instead."
            ),
        )


async def _reload_provider(request: Request) -> None:
    """Re-run provider detection and update ``app.state.provider``."""
    try:
        new_provider = await detect_provider()
        request.app.state.provider = new_provider
        logger.info("Provider reloaded: %s", new_provider.name)
    except ProviderNotAvailableError:
        request.app.state.provider = None
        logger.info("No provider available after reload")
    except Exception:
        request.app.state.provider = None
        logger.exception("Provider reload failed unexpectedly")


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.get("/api/provider/config")
async def get_provider_config(
    request: Request,
    _rl: None = Depends(
        RateLimit(lambda: settings.RATE_LIMIT_PROVIDER_READ)
    ),
) -> dict:
    """Return provider status and masked API key info.

    Always public — used by the UI to determine setup state on load.
    """
    return {
        **_provider_status_response(request),
        "bootstrap_mode": _is_bootstrap_mode(request),
    }


@router.patch("/api/provider/api-key")
async def save_provider_api_key(
    body: SaveApiKeyRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _rl: None = Depends(
        RateLimit(lambda: settings.RATE_LIMIT_PROVIDER_WRITE)
    ),
) -> dict:
    """Save an Anthropic API key, encrypt and persist, then hot-reload the provider.

    Bootstrap mode (no provider AND no GitHub OAuth): unauthenticated access.
    Normal mode: JWT required.
    """
    if not _is_bootstrap_mode(request):
        await get_current_user(request, session)

    _check_env_var_conflict()

    save_api_key(body.api_key)
    await _reload_provider(request)

    provider = getattr(request.app.state, "provider", None)

    # B2: Validate key after save — warn if invalid but don't block
    validation_warning: str | None = None
    if provider is not None and hasattr(provider, "validate_key"):
        try:
            valid, msg = await provider.validate_key()
            if not valid:
                validation_warning = msg
        except Exception:
            validation_warning = "Key saved but validation check failed"

    # B3: ok reflects whether a provider is now active
    resp = _provider_status_response(request, ok=(provider is not None))
    if validation_warning:
        resp["validation_warning"] = validation_warning
    return resp


@router.delete("/api/provider/api-key")
async def delete_provider_api_key(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_user),
    _rl: None = Depends(
        RateLimit(lambda: settings.RATE_LIMIT_PROVIDER_WRITE)
    ),
) -> dict:
    """Remove the saved API key. JWT required.

    If the active provider was using the saved key, sets provider to None.
    Does not affect env-var-configured keys.
    """
    _check_env_var_conflict()

    delete_api_key()
    await _reload_provider(request)

    return _provider_status_response(request, ok=True)
