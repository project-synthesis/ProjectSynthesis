import logging
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.models.auth import RefreshToken, User
from app.models.github import GitHubToken
from app.schemas.github import GitHubUserInfo
from app.dependencies.auth import get_current_user
from app.schemas.auth import AuthenticatedUser
from app.services.auth_service import issue_jwt_pair
from app.services.github_app_service import refresh_user_token
from app.services.github_service import encrypt_token
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.routers.github_repos import evict_repo_cache
from app.utils.jwt import decode_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["github-auth"])
limiter = Limiter(key_func=get_remote_address)

# ── JWT helpers ────────────────────────────────────────────────────────────


async def _upsert_user(
    session: AsyncSession,
    github_user_id: int,
    github_login: str,
    avatar_url: str | None = None,
) -> tuple[User, bool]:
    """Select-or-create a User row by github_user_id; update login/avatar/last_login if changed.

    Returns:
        (user, is_new) — is_new is True when the user was just created.
    """
    result = await session.execute(
        select(User).where(User.github_user_id == github_user_id)
    )
    user = result.scalar_one_or_none()
    is_new = user is None
    now = datetime.now(timezone.utc)

    if is_new:
        user = User(
            github_user_id=github_user_id,
            github_login=github_login,
            avatar_url=avatar_url,
            last_login_at=now,
        )
        session.add(user)
        await session.flush()  # populate user.id before issuing tokens
    else:
        if user.github_login != github_login:
            user.github_login = github_login
        if avatar_url is not None:
            user.avatar_url = avatar_url
        user.last_login_at = now

    return user, is_new


def _set_refresh_cookie(response: Response | RedirectResponse, raw_refresh: str) -> None:
    """Set the httponly refresh token cookie on any response type."""
    response.set_cookie(
        key="jwt_refresh_token",
        value=raw_refresh,
        httponly=True,
        samesite="strict",   # was "lax" — safe since /auth/jwt/refresh is same-origin XHR only
        path="/auth/jwt/refresh",
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        secure=settings.JWT_COOKIE_SECURE,
    )


# CSRF state is time-bound: expires after 10 minutes (per spec)
_CSRF_MAX_AGE = 600

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"


def _csrf_signer() -> TimestampSigner:
    """Return a TimestampSigner keyed on the app SECRET_KEY."""
    return TimestampSigner(settings.SECRET_KEY)


def _get_session_id(request: Request) -> str:
    """Get or create a session ID from cookies."""
    session = request.session
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())
    return session["session_id"]


def _rotate_session(request: Request, **new_data) -> str:
    """Clear the current session and repopulate with new_data + a fresh session_id.

    Returns the new session_id. Call this after successful authentication to
    prevent session fixation attacks (OWASP A07:2021).
    """
    request.session.clear()
    new_session_id = str(uuid.uuid4())
    request.session["session_id"] = new_session_id
    request.session.update(new_data)
    return new_session_id


@router.get("/auth/github/login")
@limiter.limit(lambda: settings.RATE_LIMIT_AUTH_LOGIN)
async def github_login(request: Request):
    """Initiate GitHub App OAuth flow. Redirects to GitHub."""
    # Soft check: redirect already-authenticated users back to the app.
    # This is a UX improvement, not a security gate — no DB revocation check.
    _auth = request.headers.get("Authorization", "")
    if _auth.startswith("Bearer "):
        try:
            decode_token(_auth[len("Bearer "):])
            return RedirectResponse(url=settings.FRONTEND_URL)
        except Exception:
            pass  # Invalid/expired — proceed with OAuth flow

    if not settings.GITHUB_APP_CLIENT_ID or not settings.GITHUB_APP_CLIENT_SECRET:
        raise HTTPException(
            status_code=400,
            detail="GitHub App not configured. Set GITHUB_APP_CLIENT_ID and GITHUB_APP_CLIENT_SECRET.",
        )

    # Generate time-bound CSRF state via TimestampSigner
    raw_token = uuid.uuid4().hex
    state = _csrf_signer().sign(raw_token).decode()
    request.session["oauth_state"] = state

    params: dict[str, str] = {
        "client_id": settings.GITHUB_APP_CLIENT_ID,
        "state": state,
        "allow_signup": "false",
    }
    # GitHub App client IDs start with "Iv1." — permissions are defined at App
    # registration and the scope param must be omitted.  Standard OAuth App
    # client IDs start with "Ov" and require explicit scopes.
    if not settings.GITHUB_APP_CLIENT_ID.startswith("Iv1."):
        params["scope"] = "read:user,repo"
    redirect_url = f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"
    return RedirectResponse(url=redirect_url)


@router.get("/auth/github/callback")
@limiter.limit(lambda: settings.RATE_LIMIT_AUTH_CALLBACK)
async def github_callback(
    request: Request,
    code: str = "",
    state: str = "",
    session: AsyncSession = Depends(get_session),
):
    """Handle GitHub App OAuth callback."""
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state parameter")

    # Verify CSRF state via signature + expiry check.
    # The signed state token is self-contained CSRF protection: it encodes a
    # random nonce signed with SECRET_KEY and a timestamp.  No session lookup
    # is needed because the token itself cannot be forged or replayed — the
    # session cookie is on the Vite proxy origin (localhost:5199) while the
    # callback is delivered directly to the backend (localhost:8000).
    try:
        _csrf_signer().unsign(state, max_age=_CSRF_MAX_AGE)
    except SignatureExpired:
        raise HTTPException(
            status_code=400,
            detail="OAuth state expired — please restart the login flow",
        )
    except BadSignature:
        raise HTTPException(
            status_code=400,
            detail="Invalid OAuth state signature (CSRF protection)",
        )
    # Clean up any leftover session state (best-effort; may be absent in proxy setups).
    request.session.pop("oauth_state", None)

    # Exchange code for GitHub App user-to-server tokens, then fetch user info
    # in the same connection — avoids a redundant TLS handshake.
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            GITHUB_TOKEN_URL,
            data={
                "client_id": settings.GITHUB_APP_CLIENT_ID,
                "client_secret": settings.GITHUB_APP_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        token_data = token_resp.json()

        access_token = token_data.get("access_token")
        if not access_token:
            error = token_data.get("error_description", "Failed to get access token")
            raise HTTPException(status_code=400, detail=error)

        # Fetch user info in the same client session
        user_resp = await client.get(
            GITHUB_USER_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )
        if user_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to fetch GitHub user info")
        user_data = user_resp.json()

    # GitHub App tokens include expiry and refresh token fields.
    now = datetime.now(timezone.utc)
    expires_in = int(token_data.get("expires_in", 28800))            # default 8h
    refresh_token_str = token_data.get("refresh_token")
    refresh_token_expires_in = int(
        token_data.get("refresh_token_expires_in", 15897600)         # ~6 months
    )
    expires_at = now + timedelta(seconds=expires_in)
    refresh_token_expires_at = now + timedelta(seconds=refresh_token_expires_in)

    # Encrypt tokens
    encrypted_access = encrypt_token(access_token)
    encrypted_refresh = encrypt_token(refresh_token_str) if refresh_token_str else None

    # Use the pre-auth session_id for the GitHubToken record, then rotate after DB work.
    old_session_id = request.session.get("session_id", str(uuid.uuid4()))
    github_user_id = user_data["id"]
    github_login = user_data["login"]

    # Remove any existing token for this session
    await session.execute(
        delete(GitHubToken).where(GitHubToken.session_id == old_session_id)
    )

    new_token = GitHubToken(
        session_id=old_session_id,  # will be updated to new session_id after rotation
        github_user_id=github_user_id,
        github_login=github_login,
        token_encrypted=encrypted_access,
        token_type="github_app",
        refresh_token_encrypted=encrypted_refresh,
        refresh_token_expires_at=refresh_token_expires_at,
        expires_at=expires_at,
        avatar_url=user_data.get("avatar_url"),
    )
    session.add(new_token)
    # Note: do NOT commit here — _upsert_user and _issue_jwt_pair must be part
    # of the same transaction so a constraint violation rolls back everything.

    # Issue JWT pair — upsert User then create RefreshToken record (same tx)
    user, is_new = await _upsert_user(
        session, github_user_id, github_login,
        avatar_url=user_data.get("avatar_url"),
    )
    jwt_access, raw_refresh = await issue_jwt_pair(session, user)

    # Rotate session AFTER all DB work — prevents session fixation (OWASP A07:2021).
    # Update the GitHubToken to the new session_id so future lookups don't break.
    new_session_id = _rotate_session(
        request,
        github_user_id=github_user_id,
        github_login=github_login,
        pending_access_token=jwt_access,
    )
    new_token.session_id = new_session_id

    # Emit structured audit log
    try:
        from app.services.github_app_service import log_token_event
        log_token_event(
            event="user_token_issued",
            github_login=github_login,
            github_user_id=github_user_id,
            session_id=new_session_id,
            expires_at=expires_at,
        )
    except Exception:
        pass

    # Redirect to frontend; token is retrieved via GET /auth/token (never in URL).
    # Append ?new=1 for brand-new users so the frontend can show the onboarding modal.
    redirect_params = "?auth_complete=1"
    if is_new:
        redirect_params += "&new=1"
    redirect = RedirectResponse(url=f"{settings.FRONTEND_URL}/{redirect_params}")
    _set_refresh_cookie(redirect, raw_refresh)
    return redirect


@router.get("/auth/github/me")
async def github_me(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get current GitHub auth status."""
    session_id = request.session.get("session_id")
    if not session_id:
        return GitHubUserInfo(connected=False).model_dump()

    result = await session.execute(
        select(GitHubToken).where(GitHubToken.session_id == session_id)
    )
    token_record = result.scalar_one_or_none()
    if not token_record:
        return GitHubUserInfo(connected=False).model_dump()

    return {
        "connected": True,
        "login": token_record.github_login,
        "avatar_url": token_record.avatar_url,  # cached at login; None for pre-migration tokens
        "github_user_id": token_record.github_user_id,
        "token_type": token_record.token_type,
    }


@router.delete("/auth/github/logout")
async def github_logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    """Revoke stored GitHub token, JWT refresh token, and clear browser cookies."""
    session_id = request.session.get("session_id")
    if session_id:
        await session.execute(
            delete(GitHubToken).where(GitHubToken.session_id == session_id)
        )
        evict_repo_cache(session_id)

    # Revoke the user's most recent JWT refresh token so get_current_user
    # returns AUTH_TOKEN_REVOKED for any outstanding access token after logout.
    github_user_id = request.session.get("github_user_id")
    if github_user_id:
        user_result = await session.execute(
            select(User).where(User.github_user_id == github_user_id)
        )
        user = user_result.scalar_one_or_none()
        if user:
            rt_result = await session.execute(
                select(RefreshToken)
                .where(RefreshToken.user_id == user.id, RefreshToken.revoked.is_(False))
            )
            for rt in rt_result.scalars().all():
                rt.revoked = True

    # Clear browser refresh cookie so it cannot be used to obtain new tokens.
    response.delete_cookie(key="jwt_refresh_token", path="/auth/jwt/refresh")

    # Clear session GitHub data
    request.session.pop("github_user_id", None)
    request.session.pop("github_login", None)

    return {"disconnected": True}


_MANUAL_REFRESH_WINDOW_MINUTES = 30  # refresh if token expires within this window


@router.post("/auth/github/token/refresh")
async def refresh_github_token(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Manually trigger a GitHub App user token refresh.

    Refreshes if the token expires within 30 minutes; returns refreshed=False otherwise.
    Useful after the user reports GitHub API auth failures without waiting for auto-refresh.
    """
    session_id = request.session.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="No active session")

    result = await session.execute(
        select(GitHubToken).where(GitHubToken.session_id == session_id)
    )
    gh_token = result.scalar_one_or_none()
    if not gh_token or gh_token.token_type != "github_app" or not gh_token.refresh_token_encrypted:
        return {"refreshed": False, "reason": "not_a_github_app_token"}

    now = datetime.now(timezone.utc)
    expires_at = gh_token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at - now > timedelta(minutes=_MANUAL_REFRESH_WINDOW_MINUTES):
        return {
            "refreshed": False,
            "reason": "not_expiring_soon",
            "expires_at": expires_at.isoformat(),
        }

    refreshed = await refresh_user_token(bytes(gh_token.refresh_token_encrypted))
    gh_token.token_encrypted = encrypt_token(refreshed["access_token"])
    gh_token.expires_at = refreshed["expires_at"]
    gh_token.refresh_token_encrypted = encrypt_token(refreshed["refresh_token"])
    gh_token.refresh_token_expires_at = refreshed["refresh_token_expires_at"]

    return {"refreshed": True, "expires_at": refreshed["expires_at"].isoformat()}
