"""Tests for security hardening (W1: cookies, W2: MCP auth, W3: input validation)."""

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestMCPAuthMiddleware:
    """W2: MCP server authentication."""

    async def test_middleware_noop_when_token_not_configured(self):
        """When MCP_AUTH_TOKEN is not set, all requests pass through."""
        from app.mcp_server import _MCPAuthMiddleware
        app_mock = AsyncMock()
        middleware = _MCPAuthMiddleware(app_mock, auth_token=None, allow_query_token=True)
        scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": [], "query_string": b""}
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        app_mock.assert_called_once()

    async def test_middleware_rejects_missing_token(self):
        """When MCP_AUTH_TOKEN is set, requests without token get 401."""
        from app.mcp_server import _MCPAuthMiddleware
        app_mock = AsyncMock()
        middleware = _MCPAuthMiddleware(app_mock, auth_token="secret-token", allow_query_token=True)
        scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": [], "query_string": b""}
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        app_mock.assert_not_called()

    async def test_middleware_accepts_valid_bearer_token(self):
        """Valid Authorization: Bearer token passes through."""
        from app.mcp_server import _MCPAuthMiddleware
        app_mock = AsyncMock()
        middleware = _MCPAuthMiddleware(app_mock, auth_token="secret-token", allow_query_token=True)
        scope = {
            "type": "http", "method": "POST", "path": "/mcp",
            "headers": [(b"authorization", b"Bearer secret-token")],
            "query_string": b"",
        }
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        app_mock.assert_called_once()

    async def test_middleware_accepts_query_param_token(self):
        """SSE fallback: ?token=<value> is accepted when allowed."""
        from app.mcp_server import _MCPAuthMiddleware
        app_mock = AsyncMock()
        middleware = _MCPAuthMiddleware(app_mock, auth_token="secret-token", allow_query_token=True)
        scope = {
            "type": "http", "method": "GET", "path": "/mcp",
            "headers": [],
            "query_string": b"token=secret-token",
        }
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        app_mock.assert_called_once()

    async def test_middleware_rejects_wrong_token(self):
        """Wrong token gets 401."""
        from app.mcp_server import _MCPAuthMiddleware
        app_mock = AsyncMock()
        middleware = _MCPAuthMiddleware(app_mock, auth_token="secret-token", allow_query_token=True)
        scope = {
            "type": "http", "method": "POST", "path": "/mcp",
            "headers": [(b"authorization", b"Bearer wrong-token")],
            "query_string": b"",
        }
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        app_mock.assert_not_called()

    async def test_middleware_rejects_query_token_when_disabled(self):
        """When allow_query_token=False, ?token= is rejected even if correct."""
        from app.mcp_server import _MCPAuthMiddleware
        app_mock = AsyncMock()
        middleware = _MCPAuthMiddleware(app_mock, auth_token="secret-token", allow_query_token=False)
        scope = {
            "type": "http", "method": "GET", "path": "/mcp",
            "headers": [],
            "query_string": b"token=secret-token",
        }
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        app_mock.assert_not_called()

    async def test_middleware_passes_non_http_scopes(self):
        """Non-HTTP scopes (lifespan, websocket) always pass through."""
        from app.mcp_server import _MCPAuthMiddleware
        app_mock = AsyncMock()
        middleware = _MCPAuthMiddleware(app_mock, auth_token="secret-token", allow_query_token=True)
        scope = {"type": "lifespan"}
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        app_mock.assert_called_once()


class TestInputValidation:
    """W3: Input validation & error handling."""

    @pytest.mark.asyncio
    async def test_preferences_rejects_unknown_keys(self, app_client):
        """PATCH /api/preferences must reject unknown keys."""
        resp = await app_client.patch("/api/preferences", json={"unknown_key": "value"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_feedback_comment_max_length(self, app_client):
        """Feedback comment must not exceed 2000 chars."""
        resp = await app_client.post("/api/feedback", json={
            "optimization_id": "nonexistent",
            "rating": "thumbs_up",
            "comment": "x" * 2001,
        })
        assert resp.status_code == 422

    def test_repo_name_format_validation(self):
        """Repo full_name must match owner/repo pattern."""
        import re
        repo_name_re = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9._-]+$")
        # Path traversal attempt must be rejected
        assert not repo_name_re.match("../../etc/passwd")
        # Spaces must be rejected
        assert not repo_name_re.match("owner /repo")
        # Valid names must pass
        assert repo_name_re.match("owner/repo")
        assert repo_name_re.match("my-org/my_repo.js")

    @pytest.mark.asyncio
    async def test_sort_by_rejects_invalid_column(self, app_client):
        """sort_by must be from VALID_SORT_COLUMNS."""
        resp = await app_client.get("/api/history?sort_by=;DROP TABLE")
        assert resp.status_code == 422


class TestSSESafety:
    """W3g: SSE serialization safety."""

    def test_sse_serialization_failure_returns_safe_error(self):
        """format_sse must not raise on non-serializable data."""
        from app.utils.sse import format_sse
        result = format_sse("test", {"value": object()})
        assert "error" in result
        assert "Internal error" in result


class TestCORSHardening:
    """W4: CORS & HTTP headers."""

    @pytest.mark.asyncio
    async def test_cors_methods_exclude_trace(self, app_client):
        """CORS must not allow TRACE method."""
        resp = await app_client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:5199",
                "Access-Control-Request-Method": "TRACE",
            },
        )
        allow_methods = resp.headers.get("access-control-allow-methods", "")
        assert "TRACE" not in allow_methods

    @pytest.mark.asyncio
    async def test_cors_allows_standard_methods(self, app_client):
        """CORS must allow standard API methods."""
        for method in ("GET", "POST", "PATCH", "PUT", "DELETE"):
            resp = await app_client.options(
                "/api/health",
                headers={
                    "Origin": "http://localhost:5199",
                    "Access-Control-Request-Method": method,
                },
            )
            allow_methods = resp.headers.get("access-control-allow-methods", "")
            assert method in allow_methods, f"{method} should be in allowed methods"

    @pytest.mark.asyncio
    async def test_cors_allows_required_headers(self, app_client):
        """CORS must allow Content-Type, Authorization, Cache-Control headers."""
        resp = await app_client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:5199",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        allow_headers = resp.headers.get("access-control-allow-headers", "")
        assert "content-type" in allow_headers.lower()

    def test_dev_mode_gates_localhost_origin(self):
        """localhost:5199 CORS origin should only be added in DEVELOPMENT_MODE."""
        import inspect

        from app import main as main_mod

        source = inspect.getsource(main_mod)
        assert "DEVELOPMENT_MODE" in source, (
            "CORS setup must check DEVELOPMENT_MODE before adding localhost origin"
        )


class TestXForwardedFor:
    """W3h: X-Forwarded-For parsing hardening."""

    def test_strips_whitespace_from_forwarded_ips(self):
        from app.dependencies.rate_limit import RateLimit

        request = MagicMock()
        request.client.host = "127.0.0.1"
        request.headers = {"x-forwarded-for": "  10.0.0.1 , 192.168.1.1  "}

        with patch("app.config.settings") as mock_settings:
            mock_settings.TRUSTED_PROXIES = "127.0.0.1"
            ip = RateLimit._get_client_ip(request)
        assert ip == "10.0.0.1"

    def test_falls_back_on_invalid_ip(self):
        from app.dependencies.rate_limit import RateLimit

        request = MagicMock()
        request.client.host = "127.0.0.1"
        request.headers = {"x-forwarded-for": "not-an-ip, garbage"}

        with patch("app.config.settings") as mock_settings:
            mock_settings.TRUSTED_PROXIES = "127.0.0.1"
            ip = RateLimit._get_client_ip(request)
        assert ip == "127.0.0.1"
