"""v0.4.14 cycle 3 — router migration tests."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


class TestAuditLoggerSignatureRelaxation:
    """audit_logger.log_event accepts db=None when write_queue is supplied."""

    async def test_log_event_accepts_none_db_when_write_queue_supplied(
        self, writer_engine_inmem,
    ):
        from app.services.write_queue import WriteQueue
        wq = WriteQueue(writer_engine_inmem)
        await wq.start()
        try:
            from app.services.audit_logger import log_event
            # MUST NOT raise TypeError on db=None when write_queue is supplied
            await log_event(
                db=None,
                action="test_action",
                actor_ip="127.0.0.1",
                outcome="success",
                write_queue=wq,
            )
        finally:
            await wq.stop(drain_timeout=2.0)


class TestStrategiesAuditLogMigration:
    """routers/strategies.py:164 audit-log uses log_event with write_queue."""

    def test_strategies_strategy_updated_audit_uses_log_event_with_write_queue(self):
        import app.routers.strategies as _strat_mod
        src = Path(_strat_mod.__file__).read_text()
        # Migrated source MUST NOT have async with async_session_factory in
        # the audit-log block.
        assert "async with async_session_factory() as audit_db:" not in src, (
            "strategies.py:164 audit-log site still uses bare session factory"
        )
        # Migrated source MUST call log_event with write_queue=
        assert "log_event(" in src and "write_queue=" in src, (
            "strategies.py audit-log must thread write_queue= into log_event call"
        )
        assert '"strategy_updated"' in src, "action name preserved"


class TestProvidersApiKeySetAuditLog:
    def test_providers_api_key_set_audit_uses_log_event_with_write_queue(self):
        import app.routers.providers as _prov_mod
        src = Path(_prov_mod.__file__).read_text()
        # Find the api_key_set block
        idx = src.find('"api_key_set"')
        assert idx > 0, "api_key_set block not found"
        window = src[max(0, idx - 600):idx + 200]
        assert 'async with async_session_factory() as audit_db:' not in window, (
            "providers.py:124 api_key_set audit-log still uses bare session factory"
        )
        assert "write_queue=" in window, (
            "providers.py api_key_set audit-log must thread write_queue= into log_event"
        )


class TestProvidersApiKeyDeletedAuditLog:
    def test_providers_api_key_deleted_audit_uses_log_event_with_write_queue(self):
        import app.routers.providers as _prov_mod
        src = Path(_prov_mod.__file__).read_text()
        idx = src.find('"api_key_deleted"')
        assert idx > 0, "api_key_deleted block not found"
        window = src[max(0, idx - 600):idx + 200]
        assert 'async with async_session_factory() as audit_db:' not in window
        assert "write_queue=" in window


class TestGithubAuthRefreshTokenMigration:
    def test_refresh_token_if_expired_uses_submit(self):
        import app.routers.github_auth as _gh_mod
        src = Path(_gh_mod.__file__).read_text()
        idx = src.find("def _refresh_token_if_expired")
        assert idx > 0
        # Grab the function body window (post-migration body grew with the
        # write-queue closure, so widen the window to 4000 chars).
        window = src[idx:idx + 4000]
        assert "github_token_refresh" in window, (
            "_refresh_token_if_expired must use operation_label='github_token_refresh'"
        )
        assert "get_write_queue()" in window


class TestGithubAuthCallbackBatchMigration:
    def test_callback_uses_submit_batch_for_token_and_audit(self):
        import app.routers.github_auth as _gh_mod
        src = Path(_gh_mod.__file__).read_text()
        idx = src.find('@router.get("/auth/callback")')
        assert idx > 0
        window = src[idx:idx + 6000]
        assert "submit_batch(" in window, (
            "callback must use submit_batch for atomic token+audit write"
        )
        assert "github_oauth_callback" in window, (
            "callback must use operation_label='github_oauth_callback'"
        )
