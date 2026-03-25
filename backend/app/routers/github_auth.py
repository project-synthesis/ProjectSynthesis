"""GitHub OAuth flow — login, callback, me, logout."""

import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import GitHubToken
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


async def _get_session_token(request: Request, db: AsyncSession) -> tuple[str, str]:
    """Get session_id and decrypted token from request cookie."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(401, "Not authenticated")
    result = await db.execute(
        select(GitHubToken).where(GitHubToken.session_id == session_id)
    )
    token_row = result.scalar_one_or_none()
    if not token_row:
        raise HTTPException(401, "Not authenticated")
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
) -> GitHubUserResponse:
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
    else:
        row = GitHubToken(
            session_id=session_id,
            token_encrypted=encrypted,
            github_login=user.get("login"),
            github_user_id=str(user.get("id", "")),
            avatar_url=user.get("avatar_url"),
        )
        db.add(row)
    await db.commit()

    response.set_cookie(
        "session_id", session_id, httponly=True, max_age=86400 * 14,
        samesite="lax", secure=_is_secure(), path="/api",
    )
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

    return GitHubUserResponse(login=user.get("login"), avatar_url=user.get("avatar_url"))


@router.get("/auth/me")
async def github_me(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> GitHubUserResponse:
    """Return current user info from stored token."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(401, "Not authenticated")
    result = await db.execute(
        select(GitHubToken).where(GitHubToken.session_id == session_id)
    )
    token_row = result.scalar_one_or_none()
    if not token_row:
        raise HTTPException(401, "Not authenticated")
    return GitHubUserResponse(
        login=token_row.github_login,
        avatar_url=token_row.avatar_url,
        github_user_id=token_row.github_user_id,
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
