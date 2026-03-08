"""JWT authentication router: refresh token rotation endpoint."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from jose import ExpiredSignatureError, JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.models.auth import RefreshToken, User
from app.schemas.auth import (
    ERR_TOKEN_EXPIRED,
    ERR_TOKEN_INVALID,
    ERR_TOKEN_MISSING,
    ERR_TOKEN_REVOKED,
    TokenResponse,
)
from app.services.auth_service import issue_jwt_pair
from app.utils.jwt import (
    decode_refresh_token,
    ensure_utc,
    hash_token,
)

router = APIRouter(tags=["jwt-auth"])


@router.post("/auth/jwt/refresh", response_model=TokenResponse)
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
            detail={"code": ERR_TOKEN_INVALID, "message": "Refresh token not found"},
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
            detail={"code": ERR_TOKEN_INVALID, "message": "User not found"},
        )

    # 8. Revoke old token, issue new pair via shared service
    stored_rt.revoked = True
    access_token, new_raw_refresh = await issue_jwt_pair(session, user)

    response.set_cookie(
        key="jwt_refresh_token",
        value=new_raw_refresh,
        httponly=True,
        samesite="lax",
        path="/auth/jwt/refresh",
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        secure=settings.JWT_COOKIE_SECURE,
    )

    return {"access_token": access_token, "token_type": "bearer"}
