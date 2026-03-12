import asyncio
import logging

from fastapi import APIRouter, Request

from app._version import __version__
from app.config import settings
from app.database import check_db_connection
from app.providers.base import MODEL_ROUTING
from app.services.api_credentials_service import get_credential_load_error

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

_MCP_PROBE_TIMEOUT = 2.0  # seconds


async def _probe_mcp() -> bool:
    """Return True if the standalone MCP server is accepting TCP connections.

    Uses a lightweight TCP connect rather than a full MCP initialize handshake
    to avoid creating sessions on every 15-second health poll.
    """
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(
                settings.MCP_PROBE_HOST or settings.MCP_HOST,
                settings.MCP_PORT,
            ),
            timeout=_MCP_PROBE_TIMEOUT,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def _probe_redis(request: Request) -> bool:
    """Return True if Redis responds to PING within the socket timeout."""
    redis_svc = getattr(request.app.state, "redis", None)
    if not redis_svc:
        return False
    return await redis_svc.health_check()


@router.get("/api/health")
async def health_check(request: Request):
    """Health check endpoint returning system status."""
    db_ok, mcp_ok, redis_ok = await asyncio.gather(
        check_db_connection(),
        _probe_mcp(),
        _probe_redis(request),
    )

    github_oauth_enabled = bool(
        settings.GITHUB_APP_CLIENT_ID and settings.GITHUB_APP_CLIENT_SECRET
    )

    _prov = getattr(request.app.state, "provider", None)
    provider_name = _prov.name if _prov else "none"

    # degraded if DB is down; also degraded (not error) if Redis is down — app still works
    if not db_ok:
        overall = "degraded"
    elif not redis_ok:
        overall = "degraded"
    else:
        overall = "ok"

    mcp_url = f"http://{settings.MCP_HOST}:{settings.MCP_PORT}/mcp"
    credential_error = get_credential_load_error()
    resp = {
        "status": overall,
        "provider": provider_name,
        "model_routing": MODEL_ROUTING,
        "github_oauth_enabled": github_oauth_enabled,
        "db_connected": db_ok,
        "mcp_connected": mcp_ok,
        "redis_connected": redis_ok,
        "mcp_url": mcp_url,
        "version": __version__,
    }
    if credential_error:
        resp["credential_error"] = credential_error
    return resp
