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
