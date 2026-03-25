"""Provider info and API key management endpoints."""

import logging
import shutil
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import DATA_DIR, settings
from app.utils.crypto import decrypt_with_migration, derive_fernet

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["providers"])

# --- Cached lookups (avoid per-request overhead) ---

# CLI presence rarely changes during a process lifetime; check once at import.
_CLAUDE_CLI_AVAILABLE: bool = shutil.which("claude") is not None

# Lightweight API key presence check with short TTL to avoid per-request
# Fernet decryption while still reacting to key set/delete within seconds.
# Stores a boolean (not the key itself) to minimize plaintext exposure.
_API_KEY_CACHE_TTL = 5.0  # seconds
_api_key_cache: tuple[float, bool] = (0.0, False)


def _has_api_key() -> bool:
    """Check whether an API key is configured (cached, avoids Fernet on every call)."""
    global _api_key_cache
    now = time.monotonic()
    if now - _api_key_cache[0] < _API_KEY_CACHE_TTL:
        return _api_key_cache[1]
    present = _read_api_key() is not None
    _api_key_cache = (now, present)
    return present


def invalidate_api_key_cache() -> None:
    """Force next _has_api_key() call to re-read. Called after set/delete."""
    global _api_key_cache
    _api_key_cache = (0.0, False)


class ProviderInfo(BaseModel):
    active_provider: str | None = Field(description="Name of the active LLM provider, or null if none detected.")
    available: list[str] = Field(description="List of actually usable provider identifiers.")
    routing_tiers: list[str] = Field(description="Currently reachable routing tiers.")


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

    available: list[str] = []
    if _CLAUDE_CLI_AVAILABLE:
        available.append("claude_cli")
    if _has_api_key():
        available.append("anthropic_api")

    routing_tiers = routing.available_tiers if routing else ["passthrough"]

    return ProviderInfo(
        active_provider=provider_name,
        available=available,
        routing_tiers=routing_tiers,
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
    if not key.startswith("sk-") or len(key) < 40:
        raise HTTPException(
            400,
            "Invalid API key format. Anthropic keys start with 'sk-' and are at least 40 characters.",
        )

    _write_api_key(key)
    invalidate_api_key_cache()
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

    # Audit log
    try:
        from app.database import async_session_factory
        from app.services.audit_logger import log_event

        async with async_session_factory() as audit_db:
            await log_event(
                db=audit_db,
                action="api_key_set",
                actor_ip=request.client.host if request.client else None,
                detail={"masked_key": f"sk-...{key[-4:]}"},
                outcome="success",
            )
    except Exception:
        logger.debug("Audit log write failed", exc_info=True)

    return ApiKeyStatus(configured=True, masked_key=f"sk-...{key[-4:]}")


@router.delete("/provider/api-key")
async def delete_api_key(request: Request) -> ApiKeyStatus:
    """Remove the stored API key."""
    cred_file = DATA_DIR / ".api_credentials"
    if cred_file.exists():
        cred_file.unlink()
        logger.info("API key credentials file deleted")
    invalidate_api_key_cache()

    # Clear provider from routing service
    routing = getattr(request.app.state, "routing", None)
    if routing:
        routing.set_provider(None)
    else:
        logger.warning("API key deleted but routing service not available — provider state may be stale")

    # Audit log
    try:
        from app.database import async_session_factory
        from app.services.audit_logger import log_event

        async with async_session_factory() as audit_db:
            await log_event(
                db=audit_db,
                action="api_key_deleted",
                actor_ip=request.client.host if request.client else None,
                outcome="success",
            )
    except Exception:
        logger.debug("Audit log write failed", exc_info=True)

    return ApiKeyStatus(configured=False, masked_key=None)


_API_CREDENTIAL_CONTEXT = "synthesis-api-credential-v1"


def _read_api_key() -> str | None:
    """Read API key: check env var first, then encrypted file."""
    if settings.ANTHROPIC_API_KEY:
        return settings.ANTHROPIC_API_KEY
    cred_file = DATA_DIR / ".api_credentials"
    if not cred_file.exists():
        return None
    try:
        secret = settings.resolve_secret_key()

        def _persist_migrated(new_ciphertext: bytes) -> None:
            cred_file.write_bytes(new_ciphertext)
            logger.info("API credential migrated to PBKDF2 encryption")

        plaintext = decrypt_with_migration(
            cred_file.read_bytes(), secret, _API_CREDENTIAL_CONTEXT, _persist_migrated,
        )
        return plaintext.decode()
    except Exception:
        logger.warning("Failed to decrypt API credentials")
        return None


def _write_api_key(key: str) -> None:
    """Encrypt and persist API key to disk."""
    secret = settings.resolve_secret_key()
    f = derive_fernet(secret, _API_CREDENTIAL_CONTEXT)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cred_file = DATA_DIR / ".api_credentials"
    cred_file.write_bytes(f.encrypt(key.encode()))
    cred_file.chmod(0o600)
