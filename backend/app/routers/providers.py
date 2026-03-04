"""Provider detection and health check endpoints.

Returns information about which LLM providers are available
(claude_cli, anthropic_api) and their current status.
"""

import asyncio
import logging
import shutil

from fastapi import APIRouter

from app.config import settings
from app.providers.base import MODEL_ROUTING

logger = logging.getLogger(__name__)
router = APIRouter(tags=["providers"])

# Set by main.py lifespan handler
_provider = None


def set_provider(provider):
    """Inject the detected LLM provider at startup."""
    global _provider
    _provider = provider


@router.get("/api/providers/detect")
async def detect_providers():
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
    active_provider = _provider.name if _provider else "none"

    return {
        "providers": providers,
        "active": active_provider,
        "model_routing": MODEL_ROUTING,
    }


@router.get("/api/providers/status")
async def provider_status():
    """Provider health check.

    Returns the current provider status, model routing configuration,
    and a quick connectivity test result.

    Returns:
        Dict with provider health information.
    """
    if not _provider:
        return {
            "status": "unavailable",
            "provider": None,
            "model_routing": MODEL_ROUTING,
            "healthy": False,
            "message": "No LLM provider has been initialized.",
        }

    # Quick health check: attempt a minimal completion
    healthy = True
    message = "Provider is operational."
    try:
        # Use the cheapest model for the health check
        response = await _provider.complete(
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
        "provider": _provider.name,
        "model_routing": MODEL_ROUTING,
        "healthy": healthy,
        "message": message,
    }
