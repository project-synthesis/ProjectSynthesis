"""Structured audit event logging for sensitive operations.

Writes to the AuditLog table. Auto-prunes entries older than
AUDIT_RETENTION_DAYS (default 90) via prune_audit_log().

v0.4.13 cycle 8: when ``write_queue`` is supplied, both ``log_event``
and ``prune_audit_log`` route their commit through the queue under
``operation_label='audit_log_event'`` / ``'audit_log_prune'`` so the
write serializes against every other backend writer. Legacy direct-
session writes survive in the ``write_queue is None`` branch for
backward-compat.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

if TYPE_CHECKING:
    from app.services.write_queue import WriteQueue

logger = logging.getLogger(__name__)


async def log_event(
    db: AsyncSession | None,
    action: str,
    actor_ip: str | None = None,
    actor_session: str | None = None,
    detail: dict | None = None,
    outcome: str = "success",
    *,
    write_queue: "WriteQueue | None" = None,
) -> None:
    """Write an audit log entry.

    When ``write_queue`` is supplied, the INSERT + commit route through
    ``write_queue.submit()`` under ``operation_label='audit_log_event'``;
    ``db`` is unused in that path and may be ``None``. Otherwise the
    legacy direct-session path requires a non-None ``db``.
    """
    if write_queue is not None:
        async def _do_log(write_db: AsyncSession) -> None:
            entry = AuditLog(
                action=action,
                actor_ip=actor_ip,
                actor_session=actor_session,
                detail=detail,
                outcome=outcome,
            )
            write_db.add(entry)
            await write_db.commit()

        await write_queue.submit(_do_log, operation_label="audit_log_event")
        logger.debug("Audit log: action=%s outcome=%s ip=%s", action, outcome, actor_ip)
        return

    # Legacy: write through ``db`` directly.
    if db is None:
        raise ValueError(
            "log_event: db must be non-None when write_queue is not supplied"
        )
    entry = AuditLog(
        action=action,
        actor_ip=actor_ip,
        actor_session=actor_session,
        detail=detail,
        outcome=outcome,
    )
    db.add(entry)
    await db.commit()
    logger.debug("Audit log: action=%s outcome=%s ip=%s", action, outcome, actor_ip)


async def prune_audit_log(
    db: AsyncSession | None,
    retention_days: int = 90,
    *,
    write_queue: "WriteQueue | None" = None,
) -> int:
    """Delete audit log entries older than retention_days.

    When ``write_queue`` is supplied, the DELETE + commit route through
    ``write_queue.submit()`` under ``operation_label='audit_log_prune'``;
    ``db`` is unused in that path and may be ``None``. Otherwise the
    legacy direct-session path requires a non-None ``db``.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    if write_queue is not None:
        async def _do_prune(write_db: AsyncSession) -> int:
            r = await write_db.execute(
                delete(AuditLog).where(AuditLog.timestamp < cutoff)
            )
            await write_db.commit()
            return int(r.rowcount or 0)  # type: ignore[attr-defined]

        deleted = await write_queue.submit(
            _do_prune, operation_label="audit_log_prune",
        )
        if deleted:
            logger.info(
                "Pruned %d audit log entries older than %d days",
                deleted, retention_days,
            )
        return deleted

    # Legacy: write through ``db`` directly.
    if db is None:
        raise ValueError(
            "prune_audit_log: db must be non-None when write_queue is not supplied"
        )
    result = await db.execute(
        delete(AuditLog).where(AuditLog.timestamp < cutoff)
    )
    await db.commit()
    deleted = result.rowcount  # type: ignore[attr-defined]
    if deleted:
        logger.info("Pruned %d audit log entries older than %d days", deleted, retention_days)
    return deleted
