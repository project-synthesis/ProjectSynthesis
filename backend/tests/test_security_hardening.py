"""Tests for cookie security hardening (W1)."""

import inspect
from unittest.mock import patch

import pytest


class TestIsSecure:
    """Unit tests for _is_secure() helper."""

    def test_https_url_returns_true(self):
        with patch("app.routers.github_auth.settings") as mock_settings:
            mock_settings.FRONTEND_URL = "https://synthesis.example.com"
            from app.routers.github_auth import _is_secure
            assert _is_secure() is True

    def test_http_localhost_returns_false(self):
        with patch("app.routers.github_auth.settings") as mock_settings:
            mock_settings.FRONTEND_URL = "http://localhost:5199"
            from app.routers.github_auth import _is_secure
            assert _is_secure() is False

    def test_empty_url_returns_false(self):
        with patch("app.routers.github_auth.settings") as mock_settings:
            mock_settings.FRONTEND_URL = ""
            from app.routers.github_auth import _is_secure
            assert _is_secure() is False

    def test_none_url_returns_false(self):
        with patch("app.routers.github_auth.settings") as mock_settings:
            mock_settings.FRONTEND_URL = None
            from app.routers.github_auth import _is_secure
            assert _is_secure() is False


class TestStateCookieAttributes:
    """Verify the OAuth state cookie includes samesite=lax."""

    @pytest.mark.asyncio
    async def test_state_cookie_has_samesite_lax(self, app_client):
        """Login endpoint must set samesite=lax on github_oauth_state cookie."""
        with patch("app.routers.github_auth.settings") as mock_settings:
            mock_settings.FRONTEND_URL = "http://localhost:5199"
            mock_settings.GITHUB_OAUTH_CLIENT_ID = "test-client-id"
            mock_settings.resolve_secret_key.return_value = "test-secret"
            resp = await app_client.get("/api/github/auth/login")

        assert resp.status_code == 200
        # Check the set-cookie header for github_oauth_state
        cookie_headers = [
            v for k, v in resp.headers.multi_items()
            if k.lower() == "set-cookie" and "github_oauth_state" in v
        ]
        assert len(cookie_headers) == 1, f"Expected 1 state cookie, got {cookie_headers}"
        cookie = cookie_headers[0].lower()
        assert "samesite=lax" in cookie


class TestSessionCookieMaxAge:
    """Verify the session cookie max_age is 14 days (not 30)."""

    def test_session_max_age_is_14_days(self):
        """Session cookie must use max_age=86400*14."""
        import inspect
        from app.routers import github_auth

        source = inspect.getsource(github_auth.github_callback)
        # The session cookie must use 14-day max_age
        assert "86400 * 14" in source or "86400*14" in source or "1209600" in source, (
            "Session cookie max_age should be 86400 * 14 (14 days), not 86400 * 30"
        )
        assert "86400 * 30" not in source, (
            "Session cookie max_age should NOT be 86400 * 30 (30 days)"
        )


class TestSecureFlagWhenHTTPS:
    """Verify the secure flag is set when FRONTEND_URL uses HTTPS."""

    @pytest.mark.asyncio
    async def test_state_cookie_has_secure_flag_when_https(self, app_client):
        """Login endpoint must set secure flag on state cookie when HTTPS."""
        with patch("app.routers.github_auth.settings") as mock_settings:
            mock_settings.FRONTEND_URL = "https://synthesis.example.com"
            mock_settings.GITHUB_OAUTH_CLIENT_ID = "test-client-id"
            mock_settings.resolve_secret_key.return_value = "test-secret"
            resp = await app_client.get("/api/github/auth/login")

        assert resp.status_code == 200
        cookie_headers = [
            v for k, v in resp.headers.multi_items()
            if k.lower() == "set-cookie" and "github_oauth_state" in v
        ]
        assert len(cookie_headers) == 1, f"Expected 1 state cookie, got {cookie_headers}"
        cookie = cookie_headers[0].lower()
        assert "; secure" in cookie, (
            "State cookie must include Secure flag when FRONTEND_URL is HTTPS"
        )

    @pytest.mark.asyncio
    async def test_state_cookie_no_secure_flag_when_http(self, app_client):
        """Login endpoint must NOT set secure flag on state cookie when HTTP."""
        with patch("app.routers.github_auth.settings") as mock_settings:
            mock_settings.FRONTEND_URL = "http://localhost:5199"
            mock_settings.GITHUB_OAUTH_CLIENT_ID = "test-client-id"
            mock_settings.resolve_secret_key.return_value = "test-secret"
            resp = await app_client.get("/api/github/auth/login")

        assert resp.status_code == 200
        cookie_headers = [
            v for k, v in resp.headers.multi_items()
            if k.lower() == "set-cookie" and "github_oauth_state" in v
        ]
        assert len(cookie_headers) == 1
        cookie = cookie_headers[0].lower()
        assert "; secure" not in cookie, (
            "State cookie must NOT include Secure flag when FRONTEND_URL is HTTP"
        )


class TestSessionCookiePath:
    """Verify the session cookie is scoped to path=/api."""

    def test_session_cookie_has_path_api(self):
        """Session cookie in callback must include path='/api'."""
        from app.routers import github_auth

        source = inspect.getsource(github_auth.github_callback)
        assert 'path="/api"' in source or "path='/api'" in source, (
            "Session cookie must include path='/api' to scope it to API routes"
        )

    def test_logout_delete_cookie_has_path_api(self):
        """Logout delete_cookie must include path='/api' to match the set_cookie path."""
        from app.routers import github_auth

        source = inspect.getsource(github_auth.github_logout)
        assert 'path="/api"' in source or "path='/api'" in source, (
            "delete_cookie('session_id') must include path='/api' to match the set_cookie path"
        )
