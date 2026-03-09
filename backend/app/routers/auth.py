"""JWT authentication router: refresh token rotation, profile, and session management."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from jose import ExpiredSignatureError, JWTError
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.dependencies.auth import get_current_user
from app.models.auth import RefreshToken, User
from app.schemas.auth import (
    ERR_TOKEN_EXPIRED,
    ERR_TOKEN_INVALID,
    ERR_TOKEN_MISSING,
    ERR_TOKEN_REVOKED,
    AuthenticatedUser,
    GetAuthMeResponse,
    PatchAuthMeRequest,
    SessionsResponse,
    TokenResponse,
)
from app.services.auth_service import issue_jwt_pair
from app.utils.jwt import (
    decode_refresh_token,
    ensure_utc,
    hash_token,
)

router = APIRouter(tags=["jwt-auth"])
limiter = Limiter(key_func=get_remote_address)


@router.get("/auth/token", response_model=TokenResponse)
async def get_auth_token(request: Request, response: Response) -> dict:
    """Exchange the one-time session token for the JWT access token.

    Called by the frontend immediately after the OAuth callback redirect.
    The token is stored in the server-side session (never in URL) and cleared
    on first read — subsequent calls return 401.
    """
    token = request.session.pop("pending_access_token", None)
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"code": ERR_TOKEN_MISSING, "message": "No pending auth token — please log in again"},
        )
    return {"access_token": token, "token_type": "bearer"}


@router.get("/auth/me", response_model=GetAuthMeResponse)
async def get_auth_me(
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return the authenticated user's full profile."""
    result = await session.execute(
        select(User).where(User.id == current_user.id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "id": user.id,
        "github_login": user.github_login,
        "github_user_id": user.github_user_id,
        "role": user.role.value,
        "email": user.email,
        "avatar_url": user.avatar_url,
        "display_name": user.display_name,
        "onboarding_completed": user.onboarding_completed_at is not None,
        "onboarding_completed_at": user.onboarding_completed_at.isoformat() if user.onboarding_completed_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat(),
    }


@router.patch("/auth/me")
async def patch_auth_me(
    data: PatchAuthMeRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Update the authenticated user's profile.

    Accepted fields: ``display_name``, ``email``, ``onboarding_completed``.
    All fields are optional; only supplied fields are changed.
    Setting ``onboarding_completed=true`` stamps ``onboarding_completed_at``
    with the current UTC time; ``false`` clears it.
    """
    result = await session.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if data.display_name is not None:
        user.display_name = data.display_name.strip() or None
    if data.email is not None:
        user.email = data.email.strip() or None
    if data.onboarding_completed is True and user.onboarding_completed_at is None:
        user.onboarding_completed_at = datetime.now(timezone.utc)
    elif data.onboarding_completed is False:
        user.onboarding_completed_at = None

    await session.commit()
    return {"updated": True}


@router.delete("/auth/sessions", response_model=SessionsResponse)
async def logout_all_devices(
    response: Response,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Revoke all refresh tokens for the authenticated user (logout all devices).

    Use this endpoint when the user wants to invalidate all active sessions,
    e.g. after a suspected credential compromise.
    """
    rt_result = await session.execute(
        select(RefreshToken)
        .where(RefreshToken.user_id == current_user.id, RefreshToken.revoked.is_(False))
    )
    active_rts = rt_result.scalars().all()
    for rt in active_rts:
        rt.revoked = True

    # Clear the refresh cookie on this device
    response.delete_cookie(key="jwt_refresh_token", path="/auth/jwt/refresh")

    return {"revoked_sessions": len(active_rts)}


@router.post("/auth/jwt/refresh", response_model=TokenResponse)
@limiter.limit(lambda: settings.RATE_LIMIT_JWT_REFRESH)
async def jwt_refresh(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Rotate refresh token and issue a new access token.

    Reads the ``jwt_refresh_token`` httponly cookie, validates and revokes it,
    then issues a fresh access token + new refresh cookie.
    """
    # 1. Read cookie
    raw_token = request.cookies.get("jwt_refresh_token")
    if not raw_token:
        raise HTTPException(
            status_code=401,
            detail={"code": ERR_TOKEN_MISSING, "message": "Refresh token cookie missing"},
        )

    # 2. Decode — ExpiredSignatureError BEFORE JWTError (subclass ordering)
    try:
        payload = decode_refresh_token(raw_token)
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail={"code": ERR_TOKEN_EXPIRED, "message": "Refresh token has expired"},
        )
    except JWTError:
        raise HTTPException(
            status_code=401,
            detail={"code": ERR_TOKEN_INVALID, "message": "Refresh token is invalid"},
        )

    user_id: str = payload.get("sub", "")
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail={"code": ERR_TOKEN_INVALID, "message": "Refresh token is invalid"},
        )

    # 3. Look up stored refresh token by hash
    token_hash = hash_token(raw_token)
    rt_result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored_rt = rt_result.scalar_one_or_none()
    if stored_rt is None:
        raise HTTPException(
            status_code=401,
            detail={"code": ERR_TOKEN_INVALID, "message": "Authentication failed"},
        )

    # 4. Cross-validate: stored user_id must match JWT sub claim
    if stored_rt.user_id != user_id:
        raise HTTPException(
            status_code=401,
            detail={"code": ERR_TOKEN_INVALID, "message": "Refresh token is invalid"},
        )

    # 5. Revocation check
    if stored_rt.revoked:
        raise HTTPException(
            status_code=401,
            detail={"code": ERR_TOKEN_REVOKED, "message": "Refresh token has been revoked"},
        )

    # 6. Expiry check (SQLite returns naive datetimes — ensure_utc bridges that)
    if ensure_utc(stored_rt.expires_at) < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=401,
            detail={"code": ERR_TOKEN_EXPIRED, "message": "Refresh token has expired"},
        )

    # 7. Load user
    user_result = await session.execute(
        select(User).where(User.id == user_id)
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=401,
            detail={"code": ERR_TOKEN_INVALID, "message": "Authentication failed"},
        )

    # 8. Revoke old token, issue new pair via shared service
    # Preserve device_id so per-device revocation works across refresh rotations.
    stored_rt.revoked = True
    access_token, new_raw_refresh = await issue_jwt_pair(
        session, user, device_id=stored_rt.device_id
    )

    response.set_cookie(
        key="jwt_refresh_token",
        value=new_raw_refresh,
        httponly=True,
        samesite="strict",   # was "lax" — safe since this is same-origin XHR only
        path="/auth/jwt/refresh",
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        secure=settings.JWT_COOKIE_SECURE,
    )

    return {"access_token": access_token, "token_type": "bearer"}
