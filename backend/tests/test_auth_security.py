"""Auth security hardening tests — 11 TDD cycles, RED-first.

Run: cd backend && source .venv/bin/activate && pytest tests/test_auth_security.py -v
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.auth import (
    ERR_TOKEN_EXPIRED,
    ERR_TOKEN_INVALID,
    ERR_TOKEN_MISSING,
    ERR_TOKEN_REVOKED,
)
from app.utils.jwt import sign_access_token, sign_refresh_token
from app.dependencies.auth import get_current_user


# ── Cycle 1: Token in URL (Gap A) ─────────────────────────────────────────


async def test_callback_redirect_url_has_no_access_token():
    """OAuth callback must NOT embed JWT in redirect URL (ASVS §3.5.2)."""
    from fastapi.responses import RedirectResponse
    from app.routers.github_auth import github_callback

    with patch("app.routers.github_auth._csrf_signer") as mock_signer_fn:
        mock_signer = MagicMock()
        mock_signer.unsign.return_value = b"nonce"
        mock_signer_fn.return_value = mock_signer

        mock_token_resp = MagicMock()
        mock_token_resp.json.return_value = {"access_token": "ghs_fake", "expires_in": 28800}
        mock_user_resp = MagicMock()
        mock_user_resp.status_code = 200
        mock_user_resp.json.return_value = {
            "id": 999, "login": "octocat", "avatar_url": "https://avatars.example.com/1"
        }

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_token_resp)
        mock_http.get = AsyncMock(return_value=mock_user_resp)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        mock_user = MagicMock()
        mock_user.id = "user-uuid-1"
        mock_user.github_login = "octocat"
        mock_user.role = MagicMock(value="user")
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        mock_request = MagicMock()
        mock_request.session = {}

        with patch("app.routers.github_auth.httpx.AsyncClient", return_value=mock_http):
            with patch("app.routers.github_auth.issue_jwt_pair",
                       AsyncMock(return_value=("access.jwt.token", "refresh.jwt.token"))):
                with patch("app.routers.github_auth.encrypt_token", return_value=b"enc"):
                    result = await github_callback(
                        request=mock_request, code="code", state="state", session=mock_db
                    )

    assert isinstance(result, RedirectResponse)
    location = result.headers.get("location", "")
    assert "access_token=" not in location, (
        f"JWT must not be sent as URL query parameter — found in redirect: {location}"
    )


async def test_get_auth_token_returns_pending_token_from_session():
    """GET /auth/token exchanges the one-time session token for the access token."""
    from app.routers.auth import get_auth_token  # does not exist yet → ImportError

    mock_request = MagicMock()
    mock_request.session = {"pending_access_token": "my.jwt.token"}
    mock_response = MagicMock()

    result = await get_auth_token(request=mock_request, response=mock_response)

    assert result["access_token"] == "my.jwt.token"
    assert result["token_type"] == "bearer"


async def test_get_auth_token_clears_session_after_read():
    """GET /auth/token removes the pending token after returning it (one-time use)."""
    from app.routers.auth import get_auth_token

    session_data = {"pending_access_token": "my.jwt.token"}
    mock_request = MagicMock()
    mock_request.session = session_data
    mock_response = MagicMock()

    await get_auth_token(request=mock_request, response=mock_response)

    assert "pending_access_token" not in session_data


async def test_get_auth_token_returns_401_when_no_pending_token():
    """GET /auth/token returns 401 when no pending token is in the session."""
    from fastapi import HTTPException
    from app.routers.auth import get_auth_token

    mock_request = MagicMock()
    mock_request.session = {}
    mock_response = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await get_auth_token(request=mock_request, response=mock_response)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == ERR_TOKEN_MISSING


# ── Cycle 2: Access Token Revocation Gap (Gap 7) ──────────────────────────


async def test_get_current_user_rejects_revoked_device_even_after_other_device_refreshes():
    """After Device A logs out, Device A's access token is rejected even if Device B
    has since refreshed (creating a new, non-revoked RT).

    The critical invariant: revocation is per-device, not global-most-recent.
    """
    device_id_a = "device-a-uuid"
    # sign_access_token does not yet accept device_id → will fail with TypeError (RED)
    token_a = sign_access_token("user-123", "octocat", ["user"], device_id=device_id_a)

    # Device A's RT: revoked
    mock_rt_a = MagicMock()
    mock_rt_a.user_id = "user-123"
    mock_rt_a.device_id = device_id_a
    mock_rt_a.revoked = True

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_rt_a

    mock_request = MagicMock()
    mock_request.headers = {"Authorization": f"Bearer {token_a}"}

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(Exception) as exc_info:
        await get_current_user(request=mock_request, session=mock_session)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == ERR_TOKEN_REVOKED


async def test_get_current_user_device_b_unaffected_by_device_a_logout():
    """Device B's access token (its own device_id) passes even when Device A's RT is revoked."""
    device_id_b = "device-b-uuid"
    token_b = sign_access_token("user-123", "octocat", ["user"], device_id=device_id_b)

    # Device B's RT: active
    mock_rt_b = MagicMock()
    mock_rt_b.user_id = "user-123"
    mock_rt_b.device_id = device_id_b
    mock_rt_b.revoked = False

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_rt_b

    mock_request = MagicMock()
    mock_request.headers = {"Authorization": f"Bearer {token_b}"}

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    user = await get_current_user(request=mock_request, session=mock_session)

    assert user.id == "user-123"


async def test_sign_access_token_embeds_device_id_claim():
    """sign_access_token with device_id includes it in the JWT payload."""
    from app.utils.jwt import decode_token

    token = sign_access_token("user-123", "octocat", ["user"], device_id="dev-uuid-xyz")
    payload = decode_token(token)

    assert payload.get("device_id") == "dev-uuid-xyz"


async def test_sign_access_token_without_device_id_still_works():
    """sign_access_token without device_id remains backward-compatible."""
    from app.utils.jwt import decode_token

    token = sign_access_token("user-123", "octocat", ["user"])  # no device_id
    payload = decode_token(token)

    assert payload["sub"] == "user-123"
    assert "device_id" not in payload or payload["device_id"] is None
