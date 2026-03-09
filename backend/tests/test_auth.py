"""JWT authentication layer tests — all 17 tests written RED-first."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jose import ExpiredSignatureError, JWTError

from app.utils.jwt import (
    decode_refresh_token,
    decode_token,
    hash_token,
    sign_access_token,
    sign_refresh_token,
)
from app.schemas.auth import (
    AuthenticatedUser,
    ERR_INSUFFICIENT_PERMISSIONS,
    ERR_TOKEN_EXPIRED,
    ERR_TOKEN_INVALID,
    ERR_TOKEN_MISSING,
    ERR_TOKEN_REVOKED,
)
from app.dependencies.auth import get_current_user, require_roles


# ── JWT Utils (5 tests) ────────────────────────────────────────────────────


def test_sign_and_decode_hs256_roundtrip():
    """sign_access_token → decode_token returns correct payload fields."""
    token = sign_access_token("user-123", "octocat", ["user"])
    payload = decode_token(token)

    assert payload["sub"] == "user-123"
    assert payload["github_login"] == "octocat"
    assert payload["roles"] == ["user"]
    assert "jti" in payload


def test_expired_token_raises():
    """decode_token raises ExpiredSignatureError for tokens with past expiry."""
    from app.config import settings as real_settings

    with patch("app.utils.jwt.settings") as mock_settings:
        mock_settings.JWT_SECRET = real_settings.JWT_SECRET
        mock_settings.JWT_ALGORITHM = "HS256"
        mock_settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES = -1
        token = sign_access_token("user-123", "octocat", ["user"])

        with pytest.raises(ExpiredSignatureError):
            decode_token(token)


def test_tampered_signature_raises():
    """Corrupting the JWT signature raises JWTError."""
    token = sign_access_token("user-123", "octocat", ["user"])
    parts = token.split(".")
    parts[2] = parts[2][:-4] + "XXXX"
    tampered = ".".join(parts)

    with pytest.raises(JWTError):
        decode_token(tampered)


def test_sign_refresh_token_different_from_access():
    """sign_refresh_token produces a different token using a different secret."""
    access = sign_access_token("user-123", "octocat", ["user"])
    refresh = sign_refresh_token("user-123")

    assert access != refresh
    payload = decode_refresh_token(refresh)
    assert payload["sub"] == "user-123"


def test_decode_refresh_with_access_secret_raises():
    """Decoding a refresh token with decode_token (access secret) raises JWTError."""
    refresh = sign_refresh_token("user-123")

    with pytest.raises(JWTError):
        decode_token(refresh)


# ── get_current_user dependency (5 tests) ─────────────────────────────────


async def test_get_current_user_valid_token_returns_user():
    """Valid bearer token returns AuthenticatedUser with correct fields."""
    token = sign_access_token("user-123", "octocat", ["user"])

    mock_request = MagicMock()
    mock_request.headers = {"Authorization": f"Bearer {token}"}

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    user = await get_current_user(request=mock_request, session=mock_session)

    assert isinstance(user, AuthenticatedUser)
    assert user.id == "user-123"
    assert user.github_login == "octocat"
    assert user.roles == ["user"]


async def test_get_current_user_missing_header_raises_401():
    """Missing Authorization header raises 401 with AUTH_TOKEN_MISSING code."""
    from fastapi import HTTPException

    mock_request = MagicMock()
    mock_request.headers = {}
    mock_session = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(request=mock_request, session=mock_session)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == ERR_TOKEN_MISSING


async def test_get_current_user_invalid_token_raises_401():
    """Invalid JWT format raises 401 with AUTH_TOKEN_INVALID code."""
    from fastapi import HTTPException

    mock_request = MagicMock()
    mock_request.headers = {"Authorization": "Bearer not.a.jwt"}
    mock_session = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(request=mock_request, session=mock_session)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == ERR_TOKEN_INVALID


async def test_get_current_user_expired_token_raises_401():
    """Expired token raises 401 with AUTH_TOKEN_EXPIRED code."""
    from fastapi import HTTPException
    from app.config import settings as real_settings

    with patch("app.utils.jwt.settings") as mock_settings:
        mock_settings.JWT_SECRET = real_settings.JWT_SECRET
        mock_settings.JWT_ALGORITHM = "HS256"
        mock_settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES = -1
        token = sign_access_token("user-123", "octocat", ["user"])

    # Token signed with real secret but -1 min expiry — decode_token (real settings)
    # verifies the signature but raises ExpiredSignatureError on the claim.
    mock_request = MagicMock()
    mock_request.headers = {"Authorization": f"Bearer {token}"}
    mock_session = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(request=mock_request, session=mock_session)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == ERR_TOKEN_EXPIRED


async def test_get_current_user_revoked_raises_401():
    """Token whose associated refresh record is revoked raises 401 AUTH_TOKEN_REVOKED."""
    from fastapi import HTTPException

    token = sign_access_token("user-123", "octocat", ["user"])

    mock_rt = MagicMock()
    mock_rt.revoked = True

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_rt

    mock_request = MagicMock()
    mock_request.headers = {"Authorization": f"Bearer {token}"}

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(request=mock_request, session=mock_session)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == ERR_TOKEN_REVOKED


# ── require_roles (3 tests) ───────────────────────────────────────────────


async def test_require_roles_matching_role_passes():
    """User with required role passes through unchanged."""
    user = AuthenticatedUser(id="u1", github_login="octocat", roles=["admin"])
    checker = require_roles("admin")
    result = await checker(current_user=user)
    assert result is user


async def test_require_roles_wrong_role_raises_403():
    """User without required role raises 403 AUTH_INSUFFICIENT_PERMISSIONS."""
    from fastapi import HTTPException

    user = AuthenticatedUser(id="u1", github_login="octocat", roles=["user"])
    checker = require_roles("admin")

    with pytest.raises(HTTPException) as exc_info:
        await checker(current_user=user)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == ERR_INSUFFICIENT_PERMISSIONS


async def test_require_roles_any_of_multiple_passes():
    """User with any one of the required roles passes through."""
    user = AuthenticatedUser(id="u1", github_login="octocat", roles=["moderator"])
    checker = require_roles("admin", "moderator")
    result = await checker(current_user=user)
    assert result is user


# ── /auth/jwt/refresh endpoint (4 tests) ─────────────────────────────────


async def test_refresh_valid_cookie_returns_new_access_token():
    """Valid refresh cookie issues a new access token and rotates the refresh token."""
    from app.routers.auth import jwt_refresh

    raw_refresh = sign_refresh_token("user-123")

    mock_stored_rt = MagicMock()
    mock_stored_rt.revoked = False
    mock_stored_rt.expires_at = datetime(2099, 1, 1, tzinfo=timezone.utc)
    mock_stored_rt.user_id = "user-123"
    mock_stored_rt.device_id = None  # legacy token — no device_id

    mock_user = MagicMock()
    mock_user.id = "user-123"
    mock_user.github_login = "octocat"
    mock_user.role = MagicMock()
    mock_user.role.value = "user"

    rt_result = MagicMock()
    rt_result.scalar_one_or_none.return_value = mock_stored_rt
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = mock_user

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=[rt_result, user_result])
    mock_session.add = MagicMock()  # session.add() is synchronous in SQLAlchemy

    mock_request = MagicMock()
    mock_request.cookies = {"jwt_refresh_token": raw_refresh}
    mock_response = MagicMock()

    result = await jwt_refresh(
        request=mock_request,
        response=mock_response,
        session=mock_session,
    )

    assert "access_token" in result
    assert mock_stored_rt.revoked is True
    mock_session.add.assert_called()
    mock_response.set_cookie.assert_called()


async def test_refresh_missing_cookie_raises_401():
    """Missing jwt_refresh_token cookie raises 401 AUTH_TOKEN_MISSING."""
    from fastapi import HTTPException
    from app.routers.auth import jwt_refresh

    mock_request = MagicMock()
    mock_request.cookies = {}
    mock_response = MagicMock()
    mock_session = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await jwt_refresh(request=mock_request, response=mock_response, session=mock_session)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == ERR_TOKEN_MISSING


async def test_refresh_invalid_token_raises_401():
    """Invalid refresh token value raises 401 AUTH_TOKEN_INVALID."""
    from fastapi import HTTPException
    from app.routers.auth import jwt_refresh

    mock_request = MagicMock()
    mock_request.cookies = {"jwt_refresh_token": "garbage.token.value"}
    mock_response = MagicMock()
    mock_session = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await jwt_refresh(request=mock_request, response=mock_response, session=mock_session)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == ERR_TOKEN_INVALID


async def test_refresh_revoked_token_raises_401():
    """A revoked refresh token raises 401 AUTH_TOKEN_REVOKED."""
    from fastapi import HTTPException
    from app.routers.auth import jwt_refresh

    raw_refresh = sign_refresh_token("user-123")

    mock_stored_rt = MagicMock()
    mock_stored_rt.user_id = "user-123"  # must pass cross-validation first
    mock_stored_rt.revoked = True

    rt_result = MagicMock()
    rt_result.scalar_one_or_none.return_value = mock_stored_rt

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=rt_result)

    mock_request = MagicMock()
    mock_request.cookies = {"jwt_refresh_token": raw_refresh}
    mock_response = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await jwt_refresh(request=mock_request, response=mock_response, session=mock_session)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == ERR_TOKEN_REVOKED


async def test_refresh_user_id_mismatch_raises_401():
    """Refresh token whose stored user_id differs from JWT sub raises 401 AUTH_TOKEN_INVALID."""
    from fastapi import HTTPException
    from app.routers.auth import jwt_refresh

    # Sign for "user-123" but stored record claims "user-999"
    raw_refresh = sign_refresh_token("user-123")

    mock_stored_rt = MagicMock()
    mock_stored_rt.user_id = "user-999"  # deliberate mismatch
    mock_stored_rt.revoked = False

    rt_result = MagicMock()
    rt_result.scalar_one_or_none.return_value = mock_stored_rt

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=rt_result)

    mock_request = MagicMock()
    mock_request.cookies = {"jwt_refresh_token": raw_refresh}
    mock_response = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await jwt_refresh(request=mock_request, response=mock_response, session=mock_session)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == ERR_TOKEN_INVALID
