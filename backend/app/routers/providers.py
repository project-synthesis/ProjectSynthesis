"""Provider info endpoint."""

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["providers"])


@router.get("/providers")
async def get_providers(request: Request):
    provider = getattr(request.app.state, "provider", None)
    return {
        "active_provider": provider.name if provider else None,
        "available": ["claude_cli", "anthropic_api", "mcp_passthrough"],
    }
