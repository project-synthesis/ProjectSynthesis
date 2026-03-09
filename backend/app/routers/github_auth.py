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
from app.services.auth_service import issue_jwt_pair
from app.services.github_service import encrypt_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["github-auth"])

# ── JWT helpers ────────────────────────────────────────────────────────────


async def _upsert_user(session: AsyncSession, github_user_id: int, github_login: str) -> User:
    """Select-or-create a User row by github_user_id; update login if changed.

    Flushes the session when creating a new User so that ``user.id`` is
    populated by SQLAlchemy before the caller issues JWT tokens.
    """
    result = await session.execute(
        select(User).where(User.github_user_id == github_user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(github_user_id=github_user_id, github_login=github_login)
        session.add(user)
        await session.flush()  # populate user.id before issuing tokens
    else:
        # Only write login if it actually changed; onupdate handles updated_at.
        if user.github_login != github_login:
            user.github_login = github_login
    return user


def _set_refresh_cookie(response: Response | RedirectResponse, raw_refresh: str) -> None:
    """Set the httponly refresh token cookie on any response type."""
    response.set_cookie(
        key="jwt_refresh_token",
        value=raw_refresh,
        httponly=True,
        samesite="lax",
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


@router.get("/auth/github/login")
async def github_login(request: Request):
    """Initiate GitHub App OAuth flow. Redirects to GitHub."""
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

    session_id = _get_session_id(request)
    github_user_id = user_data["id"]
    github_login = user_data["login"]

    # Remove any existing token for this session
    await session.execute(
        delete(GitHubToken).where(GitHubToken.session_id == session_id)
    )

    new_token = GitHubToken(
        session_id=session_id,
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

    # Store user info in session
    request.session["github_user_id"] = github_user_id
    request.session["github_login"] = github_login

    # Issue JWT pair — upsert User then create RefreshToken record (same tx)
    user = await _upsert_user(session, github_user_id, github_login)
    jwt_access, raw_refresh = await issue_jwt_pair(session, user)

    # Emit structured audit log
    try:
        from app.services.github_app_service import log_token_event
        log_token_event(
            event="user_token_issued",
            github_login=github_login,
            github_user_id=github_user_id,
            session_id=session_id,
            expires_at=expires_at,
        )
    except Exception:
        pass

    # Redirect to frontend with access token as query param; refresh token in cookie
    redirect = RedirectResponse(url=f"{settings.FRONTEND_URL}/?access_token={jwt_access}")
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
        from app.routers.github_repos import evict_repo_cache
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
                .order_by(RefreshToken.created_at.desc())
                .limit(1)
            )
            active_rt = rt_result.scalar_one_or_none()
            if active_rt:
                active_rt.revoked = True

    # Clear browser refresh cookie so it cannot be used to obtain new tokens.
    response.delete_cookie(key="jwt_refresh_token", path="/auth/jwt/refresh")

    # Clear session GitHub data
    request.session.pop("github_user_id", None)
    request.session.pop("github_login", None)

    return {"disconnected": True}
