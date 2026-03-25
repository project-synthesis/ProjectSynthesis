"""Security hardening tests — PR 3: audit logging, instrumentation, rate limits."""

import inspect
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update


class TestAuditLogger:
    """W7c: Structured audit logging."""

    @pytest.mark.asyncio
    async def test_log_event_writes_to_db(self, db_session):
        from app.models import AuditLog
        from app.services.audit_logger import log_event

        await log_event(
            db=db_session,
            action="api_key_set",
            actor_ip="127.0.0.1",
            detail={"masked_key": "sk-...abcd"},
            outcome="success",
        )

        result = await db_session.execute(select(AuditLog))
        row = result.scalar_one()
        assert row.action == "api_key_set"
        assert row.actor_ip == "127.0.0.1"
        assert row.outcome == "success"

    @pytest.mark.asyncio
    async def test_prune_deletes_old_entries(self, db_session):
        from app.models import AuditLog
        from app.services.audit_logger import log_event, prune_audit_log

        await log_event(db=db_session, action="test", actor_ip="1.1.1.1", outcome="success")

        await db_session.execute(
            update(AuditLog).values(timestamp=datetime.now(timezone.utc) - timedelta(days=100))
        )
        await db_session.commit()

        deleted = await prune_audit_log(db=db_session, retention_days=90)
        assert deleted >= 1

        result = await db_session.execute(select(AuditLog))
        assert result.scalar_one_or_none() is None


class TestAuditInstrumentation:
    """W7c: Verify audit log_event is wired into sensitive operations."""

    def test_set_api_key_calls_log_event(self):
        """set_api_key endpoint contains audit log_event call with 'api_key_set'."""
        from app.routers import providers
        source = inspect.getsource(providers.set_api_key)
        assert 'action="api_key_set"' in source

    def test_delete_api_key_calls_log_event(self):
        """delete_api_key endpoint contains audit log_event call with 'api_key_deleted'."""
        from app.routers import providers
        source = inspect.getsource(providers.delete_api_key)
        assert 'action="api_key_deleted"' in source

    def test_github_login_calls_log_event(self):
        """github_callback endpoint contains audit log_event call with 'github_login'."""
        from app.routers import github_auth
        source = inspect.getsource(github_auth.github_callback)
        assert 'action="github_login"' in source

    def test_github_logout_calls_log_event(self):
        """github_logout endpoint contains audit log_event call with 'github_logout'."""
        from app.routers import github_auth
        source = inspect.getsource(github_auth.github_logout)
        assert 'action="github_logout"' in source

    def test_strategy_updated_calls_log_event(self):
        """update_strategy endpoint contains audit log_event call with 'strategy_updated'."""
        from app.routers import strategies
        source = inspect.getsource(strategies.update_strategy)
        assert 'action="strategy_updated"' in source

    def test_mcp_auth_failure_logs_warning(self):
        """MCP auth middleware logs auth failures via logger.warning."""
        from app.mcp_server import _MCPAuthMiddleware
        source = inspect.getsource(_MCPAuthMiddleware.__call__)
        assert "MCP auth failure" in source


class TestRateLimitCoverage:
    """W7d: Verify rate limits are wired to previously unprotected endpoints."""

    def test_health_endpoint_has_rate_limit(self):
        """GET /api/health must have RateLimit dependency."""
        from app.routers import health
        source = inspect.getsource(health.health_check)
        assert "RateLimit" in source

    def test_settings_endpoint_has_rate_limit(self):
        """GET /api/settings must have RateLimit dependency."""
        from app.routers import settings
        source = inspect.getsource(settings.get_settings)
        assert "RateLimit" in source

    def test_cluster_detail_has_rate_limit(self):
        """GET /api/clusters/{id} must have RateLimit dependency."""
        from app.routers import clusters
        source = inspect.getsource(clusters.get_cluster_detail)
        assert "RateLimit" in source

    def test_cluster_templates_has_rate_limit(self):
        """GET /api/clusters/templates must have RateLimit dependency."""
        from app.routers import clusters
        source = inspect.getsource(clusters.get_cluster_templates)
        assert "RateLimit" in source

    def test_strategies_list_has_rate_limit(self):
        """GET /api/strategies must have RateLimit dependency."""
        from app.routers import strategies
        source = inspect.getsource(strategies.list_strategies)
        assert "RateLimit" in source
