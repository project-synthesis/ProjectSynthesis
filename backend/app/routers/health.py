"""Health check endpoint."""

from fastapi import APIRouter, Request
from app._version import __version__

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health_check(request: Request) -> dict:
    """Liveness check with provider and version info."""
    provider = getattr(request.app.state, "provider", None)
    return {
        "status": "healthy" if provider else "degraded",
        "version": __version__,
        "provider": provider.name if provider else None,
    }
