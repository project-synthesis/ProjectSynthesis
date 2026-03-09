"""JWT signing, decoding, and token utility functions."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from jose import ExpiredSignatureError, JWTError, jwt  # noqa: F401

from app.config import settings


def sign_access_token(
    user_id: str,
    github_login: str,
    roles: list[str],
    device_id: str | None = None,
) -> str:
    """Sign an HS256 (or RS256 if configured) access token.

    Payload claims: sub, github_login, roles, jti, exp, iat.
    Optional claim: device_id — used for per-device RT revocation.
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict = {
        "sub": user_id,
        "github_login": github_login,
        "roles": roles,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": expire,
    }
    if device_id is not None:
        payload["device_id"] = device_id

    if settings.JWT_ALGORITHM.startswith("RS") and settings.JWT_PRIVATE_KEY:
        key = settings.JWT_PRIVATE_KEY
    else:
        key = settings.JWT_SECRET

    return jwt.encode(payload, key, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and verify an access token.

    Raises:
        ExpiredSignatureError: token has expired (checked before JWTError).
        JWTError: token is otherwise invalid (bad signature, malformed, etc.).
    """
    if settings.JWT_ALGORITHM.startswith("RS") and settings.JWT_PUBLIC_KEY:
        key = settings.JWT_PUBLIC_KEY
    else:
        key = settings.JWT_SECRET

    # jose raises ExpiredSignatureError (subclass of JWTError) automatically.
    return jwt.decode(token, key, algorithms=[settings.JWT_ALGORITHM])


def sign_refresh_token(user_id: str) -> str:
    """Sign a refresh token using the dedicated JWT_REFRESH_SECRET (always HS256)."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_REFRESH_SECRET, algorithm="HS256")


def decode_refresh_token(token: str) -> dict:
    """Decode and verify a refresh token using JWT_REFRESH_SECRET.

    Raises:
        ExpiredSignatureError: token has expired.
        JWTError: token is invalid.
    """
    return jwt.decode(token, settings.JWT_REFRESH_SECRET, algorithms=["HS256"])


def hash_token(token: str) -> str:
    """SHA-256 hex digest of a raw token string — used for DB storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def ensure_utc(dt: datetime) -> datetime:
    """Return a timezone-aware UTC datetime.

    SQLite returns naive datetimes; this replaces missing tzinfo with UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
