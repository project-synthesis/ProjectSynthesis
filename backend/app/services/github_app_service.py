"""GitHub App service.

Provides:
  - App JWT generation (RS256) for authenticating as the GitHub App itself.
  - Installation token issuance for bot write operations (ephemeral, 1 hour).
  - User-to-server token refresh for renewing expired user tokens.

All tokens obtained here use the GitHub App credentials from settings.
Installation tokens are never persisted — they are passed directly to agent
sessions so there is no secret stored beyond its 1-hour lifetime.
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app.config import settings
from app.services.github_service import decrypt_token

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"


# ── App JWT ───────────────────────────────────────────────────────────────────


def generate_app_jwt(app_id: str, private_key_pem: str) -> str:
    """Sign a JWT as the GitHub App (valid 10 min).

    Used to authenticate as the App itself when requesting installation tokens
    or performing other App-level API calls.

    Args:
        app_id: Numeric GitHub App ID (as a string from config).
        private_key_pem: RSA private key in PEM format (\\n-escaped single line
                         or multi-line).

    Returns:
        Signed JWT string.
    """
    from jose import jwt as jose_jwt  # python-jose

    # Normalise \n-escaped single-line PEM to proper multi-line PEM.
    pem = private_key_pem.replace("\\n", "\n")

    now = int(time.time())
    payload = {
        "iat": now - 60,          # issued 60s ago to account for clock skew
        "exp": now + (10 * 60),   # valid for 10 minutes
        "iss": app_id,
    }
    return jose_jwt.encode(payload, pem, algorithm="RS256")


# ── Installation token ────────────────────────────────────────────────────────


async def get_installation_token() -> str:
    """Generate a 1-hour installation token for bot write operations.

    The token is ephemeral — it is not persisted anywhere. Pass it directly
    to agent sessions that need GitHub write access.

    Returns:
        GitHub installation token string (starts with "ghs_").

    Raises:
        RuntimeError: If GitHub App credentials are not configured or the
                      GitHub API returns an error.
    """
    if not settings.GITHUB_APP_ID or not settings.GITHUB_APP_PRIVATE_KEY:
        raise RuntimeError(
            "GitHub App credentials not configured. "
            "Set GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY in .env."
        )
    if not settings.GITHUB_APP_INSTALLATION_ID:
        raise RuntimeError(
            "GITHUB_APP_INSTALLATION_ID not configured in .env."
        )

    app_jwt = generate_app_jwt(settings.GITHUB_APP_ID, settings.GITHUB_APP_PRIVATE_KEY)

    url = f"{_GITHUB_API}/app/installations/{settings.GITHUB_APP_INSTALLATION_ID}/access_tokens"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
            },
            timeout=15,
        )

    if resp.status_code != 201:
        raise RuntimeError(
            f"Failed to create installation token: {resp.status_code} {resp.text}"
        )

    data = resp.json()
    token = data.get("token", "")
    if not token:
        raise RuntimeError("GitHub API returned an empty installation token.")

    logger.info(
        "github_token_event",
        extra={
            "event": "installation_token_issued",
            "github_login": "bot",
            "github_user_id": None,
            "session_id": None,
            "expires_at": data.get("expires_at"),
            "agent_session_id": None,
        },
    )
    return token


# ── User token refresh ────────────────────────────────────────────────────────


async def refresh_user_token(encrypted_refresh: bytes) -> dict:
    """Exchange a user-to-server refresh token for a new access + refresh pair.

    Args:
        encrypted_refresh: Fernet-encrypted refresh token bytes from the DB.

    Returns:
        Dict with keys:
          - access_token (str)
          - expires_at (datetime, UTC)
          - refresh_token (str)
          - refresh_token_expires_at (datetime, UTC)

    Raises:
        RuntimeError: If credentials are not configured or the refresh fails.
    """
    if not settings.GITHUB_APP_CLIENT_ID or not settings.GITHUB_APP_CLIENT_SECRET:
        raise RuntimeError(
            "GitHub App OAuth credentials not configured. "
            "Set GITHUB_APP_CLIENT_ID and GITHUB_APP_CLIENT_SECRET in .env."
        )

    plaintext_refresh = decrypt_token(encrypted_refresh)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": settings.GITHUB_APP_CLIENT_ID,
                "client_secret": settings.GITHUB_APP_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": plaintext_refresh,
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )

    data = resp.json()
    new_access = data.get("access_token")
    if not new_access:
        error = data.get("error_description") or data.get("error") or "Unknown error"
        raise RuntimeError(f"Token refresh failed: {error}")

    now = datetime.now(timezone.utc)
    expires_in = int(data.get("expires_in", 28800))       # default 8h
    refresh_expires_in = int(data.get("refresh_token_expires_in", 15897600))  # ~6mo

    return {
        "access_token": new_access,
        "expires_at": now + timedelta(seconds=expires_in),
        "refresh_token": data.get("refresh_token", plaintext_refresh),
        "refresh_token_expires_at": now + timedelta(seconds=refresh_expires_in),
    }


# ── Structured audit log helper ───────────────────────────────────────────────


def _hashed_session(session_id: Optional[str]) -> Optional[str]:
    """Return a one-way hash of a session ID for safe logging."""
    if not session_id:
        return None
    return hashlib.sha256(session_id.encode()).hexdigest()[:16]


def log_token_event(
    event: str,
    github_login: str,
    github_user_id: Optional[int],
    session_id: Optional[str],
    expires_at: Optional[datetime],
    agent_session_id: Optional[str] = None,
) -> None:
    """Emit a structured INFO-level token audit event."""
    logger.info(
        "github_token_event",
        extra={
            "event": event,
            "github_login": github_login,
            "github_user_id": github_user_id,
            "session_id": _hashed_session(session_id),
            "expires_at": expires_at.isoformat() if expires_at else None,
            "agent_session_id": agent_session_id,
        },
    )
