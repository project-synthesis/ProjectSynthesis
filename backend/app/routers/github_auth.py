"""GitHub OAuth flow — login, callback, me, logout."""

import logging
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import GitHubToken, LinkedRepo
from app.services.github_client import GitHubClient
from app.services.github_service import GitHubService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/github", tags=["github"])


def _is_secure() -> bool:
    """Return True when FRONTEND_URL uses HTTPS (production)."""
    return bool(settings.FRONTEND_URL and settings.FRONTEND_URL.startswith("https://"))


class LoginUrlResponse(BaseModel):
    url: str = Field(description="GitHub OAuth authorization URL to redirect the user to.")


class GitHubUserResponse(BaseModel):
    login: str = Field(description="GitHub username.")
    avatar_url: str | None = Field(default=None, description="GitHub avatar image URL.")
    github_user_id: str | None = Field(default=None, description="GitHub numeric user ID as string.")


class OkResponse(BaseModel):
    ok: bool = Field(default=True, description="Operation success indicator.")


async def _refresh_token_if_expired(
    token_row: GitHubToken,
    db: AsyncSession,
) -> bool:
    """Refresh an expired GitHub access token using the stored refresh token.

    Returns True if refresh succeeded (token_row is updated in-place),
    False if refresh is not possible or failed.
    """
    # Check if token has expired
    if not token_row.expires_at:
        return False  # No expiry tracked — non-expiring token or legacy
    if token_row.expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
        return False  # Not expired yet

    # Need refresh — check if we have a refresh token
    if not token_row.refresh_token_encrypted:
        logger.warning("GitHub access token expired but no refresh token stored (session=%s)", token_row.session_id[:8])
        return False

    github_svc = GitHubService(secret_key=settings.resolve_secret_key())
    refresh_token = github_svc.decrypt_token(token_row.refresh_token_encrypted)

    # Exchange refresh token for new access token
    client_id = settings.GITHUB_OAUTH_CLIENT_ID
    if not client_id:
        return False

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id": client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"Accept": "application/json"},
            )
            data = resp.json()
    except Exception as exc:
        logger.warning("GitHub token refresh request failed: %s", exc)
        return False

    new_access_token = data.get("access_token")
    if not new_access_token:
        logger.warning("GitHub token refresh returned no access_token: %s", data.get("error", "unknown"))
        return False

    # Update stored tokens
    token_row.token_encrypted = github_svc.encrypt_token(new_access_token)
    if data.get("refresh_token"):
        token_row.refresh_token_encrypted = github_svc.encrypt_token(data["refresh_token"])
    if data.get("expires_in"):
        token_row.expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(data["expires_in"]))
    if data.get("refresh_token_expires_in"):
        refresh_exp = int(data["refresh_token_expires_in"])
        token_row.refresh_token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=refresh_exp)
    await db.commit()
    logger.info("GitHub token refreshed (session=%s)", token_row.session_id[:8])
    return True


async def _get_session_token(request: Request, db: AsyncSession) -> tuple[str, str]:
    """Get session_id and decrypted token from request cookie.

    Automatically refreshes the access token via the stored refresh token
    if the access token has expired.  This handles GitHub Apps with
    "Expire user authorization tokens" enabled (8-hour default expiry).
    """
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(401, "Not authenticated")
    result = await db.execute(
        select(GitHubToken).where(GitHubToken.session_id == session_id)
    )
    token_row = result.scalar_one_or_none()
    if not token_row:
        raise HTTPException(401, "Not authenticated")

    # Auto-refresh if access token has expired
    if token_row.expires_at and token_row.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        refreshed = await _refresh_token_if_expired(token_row, db)
        if not refreshed:
            raise HTTPException(401, "GitHub token expired and refresh failed. Please reconnect.")

    github_svc = GitHubService(secret_key=settings.resolve_secret_key())
    return session_id, github_svc.decrypt_token(token_row.token_encrypted)


@router.get("/auth/login")
async def github_login(response: Response) -> LoginUrlResponse:
    """Generate OAuth URL, set state cookie, return URL."""
    state = secrets.token_urlsafe(32)
    response.set_cookie(
        "github_oauth_state", state, httponly=True, max_age=600,
        samesite="lax", secure=_is_secure(),
    )
    github_svc = GitHubService(
        secret_key=settings.resolve_secret_key(),
        client_id=settings.GITHUB_OAUTH_CLIENT_ID,
    )
    url = github_svc.build_oauth_url(state=state)
    return LoginUrlResponse(url=url)


@router.get("/auth/callback")
async def github_callback(
    code: str,
    state: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Exchange code for token, encrypt, store in DB."""
    import hmac

    cookie_state = request.cookies.get("github_oauth_state")
    if not cookie_state or not hmac.compare_digest(cookie_state, state):
        raise HTTPException(
            400,
            "Invalid OAuth state. The login link may have expired. Please try logging in again.",
        )

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": settings.GITHUB_OAUTH_CLIENT_ID,
                "client_secret": settings.GITHUB_OAUTH_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        data = resp.json()

    access_token = data.get("access_token")
    if not access_token:
        error_desc = data.get("error_description", "unknown error")
        logger.warning("GitHub OAuth token exchange failed: %s", error_desc)
        raise HTTPException(
            status_code=400,
            detail="Authentication failed. Please try again.",
        )

    github_client = GitHubClient()
    user = await github_client.get_user(access_token)

    github_svc = GitHubService(secret_key=settings.resolve_secret_key())
    encrypted = github_svc.encrypt_token(access_token)

    # Token expiry + refresh token (for GitHub Apps with expiring tokens)
    now = datetime.now(timezone.utc)
    expires_in = int(data["expires_in"]) if data.get("expires_in") else None
    expires_at = now + timedelta(seconds=expires_in) if expires_in else None
    refresh_encrypted = (
        github_svc.encrypt_token(data["refresh_token"]) if data.get("refresh_token") else None
    )
    refresh_exp = int(data["refresh_token_expires_in"]) if data.get("refresh_token_expires_in") else None
    refresh_expires_at = now + timedelta(seconds=refresh_exp) if refresh_exp else None

    session_id = request.cookies.get("session_id") or secrets.token_urlsafe(32)

    result = await db.execute(
        select(GitHubToken).where(GitHubToken.session_id == session_id)
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.token_encrypted = encrypted
        existing.github_login = user.get("login")
        existing.github_user_id = str(user.get("id", ""))
        existing.avatar_url = user.get("avatar_url")
        existing.expires_at = expires_at
        existing.refresh_token_encrypted = refresh_encrypted
        existing.refresh_token_expires_at = refresh_expires_at
    else:
        row = GitHubToken(
            session_id=session_id,
            token_encrypted=encrypted,
            github_login=user.get("login"),
            github_user_id=str(user.get("id", "")),
            avatar_url=user.get("avatar_url"),
            expires_at=expires_at,
            refresh_token_encrypted=refresh_encrypted,
            refresh_token_expires_at=refresh_expires_at,
        )
        db.add(row)
    await db.commit()

    logger.info("GitHub OAuth callback completed: user=%s", user.get("login"))

    # Audit log
    try:
        from app.services.audit_logger import log_event

        await log_event(
            db=db,
            action="github_login",
            actor_ip=request.client.host if request.client else None,
            detail={"github_login": user.get("login")},
            outcome="success",
        )
    except Exception:
        logger.debug("Audit log write failed", exc_info=True)

    # Redirect to frontend app after successful OAuth
    frontend_url = settings.FRONTEND_URL.rstrip("/")
    redirect = RedirectResponse(
        url=f"{frontend_url}/app?github_auth=success",
        status_code=302,
    )
    redirect.set_cookie(
        "session_id", session_id, httponly=True, max_age=86400 * 14,
        samesite="lax", secure=_is_secure(), path="/api",
    )
    return redirect


@router.get("/auth/me")
async def github_me(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> GitHubUserResponse:
    """Return current user info, validating the token is still live with GitHub.

    If the stored token has been revoked or expired, clean it up and return 401
    so the frontend shows the reconnect prompt instead of stale cached data.
    """
    session_id, token = await _get_session_token(request, db)
    # Validate token is still accepted by GitHub
    client = GitHubClient()
    try:
        user = await client.get_user(token)
    except Exception:
        # Token revoked/expired — clean up all stale data for this session:
        # token, linked repo, and session cookie.  This prevents orphan linked
        # repos that reference a dead token, which is the root cause of the
        # confusing "connected in Info tab but 401 on Files" state.
        logger.warning(
            "GitHub token validation failed for session %s — cleaning up",
            session_id[:8],
        )
        result = await db.execute(
            select(GitHubToken).where(GitHubToken.session_id == session_id)
        )
        token_row = result.scalar_one_or_none()
        if token_row:
            await db.delete(token_row)
        # Also remove the linked repo tied to this session
        result = await db.execute(
            select(LinkedRepo).where(LinkedRepo.session_id == session_id)
        )
        linked_row = result.scalar_one_or_none()
        if linked_row:
            await db.delete(linked_row)
        await db.commit()
        response.delete_cookie("session_id", path="/api")
        raise HTTPException(401, "GitHub token expired or revoked. Please reconnect.")
    # Update cached user info if it changed
    result = await db.execute(
        select(GitHubToken).where(GitHubToken.session_id == session_id)
    )
    token_row = result.scalar_one_or_none()
    if token_row:
        token_row.github_login = user.get("login", token_row.github_login)
        token_row.avatar_url = user.get("avatar_url", token_row.avatar_url)
        token_row.github_user_id = str(user.get("id", token_row.github_user_id))
        await db.commit()
    return GitHubUserResponse(
        login=user.get("login", ""),
        avatar_url=user.get("avatar_url"),
        github_user_id=str(user.get("id", "")),
    )


@router.post("/auth/logout")
async def github_logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Delete token from DB and clear session cookie."""
    session_id = request.cookies.get("session_id")
    if session_id:
        result = await db.execute(
            select(GitHubToken).where(GitHubToken.session_id == session_id)
        )
        token_row = result.scalar_one_or_none()
        if token_row:
            await db.delete(token_row)
            await db.commit()
    response.delete_cookie("session_id", path="/api")

    # Audit log
    try:
        from app.services.audit_logger import log_event

        await log_event(
            db=db,
            action="github_logout",
            actor_ip=request.client.host if request.client else None,
            outcome="success",
        )
    except Exception:
        logger.debug("Audit log write failed", exc_info=True)

    return OkResponse()


# ---------------------------------------------------------------------------
# Device Flow — zero-config OAuth (no client secret, no callback URL)
# ---------------------------------------------------------------------------


class DevicePollRequest(BaseModel):
    device_code: str = Field(description="Device code from the /auth/device request.")


class DevicePollResponse(BaseModel):
    status: str = Field(description="authorization_pending | slow_down | expired_token | success")
    user: GitHubUserResponse | None = None


@router.post("/auth/device")
async def request_device_code():
    """Start GitHub Device Flow. Returns user_code for the user to enter at github.com/login/device."""
    client_id = settings.GITHUB_OAUTH_CLIENT_ID
    if not client_id:
        raise HTTPException(
            500,
            "GITHUB_OAUTH_CLIENT_ID not configured. Set it in .env to enable GitHub integration.",
        )

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/device/code",
            data={
                "client_id": client_id,
                "scope": "repo read:user",
            },
            headers={"Accept": "application/json"},
        )
        data = resp.json()

    if "user_code" not in data:
        error_desc = data.get("error_description", "Failed to start device flow")
        logger.warning("GitHub device flow request failed: %s", error_desc)
        raise HTTPException(400, error_desc)

    logger.info("GitHub device flow started: user_code=%s", data.get("user_code"))
    return data  # {device_code, user_code, verification_uri, expires_in, interval}


@router.post("/auth/device/poll")
async def poll_device_code(
    body: DevicePollRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> DevicePollResponse:
    """Poll GitHub for device authorization status. Call repeatedly at the specified interval."""
    client_id = settings.GITHUB_OAUTH_CLIENT_ID
    if not client_id:
        raise HTTPException(500, "GITHUB_OAUTH_CLIENT_ID not configured.")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": client_id,
                "device_code": body.device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
        )
        data = resp.json()

    # Still waiting or error
    if "error" in data:
        return DevicePollResponse(status=data["error"])

    # Success — got access token
    access_token = data.get("access_token")
    if not access_token:
        return DevicePollResponse(status="error")

    # Fetch user info
    github_client = GitHubClient()
    user = await github_client.get_user(access_token)

    # Encrypt and store token + refresh token (for expiring GitHub App tokens)
    github_svc = GitHubService(secret_key=settings.resolve_secret_key())
    encrypted = github_svc.encrypt_token(access_token)

    # Compute expiry timestamps from GitHub's response
    now = datetime.now(timezone.utc)
    expires_in = int(data["expires_in"]) if data.get("expires_in") else None
    expires_at = now + timedelta(seconds=expires_in) if expires_in else None
    refresh_encrypted = (
        github_svc.encrypt_token(data["refresh_token"]) if data.get("refresh_token") else None
    )
    refresh_exp = int(data["refresh_token_expires_in"]) if data.get("refresh_token_expires_in") else None
    refresh_expires_at = now + timedelta(seconds=refresh_exp) if refresh_exp else None

    if data.get("refresh_token"):
        logger.info(
            "GitHub token has refresh support (expires_in=%s, refresh_expires_in=%s)",
            data.get("expires_in"), data.get("refresh_token_expires_in"),
        )

    session_id = request.cookies.get("session_id") or secrets.token_urlsafe(32)

    result = await db.execute(
        select(GitHubToken).where(GitHubToken.session_id == session_id)
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.token_encrypted = encrypted
        existing.github_login = user.get("login")
        existing.github_user_id = str(user.get("id", ""))
        existing.avatar_url = user.get("avatar_url")
        existing.expires_at = expires_at
        existing.refresh_token_encrypted = refresh_encrypted
        existing.refresh_token_expires_at = refresh_expires_at
    else:
        row = GitHubToken(
            session_id=session_id,
            token_encrypted=encrypted,
            github_login=user.get("login"),
            github_user_id=str(user.get("id", "")),
            avatar_url=user.get("avatar_url"),
            expires_at=expires_at,
            refresh_token_encrypted=refresh_encrypted,
            refresh_token_expires_at=refresh_expires_at,
        )
        db.add(row)
    await db.commit()

    response.set_cookie(
        "session_id", session_id, httponly=True, max_age=86400 * 14,
        samesite="lax", secure=_is_secure(), path="/api",
    )
    logger.info("GitHub device flow completed: user=%s", user.get("login"))

    # Audit log (same pattern as authorization code callback)
    try:
        from app.services.audit_logger import log_event

        await log_event(
            db=db,
            action="github_login",
            actor_ip=request.client.host if request.client else None,
            detail={"github_login": user.get("login"), "flow": "device"},
            outcome="success",
        )
    except Exception:
        logger.debug("Audit log write failed", exc_info=True)

    return DevicePollResponse(
        status="success",
        user=GitHubUserResponse(
            login=user.get("login"),
            avatar_url=user.get("avatar_url"),
        ),
    )
