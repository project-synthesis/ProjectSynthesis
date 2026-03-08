"""Auth service: JWT pair issuance shared between OAuth/PAT and refresh flows."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.auth import RefreshToken, User
from app.utils.jwt import hash_token, sign_access_token, sign_refresh_token


async def issue_jwt_pair(session: AsyncSession, user: User) -> tuple[str, str]:
    """Persist a new RefreshToken row and return ``(access_token, raw_refresh_token)``.

    The caller is responsible for committing the session.  ``session.flush()``
    is *not* called here — the caller must ensure ``user.id`` is populated
    before calling this function (use ``session.flush()`` after adding a new
    User row).

    Returns:
        (access_token, raw_refresh_token) where ``raw_refresh_token`` is the
        plaintext token to be placed in the httponly cookie.  Only its SHA-256
        hash is stored in the database.
    """
    raw_refresh = sign_refresh_token(user.id)
    token_hash = hash_token(raw_refresh)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)

    refresh_record = RefreshToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
        revoked=False,
    )
    session.add(refresh_record)

    access_token = sign_access_token(user.id, user.github_login, [user.role.value])
    return access_token, raw_refresh
