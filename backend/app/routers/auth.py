"""JWT authentication router: refresh token rotation, profile, and session management."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from jose import ExpiredSignatureError, JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.dependencies.auth import get_current_user
from app.dependencies.rate_limit import RateLimit
from app.models.auth import RefreshToken, User
from app.schemas.auth import (
    ERR_TOKEN_EXPIRED,
    ERR_TOKEN_INVALID,
    ERR_TOKEN_MISSING,
    ERR_TOKEN_REVOKED,
    AuthenticatedUser,
    GetAuthMeResponse,
    LogoutResponse,
    PatchAuthMeRequest,
    SessionsResponse,
    TokenResponse,
)
from app.services.audit_service import (
    AUTH_LOGOUT,
    AUTH_LOGOUT_ALL,
    AUTH_REFRESH,
    AUTH_TOKEN_EXCHANGE,
    log_auth_event,
)
from app.services.auth_service import issue_jwt_pair, set_refresh_cookie
from app.utils.jwt import (
    decode_refresh_token,
    decode_token,
    ensure_utc,
    hash_token,
)

router = APIRouter(tags=["jwt-auth"])


@router.get("/auth/token", response_model=TokenResponse)
async def get_auth_token(
    request: Request,
    response: Response,
    _rl: None = Depends(RateLimit(lambda: settings.RATE_LIMIT_AUTH_TOKEN)),
) -> dict:
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
    await log_auth_event(AUTH_TOKEN_EXCHANGE, request, user_id=None, metadata={"source": "session"})
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
        "onboarding_step": user.onboarding_step,
        "preferences": json.loads(user.preferences) if user.preferences else {},
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

    All fields are optional; only supplied fields are changed.

    - ``display_name``, ``email``: set to string or explicitly ``null`` to clear.
    - ``onboarding_completed``: ``true`` stamps ``onboarding_completed_at``; ``false`` clears it.
    - ``onboarding_step``: current wizard step (1-4) or ``null`` to clear.
    - ``preferences``: arbitrary JSON dict (dismissed tips, milestones, etc.).
    """
    result = await session.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if "display_name" in data.model_fields_set:
        user.display_name = data.display_name.strip() if data.display_name else None
    if "email" in data.model_fields_set:
        user.email = data.email.strip() if data.email else None
    if data.onboarding_completed is True and user.onboarding_completed_at is None:
        user.onboarding_completed_at = datetime.now(timezone.utc)
    elif data.onboarding_completed is False:
        user.onboarding_completed_at = None
    if "onboarding_step" in data.model_fields_set:
        user.onboarding_step = data.onboarding_step
    if "preferences" in data.model_fields_set:
        user.preferences = json.dumps(data.preferences) if data.preferences else None

    await session.commit()
    return {"updated": True}


@router.delete("/auth/sessions", response_model=SessionsResponse)
async def logout_all_devices(
    request: Request,
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

    await log_auth_event(AUTH_LOGOUT_ALL, request, user_id=current_user.id, metadata={"revoked_count": len(active_rts)})

    return {"revoked_sessions": len(active_rts)}


@router.post("/auth/logout", response_model=LogoutResponse)
async def logout_single_device(
    request: Request,
    response: Response,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    _rl: None = Depends(RateLimit(lambda: settings.RATE_LIMIT_AUTH_LOGOUT)),
) -> dict:
    """Log out the current device by revoking its refresh tokens.

    Extracts ``device_id`` from the JWT access token to identify which
    refresh tokens belong to this device.  If the token has no ``device_id``
    (legacy token), the most-recent non-revoked refresh token for the user
    is revoked instead.
    """
    # Extract device_id from the raw JWT payload
    auth_header = request.headers.get("Authorization", "")
    raw_token = auth_header[len("Bearer "):] if auth_header.startswith("Bearer ") else ""
    device_id: str | None = None
    if raw_token:
        try:
            payload = decode_token(raw_token)
            device_id = payload.get("device_id")
        except Exception:
            pass  # Token already validated by get_current_user; ignore decode errors here

    count = 0
    if device_id:
        # Revoke all non-revoked refresh tokens for this user + device
        rt_result = await session.execute(
            select(RefreshToken)
            .where(
                RefreshToken.user_id == current_user.id,
                RefreshToken.device_id == device_id,
                RefreshToken.revoked.is_(False),
            )
        )
        tokens = rt_result.scalars().all()
        for rt in tokens:
            rt.revoked = True
        count = len(tokens)
    else:
        # Legacy token: revoke the most-recent non-revoked RT for this user
        rt_result = await session.execute(
            select(RefreshToken)
            .where(RefreshToken.user_id == current_user.id, RefreshToken.revoked.is_(False))
            .order_by(RefreshToken.created_at.desc())
            .limit(1)
        )
        rt = rt_result.scalar_one_or_none()
        if rt is not None:
            rt.revoked = True
            count = 1

    # Clear the refresh cookie on this device
    response.delete_cookie(key="jwt_refresh_token", path="/auth/jwt/refresh")

    await log_auth_event(
        AUTH_LOGOUT, request, user_id=current_user.id,
        metadata={"device_id": device_id, "revoked_count": count},
    )

    return {"revoked_count": count}


@router.post("/auth/jwt/refresh", response_model=TokenResponse)
async def jwt_refresh(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    _rl: None = Depends(RateLimit(lambda: settings.RATE_LIMIT_JWT_REFRESH)),
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

    set_refresh_cookie(response, new_raw_refresh)

    await log_auth_event(AUTH_REFRESH, request, user_id=user_id)

    return {"access_token": access_token, "token_type": "bearer"}
