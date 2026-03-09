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


# ── Cycle 3: Multi-Device Logout (Gap 3) ──────────────────────────────────


async def test_logout_revokes_all_active_refresh_tokens_not_just_most_recent():
    """DELETE /auth/github/logout must revoke ALL active RTs for the user (multi-device)."""
    from app.routers.github_auth import github_logout

    mock_user = MagicMock()
    mock_user.id = "user-uuid"

    rt1 = MagicMock(); rt1.revoked = False
    rt2 = MagicMock(); rt2.revoked = False  # second device — currently NOT revoked by logout

    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = mock_user

    # GREEN uses scalars().all() returning both RTs
    rt_result = MagicMock()
    rt_result.scalars.return_value.all.return_value = [rt1, rt2]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=[
        MagicMock(),   # DELETE github tokens
        user_result,   # SELECT user by github_user_id
        rt_result,     # SELECT all active RTs (no .limit(1))
    ])
    mock_session.add = MagicMock()

    mock_request = MagicMock()
    mock_request.session = {"session_id": "s1", "github_user_id": 12345}
    mock_response = MagicMock()

    with patch("app.routers.github_auth.evict_repo_cache"):
        await github_logout(request=mock_request, response=mock_response, session=mock_session)

    assert rt1.revoked is True, "RT for current device must be revoked"
    assert rt2.revoked is True, "RT for second device must ALSO be revoked on logout"


async def test_logout_all_devices_endpoint_exists_and_revokes_all_tokens():
    """DELETE /auth/sessions (logout-all) revokes every active RT for the authenticated user."""
    from app.routers.auth import logout_all_devices  # does not exist yet → ImportError

    from app.schemas.auth import AuthenticatedUser
    current_user = AuthenticatedUser(id="user-uuid", github_login="octocat", roles=["user"])

    rt1 = MagicMock(); rt1.revoked = False
    rt2 = MagicMock(); rt2.revoked = False
    rt3 = MagicMock(); rt3.revoked = False

    rt_result = MagicMock()
    rt_result.scalars.return_value.all.return_value = [rt1, rt2, rt3]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=rt_result)

    mock_response = MagicMock()

    result = await logout_all_devices(
        response=mock_response, current_user=current_user, session=mock_session
    )

    assert rt1.revoked is True
    assert rt2.revoked is True
    assert rt3.revoked is True
    assert result["revoked_sessions"] == 3


# ── Cycle 4: Session Fixation (Gap 4) ─────────────────────────────────────


async def test_session_id_is_rotated_after_successful_oauth_callback():
    """Session ID must change after successful authentication (OWASP A07:2021 — session fixation)."""
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
        mock_user_resp.json.return_value = {"id": 999, "login": "octocat", "avatar_url": "https://a.com/1"}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_token_resp)
        mock_http.get = AsyncMock(return_value=mock_user_resp)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        mock_user = MagicMock()
        mock_user.id = "user-uuid"
        mock_user.github_login = "octocat"
        mock_user.role = MagicMock(value="user")
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        # Pre-auth session has a known session_id
        pre_auth_session_id = "pre-auth-session-id-fixed"
        session_data = {"session_id": pre_auth_session_id}
        mock_request = MagicMock()
        mock_request.session = session_data

        with patch("app.routers.github_auth.httpx.AsyncClient", return_value=mock_http):
            with patch("app.routers.github_auth.issue_jwt_pair",
                       AsyncMock(return_value=("access.jwt.token", "refresh.jwt.token"))):
                with patch("app.routers.github_auth.encrypt_token", return_value=b"enc"):
                    await github_callback(
                        request=mock_request, code="code", state="state", session=mock_db
                    )

    # After auth, the session_id must have changed
    post_auth_session_id = session_data.get("session_id")
    assert post_auth_session_id != pre_auth_session_id, (
        f"Session ID must rotate after login. Before: {pre_auth_session_id}, After: {post_auth_session_id}"
    )
    assert post_auth_session_id is not None, "session_id must be present after auth"


# ── Cycle 5: Rate Limiting (Gap 5) ────────────────────────────────────────


def test_rate_limiter_is_imported_in_github_auth_router():
    """The rate limiter must be applied to the GitHub auth router (structural test)."""
    import importlib
    import sys

    # Verify slowapi is importable (will fail if not installed)
    try:
        import slowapi  # noqa: F401
    except ImportError:
        pytest.fail("slowapi is not installed — add it to requirements.txt")

    # Reload to get fresh module state
    if "app.routers.github_auth" in sys.modules:
        del sys.modules["app.routers.github_auth"]
    mod = importlib.import_module("app.routers.github_auth")

    assert hasattr(mod, "limiter"), "github_auth router must define a 'limiter' instance"


def test_rate_limiter_is_imported_in_jwt_auth_router():
    """The rate limiter must be applied to the JWT auth router (structural test)."""
    import importlib
    import sys

    if "app.routers.auth" in sys.modules:
        del sys.modules["app.routers.auth"]
    mod = importlib.import_module("app.routers.auth")

    assert hasattr(mod, "limiter"), "auth router must define a 'limiter' instance"


def test_rate_limit_config_vars_exist():
    """Rate limit configuration must be settable via environment variables."""
    from app.config import Settings

    s = Settings(
        RATE_LIMIT_AUTH_LOGIN="5/minute",
        RATE_LIMIT_AUTH_CALLBACK="5/minute",
        RATE_LIMIT_JWT_REFRESH="30/minute",
    )
    assert s.RATE_LIMIT_AUTH_LOGIN == "5/minute"
    assert s.RATE_LIMIT_AUTH_CALLBACK == "5/minute"
    assert s.RATE_LIMIT_JWT_REFRESH == "30/minute"


# ── Cycle 6: Cookie Secure Enforcement (Gap B) ────────────────────────────


def test_insecure_cookie_warns_in_production_context(caplog):
    """JWT_COOKIE_SECURE=False with a non-localhost FRONTEND_URL emits CRITICAL warning."""
    import logging
    from app.config import Settings

    with caplog.at_level(logging.CRITICAL, logger="app.config"):
        Settings(
            JWT_COOKIE_SECURE=False,
            FRONTEND_URL="https://app.projectsynthesis.io",
        )

    critical_records = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert any("JWT_COOKIE_SECURE" in r.message for r in critical_records), (
        "Must emit CRITICAL warning when JWT_COOKIE_SECURE=False on a production URL"
    )


def test_insecure_cookie_allowed_on_localhost(caplog):
    """JWT_COOKIE_SECURE=False on localhost (dev) must NOT emit a CRITICAL warning."""
    import logging
    from app.config import Settings

    with caplog.at_level(logging.CRITICAL, logger="app.config"):
        Settings(
            JWT_COOKIE_SECURE=False,
            FRONTEND_URL="http://localhost:5199",
        )

    critical_cookie_records = [
        r for r in caplog.records
        if r.levelno >= logging.CRITICAL and "JWT_COOKIE_SECURE" in r.message
    ]
    assert len(critical_cookie_records) == 0, (
        "Should NOT warn about JWT_COOKIE_SECURE=False when FRONTEND_URL is localhost"
    )


# ── Cycle 7: User Model Enrichment (Gap 2) ────────────────────────────────


def test_user_model_has_required_profile_columns():
    """User model must have email, avatar_url, display_name, onboarding_completed_at, last_login_at."""
    from app.models.auth import User

    col_names = {c.name for c in User.__table__.columns}
    required = {"email", "avatar_url", "display_name", "onboarding_completed_at", "last_login_at"}
    missing = required - col_names
    assert not missing, f"User model is missing columns: {missing}"


async def test_upsert_user_sets_last_login_at():
    """_upsert_user must update last_login_at on every login (new and existing users)."""
    from app.routers.github_auth import _upsert_user

    # Simulate existing user
    mock_user = MagicMock()
    mock_user.github_login = "octocat"
    mock_user.last_login_at = None  # never logged in before

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_user

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.flush = AsyncMock()
    mock_session.add = MagicMock()

    await _upsert_user(mock_session, 999, "octocat")

    # last_login_at should now be set
    assert mock_user.last_login_at is not None, "last_login_at must be set after upsert"


async def test_upsert_user_caches_avatar_url():
    """_upsert_user must update avatar_url when provided."""
    from app.routers.github_auth import _upsert_user

    mock_user = MagicMock()
    mock_user.github_login = "octocat"
    mock_user.avatar_url = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_user

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()

    await _upsert_user(mock_session, 999, "octocat", avatar_url="https://avatars.example.com/1")

    assert mock_user.avatar_url == "https://avatars.example.com/1"


async def test_get_auth_me_returns_user_profile():
    """GET /auth/me returns the full user profile for the authenticated user."""
    from app.routers.auth import get_auth_me  # does not exist yet → ImportError
    from app.schemas.auth import AuthenticatedUser

    mock_user = MagicMock()
    mock_user.id = "user-uuid"
    mock_user.github_login = "octocat"
    mock_user.github_user_id = 999
    mock_user.role = MagicMock(value="user")
    mock_user.email = "octocat@github.com"
    mock_user.avatar_url = "https://avatars.example.com/1"
    mock_user.display_name = "The Octocat"
    mock_user.onboarding_completed_at = None
    mock_user.last_login_at = datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)
    mock_user.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_user

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    current_user = AuthenticatedUser(id="user-uuid", github_login="octocat", roles=["user"])

    result = await get_auth_me(current_user=current_user, session=mock_session)

    assert result["id"] == "user-uuid"
    assert result["github_login"] == "octocat"
    assert result["display_name"] == "The Octocat"
    assert result["onboarding_completed"] is False


# ── Cycle 8: Onboarding Flow (Gap 1) ──────────────────────────────────────


async def test_upsert_user_returns_is_new_true_for_new_user():
    """_upsert_user must return (user, True) for a brand-new user."""
    from app.routers.github_auth import _upsert_user

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None  # user doesn't exist

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    user, is_new = await _upsert_user(mock_session, 12345, "newuser")

    assert is_new is True


async def test_upsert_user_returns_is_new_false_for_existing_user():
    """_upsert_user must return (user, False) for a returning user."""
    from app.routers.github_auth import _upsert_user

    mock_user = MagicMock()
    mock_user.github_login = "octocat"
    mock_user.avatar_url = None
    mock_user.last_login_at = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_user

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    user, is_new = await _upsert_user(mock_session, 999, "octocat")

    assert is_new is False


async def test_callback_redirect_includes_new_param_for_new_users():
    """OAuth callback sets ?new=1 in redirect URL when the user is brand new."""
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
        mock_user_resp.json.return_value = {"id": 999, "login": "newuser", "avatar_url": "https://a.com/1"}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_token_resp)
        mock_http.get = AsyncMock(return_value=mock_user_resp)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        mock_db = AsyncMock()

        mock_request = MagicMock()
        mock_request.session = {}

        with patch("app.routers.github_auth.httpx.AsyncClient", return_value=mock_http):
            with patch("app.routers.github_auth.issue_jwt_pair",
                       AsyncMock(return_value=("access.jwt.token", "refresh.jwt.token"))):
                with patch("app.routers.github_auth.encrypt_token", return_value=b"enc"):
                    new_user_mock = MagicMock()
                    new_user_mock.id = "new-user-uuid"
                    new_user_mock.github_login = "newuser"
                    new_user_mock.role = MagicMock(value="user")
                    with patch("app.routers.github_auth._upsert_user",
                               AsyncMock(return_value=(new_user_mock, True))):
                        result = await github_callback(
                            request=mock_request, code="code", state="state", session=mock_db
                        )

    assert isinstance(result, RedirectResponse)
    location = result.headers.get("location", "")
    assert "new=1" in location, f"?new=1 must be in redirect URL for new users, got: {location}"


# ── Cycle 9: Manual GitHub Token Refresh (Gap 8) ──────────────────────────


async def test_github_token_refresh_endpoint_calls_refresh_user_token():
    """POST /auth/github/token/refresh triggers refresh and updates the DB record."""
    from app.routers.github_auth import refresh_github_token  # does not exist yet

    from app.schemas.auth import AuthenticatedUser
    current_user = AuthenticatedUser(id="user-uuid", github_login="octocat", roles=["user"])

    mock_gh_token = MagicMock()
    mock_gh_token.github_user_id = 999
    mock_gh_token.token_type = "github_app"
    mock_gh_token.refresh_token_encrypted = b"encrypted-refresh"
    mock_gh_token.expires_at = datetime(2026, 3, 9, 10, 0, 0, tzinfo=timezone.utc)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_gh_token

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    mock_request = MagicMock()
    mock_request.session = {"session_id": "test-session"}

    new_expires = datetime(2026, 3, 9, 18, 0, 0, tzinfo=timezone.utc)

    with patch("app.routers.github_auth.refresh_user_token", AsyncMock(return_value={
        "access_token": "new_ghs_token",
        "refresh_token": "new_refresh",
        "expires_at": new_expires,
        "refresh_token_expires_at": datetime(2026, 9, 9, tzinfo=timezone.utc),
    })):
        with patch("app.routers.github_auth.encrypt_token", return_value=b"enc"):
            with patch("app.routers.github_auth.datetime") as mock_dt:
                # Token expires at 10:00, mock now = 09:45 → within 30-min window
                mock_dt.now.return_value = datetime(2026, 3, 9, 9, 45, 0, tzinfo=timezone.utc)
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = await refresh_github_token(
                    request=mock_request, current_user=current_user, session=mock_session
                )

    assert result["refreshed"] is True
    assert "expires_at" in result


async def test_github_token_refresh_skips_if_not_expiring_soon():
    """POST /auth/github/token/refresh returns refreshed=False when token is not near expiry."""
    from app.routers.github_auth import refresh_github_token
    from app.schemas.auth import AuthenticatedUser

    current_user = AuthenticatedUser(id="user-uuid", github_login="octocat", roles=["user"])

    mock_gh_token = MagicMock()
    mock_gh_token.token_type = "github_app"
    mock_gh_token.refresh_token_encrypted = b"encrypted-refresh"
    # Token expires far in the future — should NOT trigger refresh
    mock_gh_token.expires_at = datetime(2026, 3, 9, 23, 59, 0, tzinfo=timezone.utc)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_gh_token

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_request = MagicMock()
    mock_request.session = {"session_id": "test-session"}

    with patch("app.routers.github_auth.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = await refresh_github_token(
            request=mock_request, current_user=current_user, session=mock_session
        )

    assert result["refreshed"] is False


# ── Cycle 10: Error Leakage (Gap C) ───────────────────────────────────────


async def test_user_not_found_and_token_not_found_return_same_error_body():
    """User-not-found and token-not-found in /auth/jwt/refresh must return indistinguishable errors.

    An attacker must not be able to distinguish 'user exists' from 'user doesn't exist'
    by probing error messages (oracle attack prevention).
    """
    from fastapi import HTTPException
    from app.routers.auth import jwt_refresh

    raw_refresh = sign_refresh_token("user-999")

    # Scenario A: token not in DB (scalar_one_or_none returns None)
    no_token_result = MagicMock()
    no_token_result.scalar_one_or_none.return_value = None

    mock_session_a = AsyncMock()
    mock_session_a.execute = AsyncMock(return_value=no_token_result)

    mock_request = MagicMock()
    mock_request.cookies = {"jwt_refresh_token": raw_refresh}
    mock_response = MagicMock()

    with pytest.raises(HTTPException) as exc_a:
        await jwt_refresh(request=mock_request, response=mock_response, session=mock_session_a)

    # Scenario B: token found but user not in DB
    mock_stored_rt = MagicMock()
    mock_stored_rt.user_id = "user-999"
    mock_stored_rt.revoked = False
    mock_stored_rt.expires_at = datetime(2099, 1, 1, tzinfo=timezone.utc)

    rt_result = MagicMock()
    rt_result.scalar_one_or_none.return_value = mock_stored_rt

    no_user_result = MagicMock()
    no_user_result.scalar_one_or_none.return_value = None  # user not found

    mock_session_b = AsyncMock()
    mock_session_b.execute = AsyncMock(side_effect=[rt_result, no_user_result])

    with pytest.raises(HTTPException) as exc_b:
        await jwt_refresh(request=mock_request, response=mock_response, session=mock_session_b)

    # Both must return 401 with ERR_TOKEN_INVALID
    assert exc_a.value.status_code == 401
    assert exc_b.value.status_code == 401
    assert exc_a.value.detail["code"] == ERR_TOKEN_INVALID
    assert exc_b.value.detail["code"] == ERR_TOKEN_INVALID

    # The messages must be identical — no oracle distinguishability
    assert exc_a.value.detail["message"] == exc_b.value.detail["message"], (
        f"Error messages differ: '{exc_a.value.detail['message']}' vs '{exc_b.value.detail['message']}'"
    )


# ── Cycle 11: SameSite Strict (Gap D) ─────────────────────────────────────


def test_set_refresh_cookie_uses_samesite_strict():
    """The JWT refresh cookie must use SameSite=Strict (not Lax)."""
    import inspect
    from app.routers import github_auth

    source = inspect.getsource(github_auth._set_refresh_cookie)
    assert 'samesite="strict"' in source or "samesite='strict'" in source, (
        "Refresh cookie must use SameSite=Strict — 'lax' found instead. "
        "The refresh endpoint is only called via same-origin fetch, not top-level navigation."
    )


def test_jwt_refresh_response_cookie_uses_samesite_strict():
    """The new cookie set by /auth/jwt/refresh must also use SameSite=Strict."""
    import inspect
    from app.routers import auth

    source = inspect.getsource(auth.jwt_refresh)
    assert 'samesite="strict"' in source or "samesite='strict'" in source, (
        "jwt_refresh cookie must use SameSite=Strict"
    )
