"""Security hardening tests — PR 3: audit logging."""

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
