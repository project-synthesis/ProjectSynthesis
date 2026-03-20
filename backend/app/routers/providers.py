"""Provider info and API key management endpoints."""

import base64
import hashlib
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import DATA_DIR, settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["providers"])


class ProviderInfo(BaseModel):
    active_provider: str | None = Field(description="Name of the active LLM provider, or null if none detected.")
    available: list[str] = Field(description="List of supported provider identifiers.")


class ApiKeyStatus(BaseModel):
    configured: bool = Field(description="Whether an API key is currently configured.")
    masked_key: str | None = Field(
        default=None,
        description="Masked API key showing last 4 characters (e.g. 'sk-...abcd').",
    )


@router.get("/providers")
async def get_providers(request: Request) -> ProviderInfo:
    routing = getattr(request.app.state, "routing", None)
    provider_name = routing.state.provider_name if routing else None
    return ProviderInfo(
        active_provider=provider_name,
        available=["claude_cli", "anthropic_api", "mcp_passthrough"],
    )


@router.get("/provider/api-key")
async def get_api_key() -> ApiKeyStatus:
    """Return masked API key (last 4 chars only)."""
    key = _read_api_key()
    if not key:
        return ApiKeyStatus(configured=False, masked_key=None)
    masked = f"sk-...{key[-4:]}" if len(key) > 4 else "****"
    return ApiKeyStatus(configured=True, masked_key=masked)


class ApiKeyRequest(BaseModel):
    api_key: str = Field(description="Anthropic API key (must start with 'sk-').")


@router.patch("/provider/api-key")
async def set_api_key(body: ApiKeyRequest, request: Request) -> ApiKeyStatus:
    """Set or update the Anthropic API key. Persists encrypted to disk."""
    key = body.api_key.strip()
    if not key.startswith("sk-"):
        raise HTTPException(
            400,
            "Invalid API key format. Anthropic API keys start with 'sk-'.",
        )

    _write_api_key(key)
    logger.info("API key updated (last 4: ...%s)", key[-4:])

    # Hot-reload: create provider and update routing
    try:
        from app.providers.anthropic_api import AnthropicAPIProvider
        new_provider = AnthropicAPIProvider(api_key=key)
        routing = getattr(request.app.state, "routing", None)
        if routing:
            routing.set_provider(new_provider)
        # No need to set app.state.provider — routing manager owns the provider
        logger.info("Provider hot-reloaded: anthropic_api")
    except Exception:
        logger.warning("Could not hot-reload provider after API key set", exc_info=True)

    return ApiKeyStatus(configured=True, masked_key=f"sk-...{key[-4:]}")


@router.delete("/provider/api-key")
async def delete_api_key(request: Request) -> ApiKeyStatus:
    """Remove the stored API key."""
    cred_file = DATA_DIR / ".api_credentials"
    if cred_file.exists():
        cred_file.unlink()
        logger.info("API key credentials file deleted")

    # Clear provider from routing service
    routing = getattr(request.app.state, "routing", None)
    if routing:
        routing.set_provider(None)
    else:
        logger.warning("API key deleted but routing service not available — provider state may be stale")
    return ApiKeyStatus(configured=False, masked_key=None)


def _read_api_key() -> str | None:
    """Read API key: check env var first, then encrypted file."""
    if settings.ANTHROPIC_API_KEY:
        return settings.ANTHROPIC_API_KEY
    cred_file = DATA_DIR / ".api_credentials"
    if not cred_file.exists():
        return None
    try:
        from cryptography.fernet import Fernet

        secret = settings.resolve_secret_key()
        fernet_key = base64.urlsafe_b64encode(
            hashlib.sha256(secret.encode()).digest()
        )
        f = Fernet(fernet_key)
        return f.decrypt(cred_file.read_bytes()).decode()
    except Exception:
        logger.warning("Failed to decrypt API credentials")
        return None


def _write_api_key(key: str) -> None:
    """Encrypt and persist API key to disk."""
    from cryptography.fernet import Fernet

    secret = settings.resolve_secret_key()
    fernet_key = base64.urlsafe_b64encode(
        hashlib.sha256(secret.encode()).digest()
    )
    f = Fernet(fernet_key)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cred_file = DATA_DIR / ".api_credentials"
    cred_file.write_bytes(f.encrypt(key.encode()))
    cred_file.chmod(0o600)
