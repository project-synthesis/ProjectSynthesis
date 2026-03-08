"""Provider detection and health check endpoints.

Returns information about which LLM providers are available
(claude_cli, anthropic_api) and their current status.
"""

import asyncio
import logging
import shutil

from fastapi import APIRouter, Request

from app.config import settings
from app.providers.base import MODEL_ROUTING

logger = logging.getLogger(__name__)
router = APIRouter(tags=["providers"])

# Deprecated: use request.app.state.provider. Kept for backward compat with main.py lifespan.
_provider = None


def set_provider(provider):
    """Deprecated: provider is now read from app.state. Kept for main.py compat."""
    pass  # no-op — provider injected via app.state at startup


@router.get("/api/providers/detect")
async def detect_providers(request: Request):
    """Detect available LLM providers.

    Checks for the Claude CLI on PATH and the ANTHROPIC_API_KEY
    environment variable. Returns which providers are available
    and which one is currently active.

    Returns:
        Dict with detected providers and active provider info.
    """
    providers = {}

    # Check Claude CLI
    claude_cli_available = False
    try:
        claude_path = shutil.which("claude")
        if claude_path:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    "claude", "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=5.0,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                claude_cli_available = True
                providers["claude_cli"] = {
                    "available": True,
                    "path": claude_path,
                    "version": stdout.decode().strip(),
                }
    except (asyncio.TimeoutError, FileNotFoundError, OSError) as e:
        logger.debug("Claude CLI not available: %s", e)

    if not claude_cli_available:
        providers["claude_cli"] = {
            "available": False,
            "path": None,
            "version": None,
        }

    # Check Anthropic API
    api_key_present = bool(settings.ANTHROPIC_API_KEY)
    providers["anthropic_api"] = {
        "available": api_key_present,
        "key_configured": api_key_present,
    }

    # Current active provider
    _prov = getattr(request.app.state, "provider", None)
    active_provider = _prov.name if _prov else "none"

    return {
        "providers": providers,
        "active": active_provider,
    }


@router.get("/api/providers/status")
async def provider_status(request: Request):
    """Provider health check.

    Returns the current provider status, model routing configuration,
    and a quick connectivity test result.

    Returns:
        Dict with provider health information.
    """
    _prov = getattr(request.app.state, "provider", None)
    if not _prov:
        return {
            "status": "unavailable",
            "provider": None,
            "healthy": False,
            "message": "No LLM provider has been initialized.",
        }

    # Quick health check: attempt a minimal completion
    healthy = True
    message = "Provider is operational."
    try:
        # Use the cheapest model for the health check
        response = await _prov.complete(
            system="Respond with exactly: OK",
            user="Health check",
            model=MODEL_ROUTING["analyze"],
        )
        if not response:
            healthy = False
            message = "Provider returned an empty response."
    except Exception as e:
        healthy = False
        message = f"Provider health check failed: {e}"
        logger.warning("Provider health check failed: %s", e)

    return {
        "status": "healthy" if healthy else "degraded",
        "provider": _prov.name,
        "healthy": healthy,
        "message": message,
    }
