"""FastAPI dependencies for JWT authentication and RBAC."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from jose import ExpiredSignatureError, JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.auth import RefreshToken
from app.schemas.auth import (
    AuthenticatedUser,
    ERR_INSUFFICIENT_PERMISSIONS,
    ERR_TOKEN_EXPIRED,
    ERR_TOKEN_INVALID,
    ERR_TOKEN_MISSING,
    ERR_TOKEN_REVOKED,
)
from app.utils.jwt import decode_token


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AuthenticatedUser:
    """Extract and validate a Bearer JWT from the Authorization header.

    Raises 401 for missing, invalid, expired, or revoked tokens.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"code": ERR_TOKEN_MISSING, "message": "Authorization header missing or malformed"},
        )

    raw_token = auth_header[len("Bearer "):]

    # Decode — ExpiredSignatureError BEFORE JWTError (subclass ordering)
    try:
        payload = decode_token(raw_token)
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail={"code": ERR_TOKEN_EXPIRED, "message": "Access token has expired"},
        )
    except JWTError:
        raise HTTPException(
            status_code=401,
            detail={"code": ERR_TOKEN_INVALID, "message": "Access token is invalid"},
        )

    user_id: str = payload.get("sub", "")
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail={"code": ERR_TOKEN_INVALID, "message": "Access token is invalid"},
        )
    github_login: str = payload.get("github_login", "")
    roles: list[str] = payload.get("roles", [])

    # Revocation check: per-device when device_id is in the JWT; legacy most-recent otherwise.
    # device_id was added in the auth security hardening (2026-03-09).
    # Tokens issued before this change have no device_id claim — use the old most-recent check.
    device_id: str | None = payload.get("device_id")

    if device_id:
        # New path: look up the exact RT for this device
        result = await session.execute(
            select(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.device_id == device_id)
            .limit(1)
        )
    else:
        # Legacy path: most-recent RT for backward compat with tokens issued pre-hardening
        result = await session.execute(
            select(RefreshToken)
            .where(RefreshToken.user_id == user_id)
            .order_by(RefreshToken.created_at.desc())
            .limit(1)
        )

    rt = result.scalar_one_or_none()
    if rt is not None and rt.revoked:
        raise HTTPException(
            status_code=401,
            detail={"code": ERR_TOKEN_REVOKED, "message": "Token has been revoked"},
        )

    return AuthenticatedUser(id=user_id, github_login=github_login, roles=roles)


def require_roles(*roles: str):
    """Return a dependency checker that enforces at least one of the given roles.

    Usage::

        @router.get("/admin", dependencies=[Depends(require_roles("admin"))])
        async def admin_only(): ...
    """
    async def _checker(
        current_user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if not any(r in current_user.roles for r in roles):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": ERR_INSUFFICIENT_PERMISSIONS,
                    "message": f"Required role(s): {list(roles)}",
                },
            )
        return current_user

    return _checker
